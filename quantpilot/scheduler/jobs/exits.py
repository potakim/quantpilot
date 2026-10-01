"""시간 청산 잡 (04 §8, 05 §5).

정상: engine에 명령을 넣으면 engine 타이머가 TickRunner.on_time_exit를 부르고 ack한다.
엔진 하트비트가 끊겼으면(백업 모드): scheduler가 DB 계좌로 만든 TickRunner의 on_time_exit를 직접 부른다.
어느 쪽이든 주문은 RiskManager.check → broker 순서로만 나간다 (불변식 #9). 청산은 할트와 무관하게 허용(#6).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from quantpilot.core.clock import MarketClock, to_local
from quantpilot.core.models import Market
from quantpilot.scheduler.context import JobContext, run_hook
from quantpilot.strategies import create
from quantpilot.strategies.base import Strategy

log = logging.getLogger(__name__)

# 시장별로 시간 청산을 받는 전략
TIME_EXIT_STRATEGIES: dict[Market, tuple[str, ...]] = {
    Market.UPBIT: ("vol_breakout",),
    Market.US: ("orb",),
}


async def engine_alive(ctx: JobContext, market: Market) -> bool:
    """마지막 하트비트가 stale_after 안인가."""
    last = await ctx.link.last_beat(market)
    return last is not None and ctx.utcnow() - last <= ctx.stale_after


async def run_backup_exit(ctx: JobContext, market: Market, strategy: str, cmd_id: str) -> None:
    """scheduler 프로세스에서 직접 시간 청산하고 명령을 ack한다."""
    runner = await ctx.backup(market, strategy)
    await runner.on_time_exit(strategy)
    await ctx.link.ack_time_exit(market, strategy, cmd_id)
    await ctx.notifier.send("warning", f"backup time exit done: {Market(market).value}/{strategy}")


async def time_exit(ctx: JobContext, market: Market, strategy: str) -> None:
    """시간 청산 요청. 엔진이 살아 있으면 명령만, 아니면 백업으로 직접 청산."""
    cmd_id = await ctx.link.request_time_exit(market, strategy)
    if await engine_alive(ctx, market):
        log.info("time_exit 명령 전달", extra={"strategy": strategy, "cmd": cmd_id})
        return
    log.warning("엔진 하트비트 없음 → 백업 청산", extra={"strategy": strategy, "cmd": cmd_id})
    await run_backup_exit(ctx, market, strategy, cmd_id)


def breakout_target(sym: str, bars: Sequence[Any], k: float) -> dict[str, Any] | None:
    """1분봉(전일 09:00~당일 09:00)으로 목표가 1건. 봉이 없으면 None (순수 함수).

    목표가 = 오늘 시가 + (전일 고가 − 전일 저가) × K (05 §1). 오늘 시가는 09:00 직전 마지막 종가로 근사한다.
    """
    if not bars:
        return None
    rng = max(b.high for b in bars) - min(b.low for b in bars)
    open_ref = bars[-1].close
    return {"symbol": sym, "open": open_ref, "range": rng, "target": open_ref + rng * k}


async def breakout_targets(
    candles: Any, today9: datetime
) -> tuple[Strategy, float, list[dict[str, Any]]]:
    """today9(현지 09:00) 기준 vol_breakout 심볼별 목표가. 봉 없는 심볼은 뺀다 (읽기 전용)."""
    strat = create("vol_breakout")
    k = float(strat.params["k"])
    targets = []
    for sym in strat.symbols:
        bars = await candles.load(Market.UPBIT, sym, "1m", today9 - timedelta(days=1), today9)
        t = breakout_target(sym, bars, k)
        if t is not None:
            targets.append(t)
    return strat, k, targets


async def publish_breakout_status(ctx: JobContext) -> None:
    """vol_breakout 오늘 목표가를 계산해 `strategy.status`로 발행한다.

    목표가 = 오늘 시가 + (전일 고가 − 전일 저가) × K (05 §1). 전일 = 어제 09:00~오늘 09:00 1분봉,
    오늘 시가는 09:00 직전 마지막 종가로 근사한다. 봉이 없는 심볼은 뺀다. 노이즈 K는 엔진 on_bar 몫.
    """
    if ctx.candles is None:
        return
    today9 = to_local(ctx.utcnow(), Market.UPBIT).replace(hour=9, minute=0, second=0, microsecond=0)
    strat, k, targets = await breakout_targets(ctx.candles, today9)
    await ctx.bus.publish(
        "strategy.status",
        {
            "strategy": strat.name,
            "market": Market.UPBIT.value,
            "ts": today9,
            "k": k,
            "targets": targets,
        },
    )


async def upbit_daily_exit(ctx: JobContext) -> None:
    """09:00 KST: 변동성 돌파 보유분 청산 → 목표가 재계산 → strategy.status 발행."""
    if Market.UPBIT not in ctx.markets:
        return
    await time_exit(ctx, Market.UPBIT, "vol_breakout")
    await publish_breakout_status(ctx)


def minutes_to_close(ctx: JobContext, market: Market) -> float | None:
    """오늘 세션 폐장까지 남은 분. 휴장이면 None."""
    clock = MarketClock(market, utcnow=ctx.utcnow)
    now = clock.now()
    bounds = clock.session_bounds(now.date())
    if bounds is None:
        return None
    return (bounds[1] - now).total_seconds() / 60


def _five_before_close(ctx: JobContext, market: Market) -> bool:
    """폐장 5분 전(±1분)인가. 정규 폐장·조기 폐장 두 시각에 등록된 잡이 맞는 쪽에서만 돈다."""
    left = minutes_to_close(ctx, market)
    return left is not None and 4 <= left <= 6


async def us_eod_exit(ctx: JobContext) -> None:
    """미국장 폐장 5분 전: ORB 청산 (조기 폐장일은 12:55 ET)."""
    if Market.US not in ctx.markets or not _five_before_close(ctx, Market.US):
        return
    await time_exit(ctx, Market.US, "orb")


async def gem_rebalance(ctx: JobContext) -> None:
    """월 마지막 거래일 미국장 폐장 5분 전: GEM 리밸런싱 (본문은 hook, 2단계)."""
    if Market.US not in ctx.markets or not _five_before_close(ctx, Market.US):
        return
    clock = MarketClock(Market.US, utcnow=ctx.utcnow)
    if clock.is_last_session_of_month(clock.now()):
        await run_hook(ctx, "gem_rebalance")
