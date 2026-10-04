"""DB에서 모은 데이터로 만드는 리포트 — `qp report ab`와 API(`DbCalibration`)가 같은 코드를 쓴다 (ADR 0030)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from quantpilot.core.models import Market


async def build_ab_report(
    sessions: Any, market: Market, *, weeks: int, now: datetime, initial_cash: float | None = None
) -> dict[str, Any]:
    """최근 weeks주 A/B 리포트 dict. 원장 곡선은 계좌 시작부터 재생하고 1시간 종가로 평가한다."""
    from quantpilot.config import settings
    from quantpilot.core.clock import to_local
    from quantpilot.db.repo import SqlCandleRepo, SqlJudgmentRepo, SqlLedger
    from quantpilot.judgment.ab import ab_report, book_stats

    end = to_local(now, market)
    start = end - timedelta(weeks=weeks)
    cash = initial_cash
    if cash is None:
        cash = settings.initial_cash_usd if market == Market.US else settings.initial_cash_krw
    judgments = await SqlJudgmentRepo(sessions).between(market, start, end)
    on = await SqlLedger(sessions).fills(market)
    off = await SqlLedger(sessions, shadow=True).fills(market)
    first = min((f.ts for f in on + off), default=start)
    candles = SqlCandleRepo(sessions)
    prices = {}
    for sym in sorted({f.symbol for f in on + off}):
        bars = await candles.load(market, sym, "1m", min(first, start), end)
        if bars:
            s = pd.Series([b.close for b in bars], index=pd.DatetimeIndex([b.ts for b in bars]))
            prices[sym] = s.resample("1h").last().dropna()
    return ab_report(
        judgments,
        book_stats(on, prices, cash, start, end),
        book_stats(off, prices, cash, start, end),
        start=start,
        end=end,
        signals=len(judgments),
    )
