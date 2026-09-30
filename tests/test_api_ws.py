"""P1-12 WS 허브·HubBus·엔진 수동 주문 큐 테스트.

- WS: 인증 실패 4401, 구독·팬아웃(연결별 채널 필터), 해지, ping, 무응답 4408
- HubBus: 엔진 이벤트 → 채널·상태 키, 시세 스로틀, 판단·judge_down 기록 (t10 숙제)
- 불변식 #9: API가 넣은 큐 항목을 엔진이 OrderExecutor(리스크 재검사 → 브로커)로 실행
"""

# ruff: noqa: DTZ001

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

import httpx
from api_helpers import API, PASSWORD, SECRET, make_settings, memory_sessions
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from quantpilot.api import auth as jwt
from quantpilot.api.app import create_app
from quantpilot.backtest.costs import preset
from quantpilot.core.clock import MarketClock
from quantpilot.core.errors import JudgeError
from quantpilot.core.events import JudgmentEvent, SignalEvent, TradeEvent
from quantpilot.core.models import Gate, JudgeResult, Market, OrderStatus, Side, Target
from quantpilot.db.repo import (
    SqlConfigRepo,
    SqlLedger,
    SqlPositionRepo,
    SqlSignalRepo,
)
from quantpilot.engine.orders import ManualOrderConsumer
from quantpilot.engine.replay import CollectingBus, StubFeatureBuilder
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.executor import OrderExecutor
from quantpilot.execution.persistent_paper import PersistentPaperBroker
from quantpilot.execution.ratelimit import NoLimiter
from quantpilot.execution.risk import RiskManager
from quantpilot.judgment.base import LLMVerdict, State
from quantpilot.judgment.pipeline import JudgmentPipeline
from quantpilot.judgment.stub import StubJudge, StubPipeline
from quantpilot.realtime import keys as hk
from quantpilot.realtime.bus import EventRecorder, HubBus
from quantpilot.realtime.hub import MemoryHub, make_hub
from quantpilot.strategies import create

UP = Market.UPBIT
SYM = "KRW-BTC"


def token() -> str:
    return jwt.encode({"sub": "admin", "exp": 4102444800}, SECRET)


@pytest.fixture
def ws_app(tmp_path):
    hub = MemoryHub()
    app = create_app(settings=make_settings(tmp_path), hub=hub, ws_idle=5.0)
    with TestClient(app) as client:
        yield client, hub


def publish(client: TestClient, hub: MemoryHub, ch: str, data: dict) -> None:
    client.portal.call(hub.publish, ch, {"ts": "2026-09-30T00:00:00+00:00", "data": data})


# ── WS ───────────────────────────────────────────────────


@pytest.mark.parametrize("auth", ["", "garbage", jwt.encode({"exp": 1}, SECRET)])
def test_ws_rejects_bad_auth(ws_app, auth):
    client, _ = ws_app
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"auth": auth, "subscribe": ["fills"]})
        assert ws.receive_json()["data"]["code"] == "UNAUTHORIZED"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_ws_subscribe_fanout_unsubscribe_ping(ws_app):
    client, hub = ws_app
    with (
        client.websocket_connect(f"{API}/ws") as a,
        client.websocket_connect(f"{API}/ws") as b,
    ):
        a.send_json({"auth": token(), "subscribe": ["fills", "ticks:upbit:KRW-BTC"]})
        b.send_json({"auth": token(), "subscribe": ["risk"]})
        assert a.receive_json()["data"]["subscribed"] == ["fills", "ticks:upbit:KRW-BTC"]
        assert b.receive_json()["data"]["subscribed"] == ["risk"]

        publish(client, hub, "fills", {"symbol": SYM, "qty": 0.1})
        publish(client, hub, "risk", {"kind": "judge_down"})
        publish(client, hub, "judgments", {"id": 1})  # 아무도 구독 안 함
        got = a.receive_json()
        assert got == {
            "ch": "fills",
            "ts": "2026-09-30T00:00:00+00:00",
            "data": {"symbol": SYM, "qty": 0.1},
        }
        assert b.receive_json()["ch"] == "risk"

        # b는 fills를 받지 않았다: 다음 메시지가 pong이어야 한다
        b.send_json({"ping": 7})
        pong = b.receive_json()
        assert pong["ch"] == "pong" and pong["data"] == {"pong": 7}

        a.send_json({"unsubscribe": ["fills"], "subscribe": ["judgments"]})
        assert a.receive_json()["data"]["subscribed"] == ["judgments", "ticks:upbit:KRW-BTC"]
        publish(client, hub, "fills", {"symbol": SYM})
        publish(client, hub, "judgments", {"id": 2})
        assert a.receive_json()["data"] == {"id": 2}
        a.send_json({"ping": 1})
        assert a.receive_json()["ch"] == "pong"
        a.send_json({"subscribe": "fills"})  # 형식 오류는 연결을 끊지 않는다
        assert a.receive_json()["ch"] == "error"


def test_ws_pong_follows_ping_without_foreign_channels(ws_app):
    client, hub = ws_app
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"auth": token(), "subscribe": ["risk"]})
        ws.receive_json()
        publish(client, hub, "fills", {"x": 1})
        ws.send_json({"ping": 3})
        msg = ws.receive_json()
        assert msg["ch"] == "pong" and msg["data"] == {"pong": 3}


def test_ws_idle_timeout_closes(tmp_path):
    app = create_app(settings=make_settings(tmp_path), hub=MemoryHub(), ws_idle=0.3)
    with TestClient(app) as client, client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"auth": token(), "subscribe": []})
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4408


# ── HubBus ───────────────────────────────────────────────


class Tick:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _collect(hub: MemoryHub, out: list) -> None:
    async for ch, msg in hub.listen():
        out.append((ch, msg))


async def test_hubbus_trade_throttle_and_state_keys():
    hub, clock, seen = MemoryHub(), Tick(), []
    task = asyncio.create_task(_collect(hub, seen))
    await asyncio.sleep(0)
    bus = HubBus(hub, UP, monotonic=clock)
    ts = datetime(2026, 9, 30, 9, 0)
    for i in range(10):  # 같은 순간 10건 → 1건만
        await bus.publish("trade", TradeEvent(UP, SYM, ts, 100.0 + i, 0.1, Side.BUY))
    clock.t = 0.3
    await bus.publish("trade", TradeEvent(UP, SYM, ts, 200.0, 0.1))
    await asyncio.sleep(0)
    ticks = [m for ch, m in seen if ch == "ticks:upbit:KRW-BTC"]
    assert [m["data"]["price"] for m in ticks] == [100.0, 200.0]
    assert ticks[0]["ts"] == "2026-09-30T00:00:00+00:00"  # KST 09:00 → UTC
    assert await hub.get(hk.px(UP, SYM)) == 200.0 and await hub.get(hk.feed(UP)) is True
    task.cancel()


async def test_hubbus_judgment_recorded_and_published():
    engine, sessions = await memory_sessions()
    hub, seen = MemoryHub(), []
    task = asyncio.create_task(_collect(hub, seen))
    await asyncio.sleep(0)
    bus = HubBus(hub, UP, recorder=EventRecorder.from_sessions(sessions))
    ts = MarketClock(UP).now()
    await bus.publish("signal", SignalEvent(UP, "orb", Target(SYM, 0.1), "entry", ts))
    jr = JudgeResult({"news_risk": 0.0}, 0.95, model="typesafe")
    v = (LLMVerdict("claude", True, "ok"), LLMVerdict("gemini", True, "ok"))
    state = State("upbit", SYM, "orb", "breakout")
    await bus.publish("judgment", JudgmentEvent(0, jr, Gate.FULL, 1.0, ts, (), v, state))
    await asyncio.sleep(0)
    ((_, msg),) = [x for x in seen if x[0] == "judgments"]
    d = msg["data"]
    assert d["symbol"] == SYM and d["strategy"] == "orb" and d["gate"] == "full"
    assert d["verdicts"] == [
        {"model": "claude", "approve": True},
        {"model": "gemini", "approve": True},
    ]
    sig = await SqlSignalRepo(sessions).get(d["signal_id"])
    assert sig["symbol"] == SYM and sig["outcome"] == "pending"
    # 전략 설정 행이 없어서 새로 만들어졌다
    assert await SqlConfigRepo(sessions).strategy_id("orb", UP) is not None
    task.cancel()
    await engine.dispose()


async def test_judge_down_from_pipeline_reaches_risk_events_and_channel():
    """t10 숙제: 파이프라인의 judge_down RiskEvent를 구독해 risk_events·WS risk로."""
    engine, sessions = await memory_sessions()
    hub, seen = MemoryHub(), []
    task = asyncio.create_task(_collect(hub, seen))
    await asyncio.sleep(0)
    bus = HubBus(hub, UP, recorder=EventRecorder.from_sessions(sessions))

    class DownJudge(StubJudge):
        consecutive_timeouts = 10

        async def ajudge(self, state):
            raise JudgeError("timeout")

    pipe = JudgmentPipeline(DownJudge(), bus=bus)
    sig = SignalEvent(UP, "vol_breakout", Target(SYM, 0.2), "entry", MarketClock(UP).now())
    je = await pipe.evaluate(sig, State("upbit", SYM, "vol_breakout", "x"))
    assert je.gate == Gate.HOLD
    await asyncio.sleep(0)
    ((_, msg),) = [x for x in seen if x[0] == "risk"]
    assert msg["data"]["kind"] == "judge_down" and msg["data"]["id"] is not None
    from quantpilot.db.repo import SqlRiskEventRepo

    row = await SqlRiskEventRepo(sessions).open("judge_down", UP)
    assert row["detail"]["consecutive_timeouts"] == 10
    task.cancel()
    await engine.dispose()


async def test_hubbus_swallow_errors():
    class Broken(MemoryHub):
        async def publish(self, channel, data):
            raise RuntimeError("redis down")

    bus = HubBus(Broken(), UP)
    await bus.publish("fill", object())  # 경로 없는 이벤트
    await bus.publish("trade", TradeEvent(UP, SYM, datetime(2026, 9, 30), 1.0, 1.0))  # 예외 안 남


def test_make_hub_falls_back_without_redis(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_redis(name, *a, **kw):
        if name.startswith("redis"):
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_redis)
    s = type("S", (), {"redis_url": "redis://localhost:6379/0"})()
    assert isinstance(make_hub(s), MemoryHub)
    assert isinstance(make_hub(type("S", (), {"redis_url": ""})()), MemoryHub)


async def test_memory_hub_ttl_and_queue():
    clock = Tick()
    hub = MemoryHub(monotonic=clock)
    await hub.set("px:upbit:X", 1.0, ttl=60)
    clock.t = 59
    assert await hub.get("px:upbit:X") == 1.0
    clock.t = 60
    assert await hub.get("px:upbit:X") is None
    await hub.push("q", {"a": 1})
    await hub.push("q", {"a": 2})
    assert [await hub.pop("q"), await hub.pop("q"), await hub.pop("q")] == [
        {"a": 1},
        {"a": 2},
        None,
    ]


# ── 불변식 #9: API → 큐 → 엔진 OrderExecutor ──────────────────


async def _engine_runner(sessions, hub):
    broker = PersistentPaperBroker(
        UP,
        preset(UP),
        10_000_000,
        positions=SqlPositionRepo(sessions),
        config=SqlConfigRepo(sessions),
    )
    await broker.restore()
    risk = RiskManager()
    ex = OrderExecutor(
        broker, risk, SqlLedger(sessions), NoLimiter(), signals=SqlSignalRepo(sessions)
    )
    clock = MarketClock(UP)
    runner = TickRunner(
        UP,
        [create("vol_breakout")],
        StubFeatureBuilder("upbit"),
        StubPipeline(),
        ex,
        risk,
        clock,
        HubBus(hub, UP),
        cost=preset(UP),
    )
    ex.mark(SYM, 50_000_000, clock.now())
    return runner


@pytest.mark.invariant
async def test_invariant9_queued_order_executes_through_order_executor(tmp_path):
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    app = create_app(settings=make_settings(tmp_path), sessions=sessions, hub=hub)
    runner = await _engine_runner(sessions, hub)
    consumer = ManualOrderConsumer(hub, runner)
    calls: list[str] = []
    real_check = runner.risk.check

    def spy_check(order, **kw):
        calls.append(order.id)
        return real_check(order, **kw)

    runner.risk.check = spy_check
    await hub.set(hk.px(UP, SYM), 50_000_000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        tok = (await c.post(f"{API}/auth/login", json={"password": PASSWORD})).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        body = {"market": "upbit", "symbol": SYM, "side": "buy", "amount": 1_000_000}
        r = await c.post(f"{API}/orders", json=body, headers=h)
        oid = r.json()["order"]["id"]
        assert await consumer.drain() == 1
        assert calls == [oid]  # 엔진 RiskManager가 다시 검사했다
        fills = (await c.get(f"{API}/fills?market=upbit", headers=h)).json()["items"]
        assert fills[0]["order_id"] == oid and fills[0]["qty"] == pytest.approx(0.02)
        pos = (await c.get(f"{API}/positions?market=upbit", headers=h)).json()
        assert pos[0]["symbol"] == SYM and pos[0]["strategy"] == "manual"
        orders = (await c.get(f"{API}/orders?status=filled", headers=h)).json()["items"]
        assert orders[0]["id"] == oid

        # 엔진 쪽에서 할트가 걸려 있으면 큐에 들어온 주문도 실행되지 않는다
        runner.risk.halted_reason = "reconcile_mismatch"
        runner.bus = CollectingBus()
        await hub.push(
            hk.orders_queue(UP),
            {"op": "submit", "order": {"id": "q2", "symbol": SYM, "side": "buy", "qty": 0.001}},
        )
        await consumer.drain()
        ((topic, ev),) = runner.bus.events
        assert topic == "order" and ev.order.id == "q2"
        assert ev.order.status == OrderStatus.REJECTED
        assert ev.order.reject_reason.startswith("risk:halted")
        assert len((await c.get(f"{API}/fills", headers=h)).json()["items"]) == 1
    await engine.dispose()


async def test_consumer_no_price_and_cancel():
    engine, sessions = await memory_sessions()
    hub = MemoryHub()
    runner = await _engine_runner(sessions, hub)
    runner.bus = CollectingBus()
    consumer = ManualOrderConsumer(hub, runner)
    await hub.push(
        hk.orders_queue(UP),
        {"op": "submit", "order": {"id": "np", "symbol": "KRW-NOPE", "side": "buy", "qty": 1}},
    )
    await hub.push(hk.orders_queue(UP), {"op": "cancel", "order_id": "missing"})
    await hub.push(hk.orders_queue(UP), {"op": "bogus"})
    assert await consumer.drain() == 3
    ((topic, ev),) = runner.bus.events
    assert topic == "order" and ev.order.reject_reason == "no_price"
    await engine.dispose()


def test_build_upbit_paper_wires_hub_bus_and_order_consumer(monkeypatch):
    from quantpilot.engine.main import build_upbit_paper

    hub = MemoryHub()
    eng = build_upbit_paper(hub=hub)
    assert isinstance(eng.runner.bus, HubBus) and eng.runner.bus.recorder is None
    assert isinstance(eng.orders, ManualOrderConsumer)
    trade = TradeEvent(UP, "KRW-BTC", datetime(2026, 9, 30, 9, 0), 1.0, 1.0)
    asyncio.run(eng.on_trade(trade))
    assert asyncio.run(hub.get(hk.px(UP, "KRW-BTC"))) == 1.0
