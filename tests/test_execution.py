from datetime import UTC, datetime

import pytest

from quantpilot.backtest import preset
from quantpilot.core.models import Market, Order, OrderStatus, OrderType, Position, Side
from quantpilot.execution import PaperBroker, RiskManager, RiskRules


def make_broker(cash=10_000_000):
    b = PaperBroker(Market.UPBIT, preset("upbit"), cash)
    b.on_price("KRW-ETH", 5_000_000)
    return b


def test_market_buy_applies_slippage_and_fee():
    b = make_broker()
    f = b.submit(Order("KRW-ETH", Side.BUY, 1.0))
    assert f.price == pytest.approx(5_000_000 * 1.0005)
    assert f.fee == pytest.approx(f.price * 0.0005)
    assert b.cash() == pytest.approx(10_000_000 - f.price - f.fee)
    assert b.positions()["KRW-ETH"].qty == 1.0
    assert len(b.ledger) == 1


def test_limit_order_waits_then_fills_on_price():
    b = make_broker()
    o = b.submit(Order("KRW-ETH", Side.BUY, 1.0, OrderType.LIMIT, limit_price=4_900_000))
    assert o.status == OrderStatus.PENDING and b.pending()
    assert b.on_price("KRW-ETH", 4_950_000) == []
    fills = b.on_price("KRW-ETH", 4_890_000)
    assert len(fills) == 1 and fills[0].price == 4_900_000
    assert not b.pending()


def test_cannot_sell_more_than_held_or_buy_without_cash():
    b = make_broker(cash=1_000_000)
    o = b.submit(Order("KRW-ETH", Side.SELL, 0.1))
    assert o.status == OrderStatus.REJECTED
    o = b.submit(Order("KRW-ETH", Side.BUY, 1.0))
    assert o.status == OrderStatus.REJECTED and "현금" in o.reject_reason


def test_round_trip_pnl_includes_costs():
    b = make_broker()
    b.submit(Order("KRW-ETH", Side.BUY, 1.0))
    b.on_price("KRW-ETH", 5_000_000)
    b.submit(Order("KRW-ETH", Side.SELL, 1.0))
    assert not b.positions()
    assert b.cash() < 10_000_000  # 왕복 비용만큼 손실
    assert 10_000_000 - b.cash() == pytest.approx(
        5_000_000 * preset("upbit").round_trip_rate(), rel=1e-3
    )


def test_risk_one_percent_rule_sizes_by_stop():
    rm = RiskManager()
    o = Order("KRW-ETH", Side.BUY, 10.0, stop=4_900_000)
    d = rm.check(o, equity=10_000_000, price=5_000_000, positions={})
    # 1% = 100,000원 리스크 / 100,000원 스탑 거리 = 1 ETH, 다시 종목 비중 25% = 0.5 ETH
    assert d.allowed and d.qty == pytest.approx(0.5)
    assert any("1% 룰" in a for a in d.adjustments) and any("비중 상한" in a for a in d.adjustments)


def test_risk_monthly_circuit_breaker_blocks_entries_but_allows_exits():
    rm = RiskManager()
    now = datetime(2026, 9, 1, tzinfo=UTC)
    rm.roll_month(10_000_000, now)
    d = rm.check(
        Order("KRW-ETH", Side.BUY, 0.1), equity=9_400_000, price=5_000_000, positions={}, now=now
    )
    assert not d.allowed and "monthly_loss" in d.reason
    pos = {"KRW-ETH": Position("KRW-ETH", qty=0.2, avg_price=5_000_000)}
    d = rm.check(
        Order("KRW-ETH", Side.SELL, 0.2), equity=9_400_000, price=5_000_000, positions=pos, now=now
    )
    assert d.allowed and d.reason == "exit"
    # 다음 달이 되면 해제
    rm.roll_month(9_400_000, datetime(2026, 10, 1, tzinfo=UTC))
    d = rm.check(
        Order("KRW-ETH", Side.BUY, 0.1),
        equity=9_400_000,
        price=5_000_000,
        positions={},
        now=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert d.allowed


def test_risk_intraday_cap_and_api_errors():
    rm = RiskManager(RiskRules(max_orders_per_symbol_per_sec=100))
    d = rm.check(
        Order("QQQ", Side.BUY, 100),
        equity=10_000,
        price=100,
        positions={},
        horizon="intraday",
        intraday_exposure=1_500,
    )
    assert d.allowed and d.qty == pytest.approx(5.0)  # 20% - 15% = 5% = $500 = 5주
    for _ in range(3):
        rm.api_error()
    d = rm.check(Order("QQQ", Side.BUY, 1), equity=10_000, price=100, positions={})
    assert not d.allowed and "api_errors" in d.reason
    rm.api_ok()
    assert rm.check(Order("QQQ", Side.BUY, 1), equity=10_000, price=100, positions={}).allowed


def test_risk_wash_trade_guard():
    rm = RiskManager()
    o = Order("KRW-BTC", Side.BUY, 0.01)
    rm.note_pending(o, True)
    d = rm.check(
        Order("KRW-BTC", Side.SELL, 0.01),
        equity=1e7,
        price=1e8,
        positions={"KRW-BTC": Position("KRW-BTC", 0.01, 1e8)},
    )
    assert not d.allowed and "자전거래" in d.reason
