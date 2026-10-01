"""화면 지표 계산 (ADR 0020): 오늘 손익·자산 곡선·전략별 손익·오늘 일정.

라우트는 DB에서 읽기만 하고, 계산은 여기 함수가 한다. 시각은 시장 현지 tz-naive로 계산하고,
응답으로 낼 때만 core/clock으로 UTC ISO로 바꾼다 (ADR 0009).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pandas as pd

from quantpilot.api import queries
from quantpilot.core import clock
from quantpilot.core.models import Fill, Market

log = logging.getLogger(__name__)

EQUITY_DAYS = (30, 90, 365)
# 자산 곡선 묶음 단위: 30일은 1시간, 그 이상은 하루 (ADR 0020 §2)
BUCKET = {30: "1h", 90: "1D", 365: "1D"}
BENCHMARK_SYMBOL = {Market.UPBIT: "KRW-BTC"}
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
# 잡 표의 timezone 문자열 → 같은 시간대를 쓰는 시장 (변환은 core/clock만 한다)
_TZ_MARKET = {"Asia/Seoul": Market.UPBIT, "America/New_York": Market.US}


def day_start(now_utc: datetime, market: Market) -> datetime:
    """시장 현지 오늘 0시 (tz-naive)."""
    return clock.to_local(now_utc, market).replace(hour=0, minute=0, second=0, microsecond=0)


def today_pnl(equity: float, snapshots: Sequence[Mapping[str, Any]]) -> dict[str, float] | None:
    """오늘 손익: 지금 평가액 − 오늘 첫 스냅샷. 스냅샷이 없거나 기준이 0이면 None."""
    if not snapshots:
        return None
    base = float(snapshots[0]["equity"])
    if base <= 0:
        return None
    amount = float(equity) - base
    return {"amount": amount, "pct": amount / base}


def downsample(snapshots: Sequence[Mapping[str, Any]], days: int) -> pd.Series:
    """스냅샷 → 묶음(1시간/하루)마다 마지막 값. 인덱스는 실제 마지막 스냅샷 시각(현지)."""
    if not snapshots:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(r["equity"]) for r in snapshots],
        index=pd.DatetimeIndex([r["ts"] for r in snapshots]),
    ).sort_index()
    keys = s.index.floor(BUCKET[days])
    return s[~keys.duplicated(keep="last")]


def benchmark(points: pd.Series, closes: pd.Series) -> list[float] | None:
    """같은 시작 자본으로 보유만 했을 때: v₀ × close_t ÷ close₀ (각 점 직전 종가). 못 맞추면 None."""
    if points.empty or closes.empty:
        return None
    closes = closes.sort_index()
    aligned = closes.reindex(closes.index.union(points.index)).ffill().reindex(points.index)
    c0 = aligned.iloc[0]
    if pd.isna(c0) or c0 <= 0:
        return None
    v0 = float(points.iloc[0])
    return [v0 * float(c) / float(c0) for c in aligned.ffill()]


def series_points(s: pd.Series, market: Market) -> list[dict[str, Any]]:
    """현지 시각 시리즈 → `[{ts(UTC ISO), v}]`."""
    return [
        {"ts": queries.iso(clock.to_utc(ts.to_pydatetime(), market)), "v": float(v)}
        for ts, v in s.items()
    ]


def hourly_closes(bars: Sequence[Any]) -> pd.Series:
    """1분봉 → 1시간 마지막 종가 (현지 시각)."""
    if not bars:
        return pd.Series(dtype=float)
    s = pd.Series([b.close for b in bars], index=pd.DatetimeIndex([b.ts for b in bars]))
    return s.resample("1h").last().dropna()


def strategy_stats(
    fills: Sequence[Fill],
    prices: Mapping[str, pd.Series],
    capital: float,
    *,
    month_start: datetime,
    now: datetime,
) -> dict[str, float | None]:
    """전략 하나의 `{month_pnl, mdd_30d}` (ADR 0020 §3). 구간 체결·시세·자본이 없으면 그 값은 None."""
    from quantpilot.judgment.ab import book_stats

    out: dict[str, float | None] = {"month_pnl": None, "mdd_30d": None}
    syms = {f.symbol for f in fills}
    if capital <= 0 or not fills or not any(sym in prices for sym in syms):
        return out
    own = {sym: s for sym, s in prices.items() if sym in syms}
    start30 = now - timedelta(days=30)
    if any(month_start <= f.ts <= now for f in fills):
        out["month_pnl"] = book_stats(fills, own, capital, month_start, now).ret
    if any(start30 <= f.ts <= now for f in fills):
        out["mdd_30d"] = book_stats(fills, own, capital, start30, now).mdd
    return out


def _in_range(spec: str, value: int) -> bool:
    """cron 필드 값('mon-fri', '1', '1,15')에 value가 드는가."""

    def num(tok: str) -> int:
        return _DOW[tok] if tok in _DOW else int(tok)

    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        if num(lo) <= value <= num(hi or lo):
            return True
    return False


def cron_fires(fields: Mapping[str, Any], start_utc: datetime, end_utc: datetime) -> list[datetime]:
    """hour가 정해진 cron 필드의 [start, end) 실행 시각(UTC aware). 매시·매분 잡은 빈 목록."""
    if "hour" not in fields:
        return []
    tz = str(fields.get("timezone", "UTC"))
    market = _TZ_MARKET.get(tz)
    if market is None and tz != "UTC":
        log.warning("schedule: 모르는 timezone", extra={"timezone": tz})
        return []

    def local(ts: datetime) -> datetime:
        return clock.to_local(ts, market) if market else ts.astimezone(UTC).replace(tzinfo=None)

    def utc(ts: datetime) -> datetime:
        return clock.to_utc(ts, market) if market else ts.replace(tzinfo=UTC)

    out = []
    day: date = local(start_utc).date()
    last: date = local(end_utc).date()
    while day <= last:
        ok = True
        if "day_of_week" in fields:
            ok = _in_range(str(fields["day_of_week"]), day.weekday())
        if ok and "day" in fields:
            ok = _in_range(str(fields["day"]), day.day)
        if ok:
            hms = time(
                int(fields["hour"]), int(fields.get("minute", 0)), int(fields.get("second", 0))
            )
            at = datetime.combine(day, hms)  # 잡 시간대의 현지 tz-naive
            at_utc = utc(at)
            if start_utc <= at_utc < end_utc:
                out.append(at_utc)
        day += timedelta(days=1)
    return out
