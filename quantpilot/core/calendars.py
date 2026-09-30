"""시장 세션 캘린더 내장 표 (ADR 0009). 표준 라이브러리만 쓴다.

표 원본은 exchange_calendars 4.13.2 (`python -m quantpilot.data.calendars`로 다시 뽑는다).
라이브러리에 빠진 임시 공휴일은 `*_MANUAL`에 출처와 함께 따로 둔다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Protocol

from quantpilot.core.errors import CalendarOutOfRange

FIRST_DAY = date(2020, 1, 1)
LAST_DAY = date(2027, 12, 31)

# 평일 휴장일 (연도 → "MMDD" 목록). 주말은 표에 넣지 않는다.
KRX_HOLIDAYS: dict[int, str] = {
    2020: "0101 0124 0127 0415 0430 0501 0505 0817 0930 1001 1002 1009 1225 1231",
    2021: "0101 0211 0212 0301 0505 0519 0816 0920 0921 0922 1004 1011 1231",
    2022: "0131 0201 0202 0301 0309 0505 0601 0606 0815 0909 0912 1003 1010 1230",
    2023: "0123 0124 0301 0501 0505 0529 0606 0815 0928 0929 1002 1003 1009 1225 1229",
    2024: "0101 0209 0212 0301 0410 0501 0506 0515 0606 0815 0916 0917 0918 1001 1003 1009 "
    "1225 1231",
    2025: "0101 0127 0128 0129 0130 0303 0501 0505 0506 0603 0606 0815 1003 1006 1007 1008 "
    "1009 1225 1231",
    2026: "0101 0216 0217 0218 0302 0501 0505 0525 0603 0817 0924 0925 1005 1009 1225 1231",
    2027: "0101 0208 0209 0301 0505 0513 0816 0914 0915 0916 1004 1011 1227 1231",
}
# exchange_calendars 4.13.2에 없는 날.
# 2026-06-03: 제9회 전국동시지방선거일 (관공서의 공휴일에 관한 규정 §2 — 임기만료 선거일)
KRX_MANUAL: frozenset[date] = frozenset({date(2026, 6, 3)})

NYSE_HOLIDAYS: dict[int, str] = {
    2020: "0101 0120 0217 0410 0525 0703 0907 1126 1225",
    2021: "0101 0118 0215 0402 0531 0705 0906 1125 1224",
    2022: "0117 0221 0415 0530 0620 0704 0905 1124 1226",
    2023: "0102 0116 0220 0407 0529 0619 0704 0904 1123 1225",
    2024: "0101 0115 0219 0329 0527 0619 0704 0902 1128 1225",
    2025: "0101 0109 0120 0217 0418 0526 0619 0704 0901 1127 1225",
    2026: "0101 0119 0216 0403 0525 0619 0703 0907 1126 1225",
    2027: "0101 0118 0215 0326 0531 0618 0705 0906 1125 1224",
}
NYSE_MANUAL: frozenset[date] = frozenset()

# 특수 세션 (현지 개장·폐장). KRX: 새해 첫 거래일 10:00 개장, 2020 수능일. NYSE: 13:00 조기 폐장.
KRX_SPECIAL: dict[date, tuple[time, time]] = {
    d: (time(10), time(15, 30))
    for d in (
        date(2020, 1, 2),
        date(2021, 1, 4),
        date(2022, 1, 3),
        date(2023, 1, 2),
        date(2024, 1, 2),
        date(2025, 1, 2),
        date(2026, 1, 2),
        date(2027, 1, 4),
    )
} | {date(2020, 12, 3): (time(10), time(16, 30))}

NYSE_SPECIAL: dict[date, tuple[time, time]] = {
    d: (time(9, 30), time(13))
    for d in (
        date(2020, 11, 27),
        date(2020, 12, 24),
        date(2021, 11, 26),
        date(2022, 11, 25),
        date(2023, 7, 3),
        date(2023, 11, 24),
        date(2024, 7, 3),
        date(2024, 11, 29),
        date(2024, 12, 24),
        date(2025, 7, 3),
        date(2025, 11, 28),
        date(2025, 12, 24),
        date(2026, 11, 27),
        date(2026, 12, 24),
        date(2027, 11, 26),
    )
}


class SessionCalendar(Protocol):
    """하루의 세션 경계(현지 tz-naive)를 돌려주는 캘린더. 휴장이면 None."""

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """day에 시작하는 세션의 (개장, 폐장). 휴장이면 None."""
        ...


def _parse(table: dict[int, str]) -> frozenset[date]:
    return frozenset(
        date(y, int(md[:2]), int(md[2:])) for y, days in table.items() for md in days.split()
    )


def _check_range(day: date) -> None:
    if not FIRST_DAY <= day <= LAST_DAY:
        raise CalendarOutOfRange(day, FIRST_DAY, LAST_DAY)


class TableCalendar:
    """평일 정규장 + 내장 휴장일·특수 세션 표. 표 범위 밖은 CalendarOutOfRange."""

    def __init__(
        self,
        regular: tuple[time, time],
        holidays: frozenset[date],
        special: dict[date, tuple[time, time]],
    ) -> None:
        self.regular, self._holidays, self._special = regular, holidays, special

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """day의 (개장, 폐장). 주말·휴장이면 None."""
        _check_range(day)
        if day.weekday() >= 5 or day in self._holidays:
            return None
        open_, close = self._special.get(day, self.regular)
        return datetime.combine(day, open_), datetime.combine(day, close)


class AlwaysOpenCalendar:
    """24시간 연중무휴 (업비트). 세션은 boundary ~ 다음 날 boundary."""

    def __init__(self, boundary: time = time(9)) -> None:
        self._boundary = boundary

    def session(self, day: date) -> tuple[datetime, datetime] | None:
        """day boundary부터 24시간."""
        start = datetime.combine(day, self._boundary)
        return start, start + timedelta(days=1)


KRX_CALENDAR = TableCalendar(
    (time(9), time(15, 30)), _parse(KRX_HOLIDAYS) | KRX_MANUAL, KRX_SPECIAL
)
NYSE_CALENDAR = TableCalendar(
    (time(9, 30), time(16)), _parse(NYSE_HOLIDAYS) | NYSE_MANUAL, NYSE_SPECIAL
)
UPBIT_CALENDAR = AlwaysOpenCalendar()
