"""데이터 잡: 뉴스 수집·24h 실현 수익률·일일 리뷰 (04 §8, 06 §6.1)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from quantpilot.core.clock import to_local, to_utc
from quantpilot.core.models import Market, Side
from quantpilot.scheduler.context import JobContext

log = logging.getLogger(__name__)

HORIZON = timedelta(hours=24)


async def news_collect(ctx: JobContext) -> None:
    """매시 :05: 뉴스 수집·요약 (data.news.NewsCollector)."""
    if ctx.news is None:
        return
    items = await ctx.news.collect(ctx.utcnow())
    log.info("news collected", extra={"count": len(items)})


async def _close_at(ctx: JobContext, market: Market, symbol: str, ts: datetime) -> float | None:
    """ts(현지) 시점의 가격 = ts 직전에 마감된 1분봉 종가 (10분 안)."""
    bars = await ctx.candles.load(market, symbol, "1m", ts - timedelta(minutes=10), ts)
    return bars[-1].close if bars else None


async def fill_realized_24h(ctx: JobContext) -> None:
    """매시 :10: 24시간 지난 판단의 실현 수익률·방향 적중을 채운다. hold된 신호도 채운다 (06 §6.1).

    기준가 = 신호의 price_hint(없으면 판단 시각 종가), 24h 뒤 = 그 시각 종가. 봉이 없으면 다음 회차에 다시 본다.
    """
    if ctx.judgments is None or ctx.candles is None:
        return
    done = 0
    for r in await ctx.judgments.pending_realized(ctx.utcnow() - HORIZON):
        market, sym, ts = r["market"], r["symbol"], r["ts"]
        p0 = r["price_hint"] or await _close_at(ctx, market, sym, ts)
        p1 = await _close_at(ctx, market, sym, ts + HORIZON)
        if not p0 or p1 is None:
            continue
        ret = p1 / p0 - 1
        hit = ret > 0 if r["weight"] >= 0 else ret < 0
        await ctx.judgments.set_realized(r["id"], ret, hit)
        done += 1
    log.info("realized_ret_24h filled", extra={"count": done})


async def day_stats(ctx: JobContext, now_utc: datetime) -> dict[str, Any]:
    """오늘(KST 자정부터) 시장별 체결 통계."""
    stats: dict[str, Any] = {}
    kst_midnight = to_local(now_utc, Market.UPBIT).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for market in ctx.markets:
        since = to_local(to_utc(kst_midnight, Market.UPBIT), market)
        fills = await ctx.ledger.fills(market, since=since)
        buys = [f for f in fills if f.side == Side.BUY]
        sells = [f for f in fills if f.side == Side.SELL]
        stats[market.value] = {
            "fills": len(fills),
            "buy_notional": sum(f.qty * f.price for f in buys),
            "sell_notional": sum(f.qty * f.price for f in sells),
            "costs": sum(f.fee + f.tax for f in fills),
        }
    return stats


async def daily_review(ctx: JobContext) -> None:
    """20:30 KST: 오늘 통계 → 리뷰 모델(Claude, P1-08) 요약 → daily_reviews 저장·알림.

    리뷰 모델이 없으면 통계만 담은 요약을 저장한다.
    """
    if ctx.ledger is None or ctx.ops is None:
        return
    now = ctx.utcnow()
    stats = await day_stats(ctx, now)
    if ctx.reviewer is not None:
        summary, cost = await ctx.reviewer(stats)
    else:
        summary = "리뷰 모델 미연결 — 통계만 기록: " + ", ".join(
            f"{m} 체결 {s['fills']}건" for m, s in stats.items()
        )
        cost = None
    day = to_local(now, Market.UPBIT).date()
    await ctx.ops.save_daily_review(day, summary=summary, stats=stats, cost_usd=cost)
    await ctx.notifier.send("info", summary)
