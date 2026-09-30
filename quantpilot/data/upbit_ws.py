"""업비트 public 웹소켓 (04 §6, 01 §4.1). 체결(trade)·호가(orderbook)를 받아 TradeEvent를 발행한다.

- 키 불필요(public). 인증 헤더·QP_* 키를 쓰지 않는다
- 끊기면 `backoff` 순서대로 기다렸다 재접속, 연속 실패가 그 길이를 넘으면 `DataStale`로 멈춘다
  (엔진이 REST 폴링으로 넘어가거나 신규 진입을 멈춘다 — 01 §5)
- `stale_after`초 동안 메시지가 하나도 없으면 연결을 버리고 재접속한다 (01 §5: 30초)
- 시각은 거래소 체결 시각(ms, UTC)을 core/clock으로 현지 tz-naive로 바꾼다
- 웹소켓 구현(`websockets`)은 이 어댑터 안에서만 import한다. 테스트는 `connect`를 주입한다
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from quantpilot.core.clock import to_local
from quantpilot.core.errors import DataStale
from quantpilot.core.events import TradeEvent
from quantpilot.core.models import Market, Side

try:
    from websockets.exceptions import WebSocketException
except ImportError:  # pragma: no cover - websockets 없는 설치

    class WebSocketException(Exception):  # type: ignore[no-redef]
        """websockets가 없을 때 자리만 채운다 (주입된 connect만 쓰는 경우)."""


log = logging.getLogger(__name__)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
# 01 §5: 재접속 최대 5회. 거래소 제한(5 conn/s)보다 충분히 느리다
DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 5.0, 5.0, 5.0)
STALE_AFTER = 30.0
# 끊김·접속 실패로 보고 재접속하는 예외 (ConnectionClosed·InvalidStatus 등은 WebSocketException)
_CONNECTION_ERRORS = (OSError, EOFError, asyncio.IncompleteReadError, WebSocketException)


class WsConnection(Protocol):
    """이 모듈이 쓰는 웹소켓 연결의 최소 인터페이스 (`websockets` 연결과 같은 모양)."""

    async def send(self, message: str) -> None:
        """문자열 프레임 1개를 보낸다."""
        ...

    async def recv(self) -> str | bytes:
        """프레임 1개를 받는다. 연결이 닫히면 예외."""
        ...


Connect = Callable[[str], AbstractAsyncContextManager[WsConnection]]
TradeHandler = Callable[[TradeEvent], Awaitable[None] | None]


@dataclass(frozen=True)
class OrderbookLevel:
    """호가 한 단계."""

    ask_price: float
    ask_size: float
    bid_price: float
    bid_size: float


@dataclass(frozen=True)
class OrderbookSnapshot:
    """심볼 하나의 최신 호가. levels[0]이 최우선 호가."""

    symbol: str
    ts: datetime  # 현지 tz-naive
    levels: tuple[OrderbookLevel, ...]

    @property
    def best_ask(self) -> float | None:
        """최우선 매도호가."""
        return self.levels[0].ask_price if self.levels else None

    @property
    def best_bid(self) -> float | None:
        """최우선 매수호가."""
        return self.levels[0].bid_price if self.levels else None


def _ms_to_local(ms: float) -> datetime:
    return to_local(datetime.fromtimestamp(ms / 1000, UTC), Market.UPBIT)


def parse_trade(msg: dict[str, Any]) -> TradeEvent:
    """업비트 trade 메시지(DEFAULT 포맷) → TradeEvent. ask_bid: BID=매수 주도."""
    ask_bid = msg.get("ask_bid")
    side = {"BID": Side.BUY, "ASK": Side.SELL}.get(ask_bid) if ask_bid else None
    return TradeEvent(
        market=Market.UPBIT,
        symbol=msg["code"],
        ts=_ms_to_local(msg["trade_timestamp"]),
        price=float(msg["trade_price"]),
        qty=float(msg["trade_volume"]),
        side=side,
    )


def parse_orderbook(msg: dict[str, Any]) -> OrderbookSnapshot:
    """업비트 orderbook 메시지(DEFAULT 포맷) → OrderbookSnapshot."""
    levels = tuple(
        OrderbookLevel(
            ask_price=float(u["ask_price"]),
            ask_size=float(u["ask_size"]),
            bid_price=float(u["bid_price"]),
            bid_size=float(u["bid_size"]),
        )
        for u in msg.get("orderbook_units", [])
    )
    return OrderbookSnapshot(symbol=msg["code"], ts=_ms_to_local(msg["timestamp"]), levels=levels)


def subscribe_message(symbols: Sequence[str], *, orderbook: bool = True) -> str:
    """구독 요청 프레임. ticket은 연결마다 새로 만든다."""
    req: list[dict[str, Any]] = [
        {"ticket": uuid.uuid4().hex},
        {"type": "trade", "codes": list(symbols)},
    ]
    if orderbook:
        req.append({"type": "orderbook", "codes": list(symbols)})
    req.append({"format": "DEFAULT"})
    return json.dumps(req)


@asynccontextmanager
async def _websockets_connect(url: str) -> AsyncIterator[WsConnection]:
    try:
        import websockets
    except ImportError as e:  # pragma: no cover - 설치 환경에 따라
        raise ImportError("pip install 'quantpilot[data]'  (websockets)") from e
    async with websockets.connect(url, ping_interval=20, max_size=2**20) as ws:
        yield ws


class UpbitStream:
    """업비트 public WS 구독기. `run()`이 끝날 때까지 체결을 `on_trade`로 넘긴다."""

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        on_trade: TradeHandler | None = None,
        orderbook: bool = True,
        connect: Connect | None = None,
        url: str = UPBIT_WS_URL,
        backoff: Sequence[float] = DEFAULT_BACKOFF,
        stale_after: float = STALE_AFTER,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not symbols:
            raise ValueError("구독할 심볼이 없다")
        self.symbols = tuple(symbols)
        self.on_trade = on_trade
        self.orderbook_enabled = orderbook
        self.url = url
        self.backoff = tuple(backoff)
        self.stale_after = stale_after
        self._connect = connect or _websockets_connect
        self._monotonic = monotonic
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
        self._last_msg: float | None = None
        self._stop = asyncio.Event()
        self.reconnects = 0

    # ── 조회 ─────────────────────────────────────────────
    def orderbook(self, symbol: str) -> OrderbookSnapshot | None:
        """심볼의 최신 호가 (아직 없으면 None)."""
        return self._orderbooks.get(symbol)

    def seconds_since_last_message(self) -> float | None:
        """마지막 메시지 이후 경과 초. 한 번도 못 받았으면 None."""
        return None if self._last_msg is None else self._monotonic() - self._last_msg

    def ensure_fresh(self) -> None:
        """stale_after초 넘게 시세가 없으면 DataStale. 엔진이 신규 진입 전에 부른다."""
        age = self.seconds_since_last_message()
        if age is None or age > self.stale_after:
            raise DataStale(f"업비트 시세 {'없음' if age is None else f'{age:.1f}초 무응답'}")

    def stop(self) -> None:
        """run()을 멈춘다 (현재 연결을 닫고 돌아온다)."""
        self._stop.set()

    # ── 수신 루프 ─────────────────────────────────────────
    async def run(self) -> None:
        """연결·구독·수신을 반복한다. 재접속을 다 쓰면 DataStale을 던진다."""
        failures = 0
        while not self._stop.is_set():
            got_any = False
            try:
                async with self._connect(self.url) as ws:
                    await ws.send(subscribe_message(self.symbols, orderbook=self.orderbook_enabled))
                    log.info("업비트 WS 구독", extra={"symbols": list(self.symbols)})
                    async for msg in self._messages(ws):
                        got_any = True
                        failures = 0
                        await self._dispatch(msg)
            except DataStale as e:
                log.warning("업비트 WS 무응답, 재접속", extra={"reason": str(e)})
            except _CONNECTION_ERRORS as e:
                log.warning("업비트 WS 끊김·연결 실패", extra={"error": type(e).__name__})
            if self._stop.is_set():
                break
            if not got_any:
                failures += 1
            if failures > len(self.backoff):
                raise DataStale(f"업비트 WS 재접속 {len(self.backoff)}회 실패")
            delay = self.backoff[max(failures - 1, 0)] if self.backoff else 0.0
            self.reconnects += 1
            log.info("업비트 WS 재접속 대기", extra={"delay": delay, "attempt": failures})
            await self._sleep_or_stop(delay)

    async def _messages(self, ws: WsConnection) -> AsyncIterator[dict[str, Any]]:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.stale_after)
            except TimeoutError as e:
                raise DataStale(f"{self.stale_after:.0f}초 동안 메시지 없음") from e
            self._last_msg = self._monotonic()
            try:
                msg = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                log.warning("업비트 WS 메시지 해석 실패")
                continue
            if isinstance(msg, dict):
                yield msg

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "trade":
            ev = parse_trade(msg)
            if self.on_trade is not None:
                # 소비자 쪽 버그를 연결 끊김으로 착각해 재접속하지 않도록 여기서 삼킨다
                try:
                    res = self.on_trade(ev)
                    if res is not None:
                        await res
                except Exception:
                    log.exception("체결 처리 실패", extra={"symbol": ev.symbol})
        elif kind == "orderbook":
            ob = parse_orderbook(msg)
            self._orderbooks[ob.symbol] = ob
        elif "error" in msg:
            log.error("업비트 WS 오류 응답", extra={"error": msg["error"]})

    async def _sleep_or_stop(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            pass
