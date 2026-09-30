"""실시간 허브 구현 (core.ports.Hub, 02 §2, ADR 0017).

- `MemoryHub`: 한 프로세스 안의 pub/sub·상태·큐. 테스트와 Redis 없는 개발 환경용.
- `RedisHub`: redis.asyncio. 채널은 `ch:<name>`으로 발행하고 `ch:*`를 패턴 구독한다. 키 이름은 02 §2 그대로.
- `make_hub(settings)`: `QP_REDIS_URL`이 있고 redis가 설치돼 있으면 RedisHub, 아니면 MemoryHub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

CHANNEL_PREFIX = "ch:"


def to_jsonable(obj: Any) -> Any:
    """dataclass·Enum·datetime을 JSON으로 보낼 수 있는 값으로 바꾼다."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float | int | str | bool) or obj is None:
        return obj
    return str(obj)


class MemoryHub:
    """프로세스 내 허브. 구독자마다 asyncio.Queue를 둔다 (느린 구독자는 maxsize에서 오래된 것을 버린다)."""

    def __init__(
        self, *, maxsize: int = 1000, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._subs: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()
        self._kv: dict[str, tuple[Any, float | None]] = {}
        self._queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self.maxsize = maxsize
        self._monotonic = monotonic

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        """구독자 전원에게 넣는다."""
        msg = (channel, to_jsonable(data))
        for q in list(self._subs):
            if q.full():
                q.get_nowait()
            q.put_nowait(msg)

    async def listen(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """구독을 열고 메시지를 차례로 내준다. 이터레이터가 닫히면 구독도 닫힌다."""
        q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(self.maxsize)
        self._subs.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.discard(q)

    @property
    def subscribers(self) -> int:
        """열린 구독 수."""
        return len(self._subs)

    async def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        """상태 키를 쓴다 (None이면 삭제)."""
        if value is None:
            self._kv.pop(key, None)
            return
        exp = None if ttl is None else self._monotonic() + ttl
        self._kv[key] = (to_jsonable(value), exp)

    async def get(self, key: str) -> Any:
        """상태 키를 읽는다."""
        hit = self._kv.get(key)
        if hit is None:
            return None
        value, exp = hit
        if exp is not None and self._monotonic() >= exp:
            self._kv.pop(key, None)
            return None
        return value

    async def push(self, queue: str, item: dict[str, Any]) -> None:
        """큐에 넣는다."""
        self._queues[queue].append(to_jsonable(item))

    async def pop(self, queue: str) -> dict[str, Any] | None:
        """큐에서 꺼낸다."""
        q = self._queues.get(queue)
        return q.popleft() if q else None

    async def qlen(self, queue: str) -> int:
        """큐 길이."""
        return len(self._queues.get(queue, ()))


class RedisHub:
    """Redis 구현. redis 패키지(infra extra)가 없으면 만들 수 없다."""

    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as e:  # pragma: no cover - infra extra 미설치
            raise RuntimeError("RedisHub에는 redis 패키지가 필요하다 (pip install .[infra])") from e
        self._r = aioredis.from_url(url, decode_responses=True)

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        """`ch:<channel>`에 JSON으로 발행."""
        await self._r.publish(CHANNEL_PREFIX + channel, json.dumps(to_jsonable(data)))

    async def listen(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """`ch:*` 패턴 구독."""
        ps = self._r.pubsub()
        await ps.psubscribe(CHANNEL_PREFIX + "*")
        try:
            async for m in ps.listen():
                if m.get("type") != "pmessage":
                    continue
                try:
                    data = json.loads(m["data"])
                except (TypeError, ValueError):
                    continue
                yield str(m["channel"])[len(CHANNEL_PREFIX) :], data
        finally:
            await ps.aclose()

    async def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        """상태 키를 쓴다 (None이면 삭제)."""
        if value is None:
            await self._r.delete(key)
            return
        ex = None if ttl is None else max(1, int(ttl))
        await self._r.set(key, json.dumps(to_jsonable(value)), ex=ex)

    async def get(self, key: str) -> Any:
        """상태 키를 읽는다."""
        raw = await self._r.get(key)
        return None if raw is None else json.loads(raw)

    async def push(self, queue: str, item: dict[str, Any]) -> None:
        """큐 끝에 넣는다 (RPUSH)."""
        await self._r.rpush(queue, json.dumps(to_jsonable(item)))

    async def pop(self, queue: str) -> dict[str, Any] | None:
        """큐 앞에서 꺼낸다 (LPOP)."""
        raw = await self._r.lpop(queue)
        return None if raw is None else json.loads(raw)


def make_hub(settings: Any) -> MemoryHub | RedisHub:
    """설정에 맞는 허브. Redis URL이 없거나 redis 미설치면 MemoryHub(프로세스 사이 공유 안 됨)."""
    url = getattr(settings, "redis_url", "")
    if url:
        try:
            return RedisHub(url)
        except RuntimeError:
            log.warning("redis 미설치 — MemoryHub로 대체 (프로세스 사이 실시간 공유 없음)")
    return MemoryHub()
