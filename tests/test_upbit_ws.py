"""P1-03 업비트 웹소켓: 메시지 해석, 재접속 백오프, 무응답 → DataStale. 가짜 서버로 결정적으로 돈다."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest

from quantpilot.core.errors import DataStale
from quantpilot.core.events import TradeEvent
from quantpilot.core.models import Market, Side
from quantpilot.data.upbit_ws import UpbitStream, parse_orderbook, parse_trade, subscribe_message

# 2026-09-30 00:00:00 UTC = 09:00 KST
T0_MS = 1_790_726_400_000
HANG = object()  # 이 지점에서 서버가 아무것도 보내지 않고 멈춘다


def trade(code: str, price: float, qty: float, ms: int, ask_bid: str = "BID") -> bytes:
    msg = {
        "type": "trade",
        "code": code,
        "trade_price": price,
        "trade_volume": qty,
        "ask_bid": ask_bid,
        "trade_timestamp": ms,
        "timestamp": ms + 5,
        "stream_type": "REALTIME",
    }
    return json.dumps(msg).encode()  # 업비트는 바이너리 프레임으로 보낸다


def orderbook(code: str, ms: int) -> bytes:
    units = [
        {"ask_price": 101.0, "bid_price": 100.0, "ask_size": 1.5, "bid_size": 2.0},
        {"ask_price": 102.0, "bid_price": 99.0, "ask_size": 3.0, "bid_size": 4.0},
    ]
    return json.dumps(
        {"type": "orderbook", "code": code, "timestamp": ms, "orderbook_units": units}
    ).encode()


class FakeConn:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self.script:
            raise ConnectionError("서버가 연결을 닫음")
        item = self.script.pop(0)
        if item is HANG:
            await asyncio.Event().wait()
        return item


class FakeServer:
    """연결마다 대본(script) 하나를 쓴다. 대본이 떨어지면 접속을 거부한다."""

    def __init__(self, *scripts: list[Any]) -> None:
        self.scripts = list(scripts)
        self.conns: list[FakeConn] = []
        self.attempts = 0

    @asynccontextmanager
    async def connect(self, url: str):
        self.attempts += 1
        if not self.scripts:
            raise OSError("접속 거부")
        conn = FakeConn(self.scripts.pop(0))
        self.conns.append(conn)
        yield conn


def collector(stop_after: int | None = None, stream_ref: list[UpbitStream] | None = None):
    got: list[TradeEvent] = []

    def on_trade(ev: TradeEvent) -> None:
        got.append(ev)
        if stop_after is not None and len(got) >= stop_after and stream_ref:
            stream_ref[0].stop()

    return got, on_trade


def make_stream(server: FakeServer, got_stop: int | None = None, **kw: Any):
    ref: list[UpbitStream] = []
    got, on_trade = collector(got_stop, ref)
    kw.setdefault("backoff", (0.0, 0.0, 0.0))
    stream = UpbitStream(["KRW-BTC", "KRW-ETH"], on_trade=on_trade, connect=server.connect, **kw)
    ref.append(stream)
    return stream, got


async def run(stream: UpbitStream) -> None:
    await asyncio.wait_for(stream.run(), timeout=5)


# ── 메시지 해석 ─────────────────────────────────────────


def test_parse_trade_local_time_and_side():
    ev = parse_trade(json.loads(trade("KRW-BTC", 95_000_000.0, 0.01, T0_MS + 1500, "ASK")))
    assert ev == TradeEvent(
        market=Market.UPBIT,
        symbol="KRW-BTC",
        ts=datetime(2026, 9, 30, 9, 0, 1, 500_000),
        price=95_000_000.0,
        qty=0.01,
        side=Side.SELL,
    )
    assert ev.ts.tzinfo is None
    assert parse_trade(json.loads(trade("KRW-BTC", 1, 1, T0_MS, "BID"))).side is Side.BUY


def test_parse_orderbook_best_quotes():
    ob = parse_orderbook(json.loads(orderbook("KRW-BTC", T0_MS)))
    assert (ob.best_bid, ob.best_ask) == (100.0, 101.0)
    assert ob.ts == datetime(2026, 9, 30, 9, 0)
    assert len(ob.levels) == 2


def test_subscribe_message_shape():
    req = json.loads(subscribe_message(["KRW-BTC"], orderbook=True))
    assert "ticket" in req[0]
    assert req[1] == {"type": "trade", "codes": ["KRW-BTC"]}
    assert req[2] == {"type": "orderbook", "codes": ["KRW-BTC"]}
    assert req[-1] == {"format": "DEFAULT"}
    assert len(json.loads(subscribe_message(["KRW-BTC"], orderbook=False))) == 3


def test_empty_symbols_rejected():
    with pytest.raises(ValueError):
        UpbitStream([])


# ── 수신 루프 ───────────────────────────────────────────


async def test_trades_published_and_orderbook_kept():
    server = FakeServer(
        [orderbook("KRW-BTC", T0_MS), trade("KRW-BTC", 100.0, 1.0, T0_MS + 10), b"not json"]
        + [trade("KRW-ETH", 5.0, 2.0, T0_MS + 20)]
    )
    stream, got = make_stream(server, got_stop=2)
    await run(stream)
    assert [(e.symbol, e.price) for e in got] == [("KRW-BTC", 100.0), ("KRW-ETH", 5.0)]
    assert stream.orderbook("KRW-BTC").best_ask == 101.0
    assert stream.orderbook("KRW-ETH") is None
    sub = json.loads(server.conns[0].sent[0])
    assert sub[1]["codes"] == ["KRW-BTC", "KRW-ETH"]
    assert stream.reconnects == 0


async def test_reconnects_after_disconnect_and_resubscribes():
    server = FakeServer(
        [trade("KRW-BTC", 100.0, 1.0, T0_MS), trade("KRW-BTC", 101.0, 1.0, T0_MS + 1)],
        [trade("KRW-BTC", 102.0, 1.0, T0_MS + 2)],
    )
    stream, got = make_stream(server, got_stop=3)
    await run(stream)
    assert [e.price for e in got] == [100.0, 101.0, 102.0]
    assert stream.reconnects == 1
    assert len(server.conns) == 2
    # 재접속마다 새 ticket으로 다시 구독한다
    tickets = [json.loads(c.sent[0])[0]["ticket"] for c in server.conns]
    assert tickets[0] != tickets[1]


async def test_backoff_delays_follow_schedule(monkeypatch):
    slept: list[float] = []
    server = FakeServer([], [], [trade("KRW-BTC", 1.0, 1.0, T0_MS)])
    stream, got = make_stream(server, got_stop=1, backoff=(0.01, 0.02, 0.03))

    real = stream._sleep_or_stop

    async def spy(delay: float) -> None:
        slept.append(delay)
        await real(0)

    monkeypatch.setattr(stream, "_sleep_or_stop", spy)
    await run(stream)
    assert slept == [0.01, 0.02]  # 연속 실패 1회째, 2회째
    assert len(got) == 1


async def test_gives_up_with_datastale_after_max_retries():
    server = FakeServer()  # 모든 접속 거부
    stream, _ = make_stream(server, backoff=(0.0, 0.0, 0.0, 0.0, 0.0))
    with pytest.raises(DataStale):
        await run(stream)
    assert server.attempts == 6  # 첫 시도 + 재접속 5회


async def test_success_resets_failure_count():
    # 실패 2회 → 성공 → 실패 2회 → 성공: 한도(2)를 넘지 않는다
    server = FakeServer(
        [],
        [],
        [trade("KRW-BTC", 1.0, 1.0, T0_MS)],
        [],
        [],
        [trade("KRW-BTC", 2.0, 1.0, T0_MS + 1)],
    )
    stream, got = make_stream(server, got_stop=2, backoff=(0.0, 0.0))
    await run(stream)
    assert [e.price for e in got] == [1.0, 2.0]


async def test_silence_beyond_stale_after_reconnects():
    server = FakeServer([HANG], [trade("KRW-BTC", 1.0, 1.0, T0_MS)])
    stream, got = make_stream(server, got_stop=1, stale_after=0.05)
    await run(stream)
    assert len(got) == 1
    assert stream.reconnects == 1


async def test_silence_everywhere_ends_in_datastale():
    server = FakeServer([HANG], [HANG], [HANG])
    stream, _ = make_stream(server, stale_after=0.05, backoff=(0.0, 0.0))
    with pytest.raises(DataStale):
        await run(stream)
    assert server.attempts == 3


async def test_handler_error_does_not_drop_connection():
    server = FakeServer([trade("KRW-BTC", 1.0, 1.0, T0_MS), trade("KRW-BTC", 2.0, 1.0, T0_MS + 1)])
    seen: list[float] = []
    ref: list[UpbitStream] = []

    def on_trade(ev: TradeEvent) -> None:
        seen.append(ev.price)
        if ev.price == 1.0:
            raise RuntimeError("소비자 버그")
        ref[0].stop()

    stream = UpbitStream(["KRW-BTC"], on_trade=on_trade, connect=server.connect, backoff=(0.0,))
    ref.append(stream)
    await run(stream)
    assert seen == [1.0, 2.0]
    assert stream.reconnects == 0


async def test_async_handler_awaited():
    server = FakeServer([trade("KRW-BTC", 1.0, 1.0, T0_MS)])
    got: list[TradeEvent] = []
    ref: list[UpbitStream] = []

    async def on_trade(ev: TradeEvent) -> None:
        await asyncio.sleep(0)
        got.append(ev)
        ref[0].stop()

    stream = UpbitStream(["KRW-BTC"], on_trade=on_trade, connect=server.connect)
    ref.append(stream)
    await run(stream)
    assert len(got) == 1


# ── 30초 무응답 판정 ────────────────────────────────────


async def test_ensure_fresh_30s_rule():
    now = [1000.0]
    server = FakeServer([trade("KRW-BTC", 1.0, 1.0, T0_MS)])
    stream, _ = make_stream(server, got_stop=1, monotonic=lambda: now[0])
    with pytest.raises(DataStale):
        stream.ensure_fresh()  # 아직 아무것도 못 받음
    await run(stream)
    stream.ensure_fresh()
    now[0] += 30.0
    stream.ensure_fresh()  # 정확히 30초는 아직 허용
    now[0] += 0.1
    with pytest.raises(DataStale):
        stream.ensure_fresh()
    assert stream.seconds_since_last_message() == pytest.approx(30.1)


# ── 실제 websockets 라이브러리 (루프백 서버, 인터넷 아님) ─────────


async def test_real_websockets_disconnect_reconnects():
    pytest.importorskip("websockets")
    from websockets.asyncio.server import serve

    sessions = [
        [trade("KRW-BTC", 1.0, 1.0, T0_MS)],  # 1건 보내고 서버가 끊음 → ConnectionClosed
        [trade("KRW-BTC", 2.0, 1.0, T0_MS + 1)],
    ]
    subs: list[str] = []

    async def handler(ws) -> None:
        subs.append(await ws.recv())
        for frame in sessions.pop(0) if sessions else []:
            await ws.send(frame)
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ref: list[UpbitStream] = []
        got, on_trade = collector(2, ref)
        stream = UpbitStream(
            ["KRW-BTC"], on_trade=on_trade, url=f"ws://127.0.0.1:{port}", backoff=(0.0,)
        )
        ref.append(stream)
        await run(stream)
    assert [e.price for e in got] == [1.0, 2.0]
    assert stream.reconnects == 1
    assert len(subs) == 2
