"""엔진이 업비트 WS 호가를 허브 `ob` 키·`orderbook` 채널로 내보낸다 (02 §5, 03 §3) — 화면 호가창이 비던 문제."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from test_upbit_ws import T0_MS, orderbook

from quantpilot.core.models import Market
from quantpilot.data.upbit_ws import OrderbookLevel, OrderbookSnapshot, parse_orderbook
from quantpilot.engine.main import MarketEngine
from quantpilot.realtime.bus import ORDERBOOK_TTL, orderbook_payload


class SpyHub:
    """허브 대신: set(키, 값, ttl)과 publish(채널, 메시지)를 모은다."""

    def __init__(self) -> None:
        self.sets: list[tuple[str, dict, float | None]] = []
        self.sent: list[tuple[str, dict]] = []

    async def set(self, key: str, value: dict, ttl: float | None = None) -> None:
        self.sets.append((key, value, ttl))

    async def publish(self, channel: str, msg: dict) -> None:
        self.sent.append((channel, msg))


class BrokenHub(SpyHub):
    async def set(self, key: str, value: dict, ttl: float | None = None) -> None:
        raise ConnectionError("redis down")


class FakeStream:
    """UpbitStream처럼 symbols와 orderbook(symbol)만 가진다."""

    def __init__(self, books: dict[str, OrderbookSnapshot]) -> None:
        self.books = books
        self.symbols = ("KRW-BTC", "KRW-ETH", "KRW-SOL")

    def orderbook(self, symbol: str) -> OrderbookSnapshot | None:
        return self.books.get(symbol)


def _snap(symbol: str, ms: int) -> OrderbookSnapshot:
    return parse_orderbook(json.loads(orderbook(symbol, ms)))


def _engine(hub) -> MarketEngine:
    return MarketEngine(SimpleNamespace(market=Market.UPBIT), None, None, hub=hub)


def test_payload_is_screen_shape_top5():
    """화면 모양 {asks, bids} [[가격, 수량]…], levels[0]이 최우선, 5단계까지."""
    levels = tuple(OrderbookLevel(100.0 + i, 1.0, 99.0 - i, 2.0) for i in range(7))
    snap = OrderbookSnapshot("KRW-BTC", _snap("KRW-BTC", T0_MS).ts, levels)
    p = orderbook_payload(snap)
    assert p["asks"][:2] == [[100.0, 1.0], [101.0, 1.0]]
    assert p["bids"][:2] == [[99.0, 2.0], [98.0, 2.0]]
    assert (len(p["asks"]), len(p["bids"])) == (5, 5)


def test_engine_publishes_changed_books_only():
    """업비트 원본 메시지 → 스냅샷 → 허브 키(TTL 10초)·채널. 같은 스냅샷은 다시 안 보내고, 없는 종목은 건너뛴다."""

    async def go():
        hub = SpyHub()
        eng = _engine(hub)
        stream = FakeStream(
            {"KRW-BTC": _snap("KRW-BTC", T0_MS), "KRW-ETH": _snap("KRW-ETH", T0_MS)}
        )
        await eng.publish_orderbooks(stream)
        # SOL은 아직 호가가 없어 건너뛴다
        assert [k for k, _, _ in hub.sets] == ["ob:upbit:KRW-BTC", "ob:upbit:KRW-ETH"]
        assert {ttl for _, _, ttl in hub.sets} == {ORDERBOOK_TTL}
        ch, msg = hub.sent[0]
        assert ch == "orderbook:upbit:KRW-BTC"
        assert msg["data"] == {
            "asks": [[101.0, 1.5], [102.0, 3.0]],
            "bids": [[100.0, 2.0], [99.0, 4.0]],
        }
        assert msg["ts"].startswith("2026-09-30T00:00:00")  # 09:00 KST = 00:00 UTC

        await eng.publish_orderbooks(stream)
        assert len(hub.sent) == 2  # 그대로면 안 보낸다

        stream.books["KRW-BTC"] = _snap("KRW-BTC", T0_MS + 500)
        await eng.publish_orderbooks(stream)
        assert [ch for ch, _ in hub.sent] == [
            "orderbook:upbit:KRW-BTC",
            "orderbook:upbit:KRW-ETH",
            "orderbook:upbit:KRW-BTC",
        ]

    asyncio.run(go())


def test_memory_hub_book_readable_then_expires_when_feed_stops():
    """실제 MemoryHub: /quotes가 읽는 키(hk.ob)로 바로 읽히고, 호가가 10초 넘게 안 바뀌면 사라진다."""
    from quantpilot.realtime import keys as hk
    from quantpilot.realtime.hub import MemoryHub

    async def go():
        now = [1000.0]
        hub = MemoryHub(monotonic=lambda: now[0])
        eng = _engine(hub)
        stream = FakeStream({"KRW-BTC": _snap("KRW-BTC", T0_MS)})
        await eng.publish_orderbooks(stream)
        assert (await hub.get(hk.ob(Market.UPBIT, "KRW-BTC")))["asks"][0] == [101.0, 1.5]
        now[0] += ORDERBOOK_TTL - 1
        await eng.publish_orderbooks(stream)  # 같은 스냅샷 — 키를 늘리지 않는다
        assert await hub.get(hk.ob(Market.UPBIT, "KRW-BTC")) is not None
        now[0] += 2
        # 시세가 끊긴 호가는 남지 않는다
        assert await hub.get(hk.ob(Market.UPBIT, "KRW-BTC")) is None

    asyncio.run(go())


def test_publish_failure_does_not_raise_and_retries():
    """허브가 실패해도 예외 없이 지나가고, 다음 타이머에 다시 보낸다. 허브가 없으면 아무것도 안 한다."""

    async def go():
        stream = FakeStream({"KRW-BTC": _snap("KRW-BTC", T0_MS)})
        eng = _engine(BrokenHub())
        await eng.publish_orderbooks(stream)
        eng.hub = SpyHub()
        await eng.publish_orderbooks(stream)
        assert [ch for ch, _ in eng.hub.sent] == ["orderbook:upbit:KRW-BTC"]
        await _engine(None).publish_orderbooks(stream)

    asyncio.run(go())
