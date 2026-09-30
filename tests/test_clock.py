"""P1-02 MarketClock·캘린더·이벤트·오류 타입 (04 §1, 05 §5, ADR 0009)."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

import ast
import dataclasses
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quantpilot.core import calendars
from quantpilot.core.clock import MarketClock, to_local, to_utc
from quantpilot.core.errors import (
    BrokerError,
    CalendarOutOfRange,
    DataStale,
    JudgeTimeout,
    QuantPilotError,
    RateLimited,
)
from quantpilot.core.events import (
    BarClosed,
    FillEvent,
    JudgmentEvent,
    OrderEvent,
    RiskEvent,
    SignalEvent,
    TradeEvent,
)
from quantpilot.core.models import Market

KRX = MarketClock(Market.KRX)
US = MarketClock(Market.US)
UPBIT = MarketClock(Market.UPBIT)


# ── 휴장일 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("clock", "day", "expected"),
    [
        (KRX, date(2026, 9, 24), False),  # 추석
        (KRX, date(2026, 9, 25), False),  # 추석
        (KRX, date(2025, 10, 6), False),  # 추석 대체
        (KRX, date(2026, 6, 3), False),  # 지방선거일 (내장 표 수동 추가)
        (KRX, date(2026, 12, 31), False),  # 연말 휴장
        (KRX, date(2026, 9, 26), False),  # 토요일
        (KRX, date(2026, 9, 30), True),
        (US, date(2026, 11, 26), False),  # 추수감사절
        (US, date(2026, 7, 3), False),  # 독립기념일 대체 (7/4 토요일)
        (US, date(2025, 1, 9), False),  # 카터 전 대통령 국장일
        (US, date(2026, 9, 7), False),  # 노동절
        (US, date(2026, 9, 30), True),
        (UPBIT, date(2026, 9, 24), True),  # 업비트는 연중무휴
    ],
)
def test_holidays(clock, day, expected):
    assert clock.is_session(day) is expected


def test_regular_and_special_sessions():
    assert KRX.session_bounds(date(2026, 9, 30)) == (
        datetime(2026, 9, 30, 9, 0),
        datetime(2026, 9, 30, 15, 30),
    )
    # KRX 새해 첫 거래일은 10:00 개장
    assert KRX.session_bounds(date(2026, 1, 2))[0] == datetime(2026, 1, 2, 10, 0)
    assert not KRX.is_open(datetime(2026, 1, 2, 9, 30))
    # 미국 추수감사절 다음 날 13:00 조기 폐장 (05 §5: ORB 청산 12:55 ET)
    assert US.session_bounds(date(2026, 11, 27))[1] == datetime(2026, 11, 27, 13, 0)
    assert not US.is_open(datetime(2026, 11, 27, 13, 0))
    assert US.session_bounds(date(2026, 11, 26)) is None


def test_is_open_boundaries():
    assert not KRX.is_open(datetime(2026, 9, 30, 8, 59))
    assert KRX.is_open(datetime(2026, 9, 30, 9, 0))  # 개장 시각 포함
    assert KRX.is_open(datetime(2026, 9, 30, 15, 29))
    assert not KRX.is_open(datetime(2026, 9, 30, 15, 30))  # 폐장 시각 제외
    assert not KRX.is_open(datetime(2026, 9, 24, 10, 0))  # 휴장일
    # tz-aware 입력은 시장 현지시간으로 바꿔서 판단: 00:30 UTC = 09:30 KST
    assert KRX.is_open(datetime(2026, 9, 30, 0, 30, tzinfo=UTC))


def test_upbit_is_24h_with_0900_session_boundary():
    for h in (0, 8, 9, 23):
        assert UPBIT.is_open(datetime(2026, 9, 24, h, 0))
    assert UPBIT.session_bounds(date(2026, 9, 30)) == (
        datetime(2026, 9, 30, 9, 0),
        datetime(2026, 10, 1, 9, 0),
    )
    assert UPBIT.next_open(datetime(2026, 9, 30, 8, 0)) == datetime(2026, 9, 30, 9, 0)
    assert UPBIT.next_close(datetime(2026, 9, 30, 8, 0)) == datetime(2026, 9, 30, 9, 0)


# ── 서머타임 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("day", "open_utc", "open_kst"),
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 14, 30, tzinfo=UTC), datetime(2026, 3, 6, 23, 30)),
        # 2026-03-08(일) 서머타임 시작 → 다음 거래일부터 한 시간 당겨짐
        (date(2026, 3, 9), datetime(2026, 3, 9, 13, 30, tzinfo=UTC), datetime(2026, 3, 9, 22, 30)),
        (
            date(2026, 10, 30),
            datetime(2026, 10, 30, 13, 30, tzinfo=UTC),
            datetime(2026, 10, 30, 22, 30),
        ),
        # 2026-11-01(일) 서머타임 종료
        (
            date(2026, 11, 2),
            datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 2, 23, 30),
        ),
    ],
)
def test_us_open_follows_dst(day, open_utc, open_kst):
    open_et, _ = US.session_bounds(day)
    assert open_et.time().isoformat() == "09:30:00"  # 현지시간은 늘 09:30
    assert US.to_utc(open_et) == open_utc
    assert to_local(US.to_utc(open_et), Market.KRX) == open_kst  # 05 §5: 22:30 (23:30) KST


def test_conversion_roundtrip_and_naive_utc_input():
    local = datetime(2026, 7, 1, 9, 30)
    assert US.to_local(US.to_utc(local)) == local
    assert to_local(datetime(2026, 7, 1, 13, 30), Market.US) == local  # naive 입력은 UTC로 간주
    kst = datetime(2026, 9, 30, 18, 0, tzinfo=timezone(timedelta(hours=9)))
    assert to_utc(kst, Market.US) == datetime(2026, 9, 30, 9, 0, tzinfo=UTC)  # aware는 그대로


# ── next_open / next_close ─────────────────────────────


def test_next_open_and_close_skip_holidays():
    # 추석 연휴(9/24~25) + 주말 → 9/28(월)
    assert KRX.next_open(datetime(2026, 9, 23, 16, 0)) == datetime(2026, 9, 28, 9, 0)
    assert KRX.next_close(datetime(2026, 9, 23, 16, 0)) == datetime(2026, 9, 28, 15, 30)
    # 장중이면 다음 개장은 내일, 다음 폐장은 오늘
    assert KRX.next_open(datetime(2026, 9, 30, 10, 0)) == datetime(2026, 10, 1, 9, 0)
    assert KRX.next_close(datetime(2026, 9, 30, 10, 0)) == datetime(2026, 9, 30, 15, 30)
    # 추수감사절 건너뛰고 조기 폐장일
    assert US.next_open(datetime(2026, 11, 25, 16, 30)) == datetime(2026, 11, 27, 9, 30)
    assert US.next_close(datetime(2026, 11, 25, 16, 30)) == datetime(2026, 11, 27, 13, 0)


def test_now_uses_injected_utc_clock():
    fixed = datetime(2026, 9, 30, 0, 30, tzinfo=UTC)
    clock = MarketClock(Market.KRX, utcnow=lambda: fixed)
    assert clock.now() == datetime(2026, 9, 30, 9, 30)
    assert clock.is_open()
    assert clock.next_close() == datetime(2026, 9, 30, 15, 30)
    us = MarketClock(Market.US, utcnow=lambda: fixed)
    assert us.now() == datetime(2026, 9, 29, 20, 30)
    assert us.next_open() == datetime(2026, 9, 30, 9, 30)


# ── 월 마지막 거래일 (04 §2, 05 §5) ─────────────────────


@pytest.mark.parametrize(
    ("clock", "day", "expected"),
    [
        (KRX, date(2026, 9, 30), True),
        (KRX, date(2026, 9, 29), False),
        (KRX, date(2026, 12, 30), True),  # 12/31 휴장
        (KRX, date(2026, 12, 31), False),  # 휴장일 자체는 거래일이 아님
        (KRX, date(2026, 10, 30), True),  # 10/31 토요일
        (US, date(2026, 7, 31), True),
        (US, date(2027, 12, 31), True),
        (US, date(2026, 5, 29), True),  # 5/31 일요일
        (US, date(2026, 5, 28), False),
        (UPBIT, date(2026, 9, 30), True),
        (UPBIT, date(2026, 9, 29), False),
    ],
)
def test_is_last_session_of_month(clock, day, expected):
    assert clock.is_last_session_of_month(day) is expected
    assert clock.is_last_session_of_month(datetime.combine(day, datetime.min.time())) is expected


# ── 범위 밖 ────────────────────────────────────────────


def test_out_of_range_is_loud():
    with pytest.raises(CalendarOutOfRange, match="2019-12-31"):
        KRX.session_bounds(date(2019, 12, 31))
    with pytest.raises(ValueError):  # ValueError로도 잡힌다
        US.is_session(date(2028, 1, 3))
    assert UPBIT.is_session(date(2030, 1, 1))  # 업비트는 표가 필요 없다


def test_custom_calendar_can_be_injected():
    class Closed:
        def session(self, day):
            return None

    clock = MarketClock(Market.KRX, calendar=Closed())
    assert not clock.is_open(datetime(2026, 9, 30, 10, 0))
    with pytest.raises(RuntimeError, match="개장이 없다"):
        clock.next_open(datetime(2026, 9, 30, 10, 0))


# ── exchange_calendars 대조 (설치됐을 때만) ────────────


@pytest.mark.parametrize("market", [Market.KRX, Market.US])
def test_builtin_table_matches_exchange_calendars(market):
    pytest.importorskip("exchange_calendars")
    from quantpilot.data.calendars import ExchangeCalendar

    builtin = MarketClock(market)
    manual = calendars.KRX_MANUAL if market == Market.KRX else calendars.NYSE_MANUAL
    ref = ExchangeCalendar(market, calendars.FIRST_DAY, calendars.LAST_DAY)
    day, diffs = calendars.FIRST_DAY, []
    while day <= calendars.LAST_DAY:
        if day not in manual and builtin.session_bounds(day) != ref.session(day):
            diffs.append(day)
        day += timedelta(days=1)
    assert diffs == []


# ── 오류 타입 ──────────────────────────────────────────


def test_error_types():
    e = BrokerError("5xx", retryable=True)
    assert e.retryable and isinstance(e, QuantPilotError)
    assert not BrokerError("잔고 부족", retryable=False).retryable
    rl = RateLimited("429", retry_after=1.5)
    assert isinstance(rl, BrokerError) and rl.retryable and rl.retry_after == 1.5
    assert RateLimited().retry_after is None
    with pytest.raises(TypeError):
        BrokerError("retryable는 반드시 명시")  # type: ignore[call-arg]
    assert issubclass(JudgeTimeout, QuantPilotError)
    assert issubclass(DataStale, QuantPilotError)


# ── 이벤트 ─────────────────────────────────────────────


def test_events_are_frozen_dataclasses():
    for cls in (
        TradeEvent,
        BarClosed,
        SignalEvent,
        JudgmentEvent,
        OrderEvent,
        FillEvent,
        RiskEvent,
    ):
        assert dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen
    ev = TradeEvent(Market.UPBIT, "KRW-BTC", datetime(2026, 9, 30, 9, 0), 95_000_000.0, 0.01)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.price = 1.0  # type: ignore[misc]
    names = {f.name for f in dataclasses.fields(JudgmentEvent)}
    assert not names & {"qty", "price", "stop"}  # 불변식 #7


# ── 코어 의존성 규칙 (CLAUDE.md) ───────────────────────


@pytest.mark.invariant
def test_core_imports_only_stdlib_pandas_numpy():
    allowed = set(sys.stdlib_module_names) | {"pandas", "numpy", "quantpilot"}
    core = Path(__file__).resolve().parents[1] / "quantpilot" / "core"
    bad = []
    for path in core.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            bad += [f"{path.name}:{n}" for n in names if n.split(".")[0] not in allowed]
    assert bad == []
