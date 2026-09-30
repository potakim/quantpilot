"""체결 → 봉 집계 (04 §6, 01 §4.1).

봉 하나가 확정되는 시점은 둘 중 빠른 쪽이다.
1. 다음 봉 구간의 첫 체결이 들어왔을 때 (`on_trade`)
2. 봉 마감 + `grace`(기본 2초)가 지났을 때 (`on_timer`, 엔진이 주기적으로 부른다)

- 시각은 전부 시장 현지 tz-naive. `BarClosed.ts`는 봉 시작 시각
- 체결이 없는 구간은 봉을 만들지 않는다 (빈 봉을 지어내지 않는다)
- 이미 확정된 봉 구간에 늦게 온 체결은 버린다 (확정된 봉은 바꾸지 않는다)
- 일봉 경계는 시장별 (업비트 09:00 KST — 업비트 REST 일봉과 같은 경계)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from quantpilot.core.events import BarClosed, TradeEvent
from quantpilot.core.models import Market

log = logging.getLogger(__name__)

GRACE = timedelta(seconds=2)
# 일봉 이상 경계. 없으면 자정
DAY_START: dict[Market, time] = {Market.UPBIT: time(9)}
_TF = re.compile(r"^(\d+)([mhd])$")
_UNIT = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def parse_timeframe(tf: str) -> timedelta:
    """'1m'·'5m'·'1h'·'1d' → 길이. 모르는 형식이면 ValueError."""
    m = _TF.match(tf)
    if m is None or int(m.group(1)) <= 0:
        raise ValueError(f"지원하지 않는 봉 주기: {tf!r}")
    span = int(m.group(1)) * _UNIT[m.group(2)]
    # 봉 경계를 하루 시작점 기준으로 자르므로 하루를 나누어떨어지게 하는 주기만 받는다
    if span > timedelta(days=1) or timedelta(days=1) % span:
        raise ValueError(f"하루를 나누어떨어지게 하지 않는 봉 주기: {tf!r}")
    return span


@dataclass
class _Building:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandleAggregator:
    """한 시장·한 주기의 체결 집계기. 심볼마다 진행 중인 봉 1개를 들고 있다."""

    def __init__(self, tf: str, market: Market, *, grace: timedelta = GRACE) -> None:
        self.tf = tf
        self.market = Market(market)
        self.span = parse_timeframe(tf)
        self.grace = grace
        start = DAY_START.get(self.market, time(0))
        self._origin_offset = timedelta(hours=start.hour, minutes=start.minute)
        self._bars: dict[str, _Building] = {}
        self._closed_until: dict[str, datetime] = {}  # 심볼별 마지막 확정 봉의 끝

    def bucket_start(self, ts: datetime) -> datetime:
        """ts가 속한 봉의 시작 시각."""
        day0 = datetime.combine(ts.date(), time(0)) + self._origin_offset
        if ts < day0:
            day0 -= timedelta(days=1)
        n = (ts - day0) // self.span
        return day0 + n * self.span

    def on_trade(self, ev: TradeEvent) -> BarClosed | None:
        """체결 1건 반영. 새 구간의 첫 체결이면 직전 봉을 확정해 돌려준다."""
        if ev.market != self.market:
            raise ValueError(f"{self.market.value} 집계기에 {ev.market} 체결")
        start = self.bucket_start(ev.ts)
        closed_until = self._closed_until.get(ev.symbol)
        if closed_until is not None and start < closed_until:
            log.warning(
                "확정된 봉 구간의 늦은 체결 버림",
                extra={"symbol": ev.symbol, "ts": ev.ts.isoformat(), "tf": self.tf},
            )
            return None
        cur = self._bars.get(ev.symbol)
        done: BarClosed | None = None
        if cur is not None and start > cur.start:
            done = self._close(ev.symbol)
            cur = None
        if cur is None:
            self._bars[ev.symbol] = _Building(start, ev.price, ev.price, ev.price, ev.price, ev.qty)
        else:
            cur.high = max(cur.high, ev.price)
            cur.low = min(cur.low, ev.price)
            cur.close = ev.price
            cur.volume += ev.qty
        return done

    def on_timer(self, now: datetime) -> list[BarClosed]:
        """now(현지 tz-naive) 기준으로 마감+grace가 지난 봉을 모두 확정한다."""
        due = [s for s, b in self._bars.items() if now >= b.start + self.span + self.grace]
        return [self._close(s) for s in due]

    def pending(self, symbol: str) -> BarClosed | None:
        """아직 확정되지 않은 진행 중 봉 (조회용, 상태를 바꾸지 않는다)."""
        b = self._bars.get(symbol)
        return None if b is None else self._to_event(symbol, b)

    def _close(self, symbol: str) -> BarClosed:
        b = self._bars.pop(symbol)
        self._closed_until[symbol] = b.start + self.span
        return self._to_event(symbol, b)

    def _to_event(self, symbol: str, b: _Building) -> BarClosed:
        return BarClosed(
            market=self.market,
            symbol=symbol,
            timeframe=self.tf,
            ts=b.start,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
