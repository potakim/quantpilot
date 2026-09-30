"""상태 잡: 엔진 하트비트 감시(백업 모드)·평가액 스냅샷·월 롤 (04 §8)."""

from __future__ import annotations

import logging

from quantpilot.core.clock import to_local
from quantpilot.core.models import Market
from quantpilot.scheduler.context import JobContext
from quantpilot.scheduler.jobs.exits import TIME_EXIT_STRATEGIES, engine_alive, run_backup_exit

log = logging.getLogger(__name__)


async def engine_heartbeat(ctx: JobContext) -> None:
    """30초마다: 하트비트가 90초 넘게 없으면 알림 + 백업 모드. 백업 모드면 밀린 청산 명령을 직접 처리.

    알림은 상태가 바뀔 때만 보낸다(끊김 1회, 복구 1회).
    """
    for market in ctx.markets:
        alive = await engine_alive(ctx, market)
        if not alive and market not in ctx.backup_armed:
            ctx.backup_armed.add(market)
            await ctx.notifier.send(
                "critical", f"engine heartbeat lost ({market.value}) — backup exit armed"
            )
        elif alive and market in ctx.backup_armed:
            ctx.backup_armed.discard(market)
            await ctx.notifier.send("info", f"engine heartbeat recovered ({market.value})")
        if alive:
            continue
        # 엔진이 받아 놓고 처리하지 못한 시간 청산 명령
        for strategy in TIME_EXIT_STRATEGIES.get(market, ()):
            cmd_id = await ctx.link.pending_time_exit(market, strategy)
            if cmd_id is not None:
                await run_backup_exit(ctx, market, strategy, cmd_id)


async def equity_snapshot(ctx: JobContext) -> None:
    """매분: 시장별 (현금, 평가액)을 equity_snapshots에 쓴다."""
    if ctx.account is None or ctx.ops is None:
        return
    for market in ctx.markets:
        acct = await ctx.account(market)
        if acct is None:
            continue
        cash, equity = acct
        ts = to_local(ctx.utcnow(), market).replace(second=0, microsecond=0)
        await ctx.ops.add_equity_snapshot(market=market, ts=ts, cash=cash, equity=equity)


def month_start_key(market: Market) -> str:
    """settings에 월초 평가액을 두는 키."""
    return f"month_start_equity.{Market(market).value}"


async def month_roll(ctx: JobContext) -> None:
    """매월 1일 UTC 00:00: 월초 평가액을 settings에 저장한다 (롤 자체는 RiskManager, 04 §5)."""
    if ctx.account is None or ctx.config is None:
        return
    month = ctx.utcnow().strftime("%Y-%m")
    for market in ctx.markets:
        acct = await ctx.account(market)
        if acct is None:
            continue
        await ctx.config.set_setting(month_start_key(market), {"month": month, "equity": acct[1]})
        log.info("month_start_equity 저장", extra={"market": market.value, "month": month})
