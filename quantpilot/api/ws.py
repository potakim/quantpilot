"""WebSocket 허브 `/ws` (03 §3, ADR 0017 §2).

- 첫 메시지 `{"auth": "<JWT>", "subscribe": [...]}`로 인증. 실패하면 4401로 닫는다.
- 허브(Redis pub/sub 또는 MemoryHub)를 프로세스당 한 번 구독하고, 연결마다 구독한 채널만 골라 보낸다.
- 클라이언트 → 서버: subscribe·unsubscribe·ping. `ws_idle`초(기본 30) 동안 아무것도 안 오면 4408로 닫는다.
- 서버 → 클라이언트: `{"ch", "ts", "data"}`. 느린 연결은 큐가 차면 오래된 메시지부터 버린다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from quantpilot.api.errors import ApiError
from quantpilot.core.ports import Hub

log = logging.getLogger(__name__)

router = APIRouter()

CLOSE_UNAUTHORIZED = 4401
CLOSE_IDLE = 4408
QUEUE_MAX = 500
MAX_SUBSCRIPTIONS = 100


class Connection:
    """연결 하나: 구독 채널 + 보낼 메시지 큐."""

    def __init__(self, subs: set[str]) -> None:
        self.subs = subs
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_MAX)

    def offer(self, msg: dict[str, Any]) -> None:
        """큐에 넣는다. 가득 차면 가장 오래된 것을 버린다."""
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(msg)


class WsHub:
    """허브 구독 1개 → 연결 N개 팬아웃."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.connections: set[Connection] = set()
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def ensure_started(self) -> None:
        """팬아웃 태스크를 (한 번) 띄우고 허브 구독이 열릴 때까지 기다린다."""
        if self._task is None or self._task.done():
            self._ready = asyncio.Event()
            self._task = asyncio.create_task(self._pump())
        await self._ready.wait()

    async def _pump(self) -> None:
        it = self.hub.listen().__aiter__()
        nxt = asyncio.ensure_future(it.__anext__())
        await asyncio.sleep(0)  # 구독 등록까지 진행시킨다
        self._ready.set()
        try:
            while True:
                ch, msg = await nxt
                nxt = asyncio.ensure_future(it.__anext__())
                self.fanout(ch, msg)
        except asyncio.CancelledError:
            nxt.cancel()
            raise
        finally:
            with contextlib.suppress(Exception):
                await it.aclose()

    def fanout(self, ch: str, msg: dict[str, Any]) -> int:
        """ch를 구독한 연결에 보낸다. 보낸 연결 수."""
        out = {"ch": ch, "ts": msg.get("ts"), "data": msg.get("data", msg)}
        n = 0
        for c in list(self.connections):
            if ch in c.subs:
                c.offer(out)
                n += 1
        return n

    async def stop(self) -> None:
        """팬아웃 태스크를 멈춘다."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _channels(value: Any) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError("채널 목록은 문자열 배열")
    if len(value) > MAX_SUBSCRIPTIONS:
        raise ValueError("구독 채널이 너무 많다")
    return set(value)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """인증 → 구독 → 팬아웃."""
    deps = ws.app.state.deps
    wshub: WsHub = ws.app.state.wshub
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=deps.ws_idle)
        if not isinstance(first, dict):
            raise ValueError("첫 메시지는 객체")  # noqa: TRY004 — 프로토콜 오류로 한데 묶는다
        deps.auth.verify(str(first.get("auth") or ""))
        subs = _channels(first.get("subscribe", []))
    except (TimeoutError, ValueError, ApiError, WebSocketDisconnect) as e:
        log.info("ws auth failed", extra={"error": type(e).__name__})
        with contextlib.suppress(Exception):
            await ws.send_json({"ch": "error", "ts": _now(), "data": {"code": "UNAUTHORIZED"}})
            await ws.close(CLOSE_UNAUTHORIZED)
        return

    conn = Connection(subs)
    await wshub.ensure_started()
    wshub.connections.add(conn)
    conn.offer({"ch": "system", "ts": _now(), "data": {"subscribed": sorted(conn.subs)}})

    async def writer() -> None:
        while True:
            await ws.send_json(await conn.queue.get())

    wtask = asyncio.create_task(writer())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=deps.ws_idle)
            except TimeoutError:
                await ws.close(CLOSE_IDLE)
                return
            except ValueError:
                conn.offer({"ch": "error", "ts": _now(), "data": {"message": "JSON 형식 오류"}})
                continue
            try:
                if not isinstance(msg, dict):
                    raise ValueError("메시지는 객체")  # noqa: TRY004 — 프로토콜 오류로 한데 묶는다
                if "subscribe" in msg:
                    conn.subs |= _channels(msg["subscribe"])
                if "unsubscribe" in msg:
                    conn.subs -= _channels(msg["unsubscribe"])
                if "ping" in msg:
                    conn.offer({"ch": "pong", "ts": _now(), "data": {"pong": msg["ping"]}})
                elif "subscribe" in msg or "unsubscribe" in msg:
                    data = {"subscribed": sorted(conn.subs)}
                    conn.offer({"ch": "system", "ts": _now(), "data": data})
            except ValueError as e:
                conn.offer({"ch": "error", "ts": _now(), "data": {"message": str(e)}})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        wshub.connections.discard(conn)
        wtask.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await wtask
