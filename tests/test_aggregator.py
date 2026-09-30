"""P1-03 캔들 집계: 마감 규칙(다음 봉 첫 체결 또는 마감+2초), OHLCV, 늦은 체결, 일봉 경계."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from quantpilot.core.events import BarClosed, TradeEvent
from quantpilot.core.models import Market
from quantpilot.data.aggregator import CandleAggregator, parse_timeframe

T = datetime(2026, 9, 30, 10, 0)


def tr(sec: float, price: float, qty: float = 1.0, symbol: str = "KRW-BTC") -> TradeEvent:
    return TradeEvent(Market.UPBIT, symbol, T + timedelta(seconds=sec), price, qty)


def test_next_bar_first_trade_closes_previous():
    agg = CandleAggregator("1m", Market.UPBIT)
    for ev in [tr(0, 100, 1), tr(10, 105, 2), tr(20, 95, 0.5), tr(59.9, 101, 1)]:
        assert agg.on_trade(ev) is None
    bar = agg.on_trade(tr(61, 102, 3))
    assert bar == BarClosed(Market.UPBIT, "KRW-BTC", "1m", T, 100, 105, 95, 101, 4.5)
    assert agg.pending("KRW-BTC").open == 102
    assert agg.pending("KRW-BTC").ts == T + timedelta(minutes=1)


def test_timer_closes_at_end_plus_two_seconds():
    agg = CandleAggregator("1m", Market.UPBIT)
    agg.on_trade(tr(5, 100))
    assert agg.on_timer(T + timedelta(seconds=60)) == []
    assert agg.on_timer(T + timedelta(seconds=61.999)) == []
    [bar] = agg.on_timer(T + timedelta(seconds=62))
    assert (bar.ts, bar.close) == (T, 100)
    assert agg.pending("KRW-BTC") is None
    assert agg.on_timer(T + timedelta(seconds=120)) == []  # 두 번 확정하지 않는다


def test_late_trade_after_timer_close_is_dropped():
    agg = CandleAggregator("1m", Market.UPBIT)
    agg.on_trade(tr(5, 100))
    agg.on_timer(T + timedelta(seconds=62))
    assert agg.on_trade(tr(59, 999)) is None  # 이미 확정된 구간
    assert agg.pending("KRW-BTC") is None
    assert agg.on_trade(tr(63, 101)) is None  # 새 구간은 정상 시작
    assert agg.pending("KRW-BTC").open == 101


def test_gap_minutes_make_no_empty_bars():
    agg = CandleAggregator("1m", Market.UPBIT)
    agg.on_trade(tr(0, 100))
    bar = agg.on_trade(tr(5 * 60 + 1, 110))  # 4분 동안 체결 없음
    assert bar.ts == T
    assert agg.pending("KRW-BTC").ts == T + timedelta(minutes=5)


def test_symbols_are_independent():
    agg = CandleAggregator("1m", Market.UPBIT)
    agg.on_trade(tr(0, 100, symbol="KRW-BTC"))
    agg.on_trade(tr(30, 5, symbol="KRW-ETH"))
    assert agg.on_trade(tr(61, 6, symbol="KRW-ETH")).symbol == "KRW-ETH"
    assert agg.pending("KRW-BTC").close == 100
    closed = agg.on_timer(T + timedelta(minutes=1, seconds=2))
    assert [b.symbol for b in closed] == ["KRW-BTC"]


def test_five_minute_and_hour_buckets():
    agg5 = CandleAggregator("5m", Market.UPBIT)
    assert agg5.bucket_start(datetime(2026, 9, 30, 10, 7, 30)) == datetime(2026, 9, 30, 10, 5)
    agg1h = CandleAggregator("1h", Market.UPBIT)
    assert agg1h.bucket_start(datetime(2026, 9, 30, 10, 59, 59)) == datetime(2026, 9, 30, 10, 0)


def test_upbit_daily_boundary_is_0900_kst():
    agg = CandleAggregator("1d", Market.UPBIT)
    assert agg.bucket_start(datetime(2026, 9, 30, 8, 59, 59)) == datetime(2026, 9, 29, 9, 0)
    assert agg.bucket_start(datetime(2026, 9, 30, 9, 0)) == datetime(2026, 9, 30, 9, 0)
    assert agg.bucket_start(datetime(2026, 10, 1, 2, 0)) == datetime(2026, 9, 30, 9, 0)


def test_other_markets_daily_boundary_is_midnight():
    agg = CandleAggregator("1d", Market.KRX)
    assert agg.bucket_start(datetime(2026, 9, 30, 15, 20)) == datetime(2026, 9, 30)


def test_market_mismatch_rejected():
    agg = CandleAggregator("1m", Market.KRX)
    with pytest.raises(ValueError):
        agg.on_trade(tr(0, 100))


@pytest.mark.parametrize("tf", ["1m", "3m", "15m", "60m", "4h", "1d"])
def test_supported_timeframes(tf):
    assert timedelta(days=1) % parse_timeframe(tf) == timedelta(0)


@pytest.mark.parametrize("tf", ["", "0m", "7m", "5h", "2d", "1w", "m1"])
def test_unsupported_timeframes(tf):
    with pytest.raises(ValueError):
        parse_timeframe(tf)
