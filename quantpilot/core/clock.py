"""시장 시계. 현지시간(tz-naive) ↔ UTC 변환은 이 모듈에서만 한다 (CLAUDE.md, ADR 0009).

규칙: 엔진 안의 시각은 **각 시장의 현지시간 tz-naive**, DB는 UTC(timestamptz).
tz-aware 값이 들어오면 먼저 해당 시장 현지시간으로 바꿔서 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from quantpilot.core.calendars import (
    KRX_CALENDAR,
    NYSE_CALENDAR,
    UPBIT_CALENDAR,
    SessionCalendar,
)
from quantpilot.core.models import Market

TIMEZONES: dict[Market, ZoneInfo] = {
    Market.UPBIT: ZoneInfo("Asia/Seoul"),
    Market.KRX: ZoneInfo("Asia/Seoul"),
    Market.US: ZoneInfo("America/New_York"),
}
DEFAULT_CALENDARS: dict[Market, SessionCalendar] = {
    Market.UPBIT: UPBIT_CALENDAR,
    Market.KRX: KRX_CALENDAR,
    Market.US: NYSE_CALENDAR,
}
# 연휴가 아무리 길어도 이 안에는 다음 세션이 있다 (추석+주말+임시공휴일 ≈ 10일)
_SEARCH_DAYS = 31


def to_utc(ts_local: datetime, market: Market) -> datetime:
    """시장 현지 tz-naive 시각 → UTC tz-aware. aware 값은 그대로 UTC로 바꾼다."""
    if ts_local.tzinfo is None:
        ts_local = ts_local.replace(tzinfo=TIMEZONES[Market(market)])
    return ts_local.astimezone(UTC)


def to_local(ts_utc: datetime, market: Market) -> datetime:
    """UTC 시각 → 시장 현지 tz-naive. tz-naive 입력은 UTC로 간주한다."""
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=UTC)
    return ts_utc.astimezone(TIMEZONES[Market(market)]).replace(tzinfo=None)


class MarketClock:
    """시장 하나의 세션 시계 (04 §1). 모든 입출력 시각은 현지 tz-naive."""

    def __init__(
        self,
        market: Market,
        *,
        calendar: SessionCalendar | None = None,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.market = Market(market)
        self.tz = TIMEZONES[self.market]
        self._calendar = calendar or DEFAULT_CALENDARS[self.market]
        self._utcnow = utcnow or (lambda: datetime.now(UTC))

    # ── 변환 ─────────────────────────────────────────────
    def now(self) -> datetime:
        """현재 현지 시각 (tz-naive)."""
        return self.to_local(self._utcnow())

    def to_local(self, ts_utc: datetime) -> datetime:
        """UTC → 이 시장 현지 tz-naive."""
        return to_local(ts_utc, self.market)

    def to_utc(self, ts_local: datetime) -> datetime:
        """이 시장 현지 tz-naive → UTC tz-aware."""
        return to_utc(ts_local, self.market)

    def _local(self, ts: datetime | None) -> datetime:
        if ts is None:
            return self.now()
        return ts if ts.tzinfo is None else self.to_local(ts)

    # ── 세션 ─────────────────────────────────────────────
    def session_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        """day에 시작하는 세션의 (개장, 폐장). 휴장이면 None."""
        return self._calendar.session(day)

    def is_session(self, day: date) -> bool:
        """day가 거래일인가."""
        return self.session_bounds(day) is not None

    def is_open(self, ts: datetime | None = None) -> bool:
        """ts(기본: 지금)에 장이 열려 있는가. 개장 시각 포함, 폐장 시각 제외."""
        t = self._local(ts)
        # 업비트 세션은 자정을 넘으므로 전날 세션도 본다
        for day in (t.date() - timedelta(days=1), t.date()):
            bounds = self.session_bounds(day)
            if bounds is not None and bounds[0] <= t < bounds[1]:
                return True
        return False

    def _sessions_from(self, day: date) -> Iterator[tuple[datetime, datetime]]:
        for i in range(_SEARCH_DAYS + 1):
            bounds = self.session_bounds(day + timedelta(days=i))
            if bounds is not None:
                yield bounds

    def next_open(self, after: datetime | None = None) -> datetime:
        """after(기본: 지금) 이후 첫 개장 시각. 정확히 개장 시각이면 그다음 개장."""
        t = self._local(after)
        for open_, _ in self._sessions_from(t.date()):
            if open_ > t:
                return open_
        raise RuntimeError(f"{self.market.value}: {t} 이후 {_SEARCH_DAYS}일 안에 개장이 없다")

    def next_close(self, after: datetime | None = None) -> datetime:
        """after(기본: 지금) 이후 첫 폐장 시각."""
        t = self._local(after)
        for _, close in self._sessions_from(t.date() - timedelta(days=1)):
            if close > t:
                return close
        raise RuntimeError(f"{self.market.value}: {t} 이후 {_SEARCH_DAYS}일 안에 폐장이 없다")

    def is_last_session_of_month(self, ts: datetime | date) -> bool:
        """ts의 날짜가 그 달의 마지막 거래일인가 (월간 전략 호출 판단, 04 §2)."""
        day = self._local(ts).date() if isinstance(ts, datetime) else ts
        if not self.is_session(day):
            return False
        nxt = day + timedelta(days=1)
        while nxt.month == day.month:
            if self.is_session(nxt):
                return False
            nxt += timedelta(days=1)
        return True
