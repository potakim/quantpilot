"""exchange_calendars 어댑터 (선택, ADR 0009). 내장 표 범위를 넘는 백테스트에서만 주입한다.

    clock = MarketClock(Market.KRX, calendar=ExchangeCalendar(Market.KRX, "2010-01-01", "2030-12-31"))

`python -m quantpilot.data.calendars`는 core/calendars.py 내장 표의 원본을 출력한다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

from quantpilot.core import calendars as builtin
from quantpilot.core.clock import TIMEZONES
from quantpilot.core.errors import CalendarOutOfRange
from quantpilot.core.models import Market

try:
    import exchange_calendars as xcals
except ImportError:  # pragma: no cover - 선택 의존성 (infra extra)
    xcals = None

CODES: dict[Market, str] = {Market.KRX: "XKRX", Market.US: "XNYS"}


class ExchangeCalendar:
    """exchange_calendars 기반 SessionCalendar. 시각은 시장 현지 tz-naive로 돌려준다."""

    def __init__(self, market: Market, start: str | date, end: str | date) -> None:
        if xcals is None:
            raise ImportError("exchange_calendars가 없다: uv pip install -e '.[infra]'")
        self.market = Market(market)
        self._start, self._end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
        self._cal = xcals.get_calendar(CODES[self.market], start=str(start), end=str(end))
        self._first = self._cal.first_session.date()
        self._last = self._cal.last_session.date()
        self._tz = TIMEZONES[self.market]

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """day의 (개장, 폐장). 휴장이면 None, 요청 범위 밖이면 CalendarOutOfRange."""
        if not self._start <= day <= self._end:
            raise CalendarOutOfRange(day, self._start, self._end)
        # 범위 끝이 휴장일이면 라이브러리는 첫·마지막 세션 밖을 범위 밖으로 본다
        if not self._first <= day <= self._last or not self._cal.is_session(day.isoformat()):
            return None
        return (
            self._local(self._cal.session_open(day.isoformat())),
            self._local(self._cal.session_close(day.isoformat())),
        )

    def _local(self, ts: object) -> datetime:
        return ts.tz_convert(self._tz).tz_localize(None).to_pydatetime()  # type: ignore[attr-defined]


def dump(market: Market, start: date = builtin.FIRST_DAY, end: date = builtin.LAST_DAY) -> str:
    """내장 표 형식(연도 → 'MMDD ...')의 평일 휴장일과 특수 세션을 문자열로."""
    cal = ExchangeCalendar(market, start, end)
    reg = (builtin.KRX_CALENDAR if market == Market.KRX else builtin.NYSE_CALENDAR).regular
    lines: list[str] = [f"# {CODES[Market(market)]} {start} ~ {end}"]
    holidays: dict[int, list[str]] = {}
    special: list[str] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            got = cal.session(day)
            if got is None:
                holidays.setdefault(day.year, []).append(day.strftime("%m%d"))
            elif (got[0].time(), got[1].time()) != reg:
                special.append(f"{day} {got[0]:%H:%M}-{got[1]:%H:%M}")
        day += timedelta(days=1)
    lines += [f"{y}: {' '.join(v)}" for y, v in sorted(holidays.items())]
    lines += ["# special", *special]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    for m in (Market.KRX, Market.US):
        sys.stdout.write(dump(m) + "\n\n")
