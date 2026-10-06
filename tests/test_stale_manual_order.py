"""시세가 끊긴 동안 수동 매수는 거부하고 매도·청산은 그대로 처리한다 (07 §7.6, 불변식 #6).

자동 전략은 체결이 있어야 분봉이 생기므로 시세가 끊기면 진입하지 않는다. 수동 주문은 엔진이 마지막으로 본
가격으로 바로 체결되므로, 끊긴 뒤 엔진이 멈추기까지(재접속 5회) 오래된 가격으로 매수될 수 있었다.
"""

from __future__ import annotations

from api_helpers import memory_sessions
from test_api_ws import SYM, UP, _engine_runner

from quantpilot.core.clock import MarketClock
from quantpilot.core.events import TradeEvent
from quantpilot.core.models import Fill, Order, OrderStatus
from quantpilot.engine.orders import STALE_AFTER, ManualOrderConsumer
from quantpilot.engine.replay import CollectingBus
from quantpilot.realtime import keys as hk
from quantpilot.realtime.hub import MemoryHub


def _item(oid: str, side: str, qty: float) -> dict:
    return {"op": "submit", "order": {"id": oid, "symbol": SYM, "side": side, "qty": qty}}


async def _consumer(age: dict[str, float | None]):
    _, sessions = await memory_sessions()
    hub = MemoryHub()
    runner = await _engine_runner(sessions, hub)
    runner.bus = CollectingBus()
    return ManualOrderConsumer(hub, runner, price_age=lambda s: age["v"]), runner


async def test_buy_rejected_when_price_is_stale_but_sell_still_fills():
    """체결 후 31초: 매수는 stale_price 거부(체결 없음), 같은 상황의 매도(청산)는 체결된다."""
    age: dict[str, float | None] = {"v": 1.0}
    consumer, runner = await _consumer(age)

    bought = await consumer.handle(_item("b1", "buy", 0.01))
    assert isinstance(bought, Fill)  # 신선하면 지금처럼 체결

    age["v"] = STALE_AFTER + 1
    stale = await consumer.handle(_item("b2", "buy", 0.01))
    assert isinstance(stale, Order)
    assert (stale.status, stale.reject_reason) == (OrderStatus.REJECTED, "stale_price")
    assert runner.executor.positions()[SYM].qty == 0.01  # 늘지 않았다

    sold = await consumer.handle(_item("s1", "sell", 0.01))
    assert isinstance(sold, Fill)  # 청산은 시세와 무관하게 허용 (불변식 #6)


async def test_buy_rejected_before_any_trade_after_restart():
    """재시작 직후 아직 체결을 못 받았으면(None) 매수하지 않는다 — 복원된 옛 가격으로 사지 않는다."""
    consumer, _ = await _consumer({"v": None})
    res = await consumer.handle(_item("b0", "buy", 0.01))
    assert isinstance(res, Order) and res.reject_reason == "stale_price"


async def test_without_price_age_behaviour_is_unchanged():
    """price_age를 주지 않는 기존 사용(테스트·백업)은 검사하지 않는다."""
    _, sessions = await memory_sessions()
    hub = MemoryHub()
    runner = await _engine_runner(sessions, hub)
    runner.bus = CollectingBus()
    res = await ManualOrderConsumer(hub, runner).handle(_item("b9", "buy", 0.01))
    assert isinstance(res, Fill)


async def test_engine_wires_trade_age_and_queue_rejects_stale(monkeypatch, tmp_path):
    """실제 엔진 조립: 체결 전에는 None, 체결을 받으면 경과 초. 큐로 들어온 매수도 같은 검사를 거친다."""
    from quantpilot.config import settings
    from quantpilot.engine.main import build_upbit_paper

    monkeypatch.setattr(settings, "events_file", tmp_path / "none.yaml")
    _, sessions = await memory_sessions()
    hub = MemoryHub()
    now = [100.0]
    eng = build_upbit_paper(sessions=sessions, hub=hub)
    eng._monotonic = lambda: now[0]
    eng.runner.bus = CollectingBus()
    assert eng.orders.price_age == eng.trade_age
    assert eng.trade_age(SYM) is None

    ts = MarketClock(UP).now()
    await eng.on_trade(TradeEvent(UP, SYM, ts, 50_000_000.0, 0.01))
    now[0] += 5
    assert eng.trade_age(SYM) == 5

    now[0] += STALE_AFTER
    await hub.push(hk.orders_queue(UP), _item("q1", "buy", 0.001))
    assert await eng.orders.drain() == 1
    rejected = [e for e in eng.runner.bus.events if e[0] == "order"]
    assert rejected and rejected[-1][1].order.reject_reason == "stale_price"
