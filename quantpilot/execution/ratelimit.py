"""거래소 API 호출 한도 (04 §5.2 2단계, ADR 0011 §8).

그룹(예: ``upbit:order``)마다 슬라이딩 윈도로 거래소 한도의 80%만 쓴다.
- SlidingWindowLimiter: 한 프로세스 안에서 쓰는 기본 구현
- RedisSlidingWindowLimiter: 여러 프로세스가 같은 계좌를 쓸 때. redis 클라이언트를 주입받는다(여기서 import 안 함)
- NoLimiter: 페이퍼 등 한도가 없는 곳
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

# 그룹 → (거래소 한도, 윈도 초). 04 §5.1 표
EXCHANGE_LIMITS: dict[str, tuple[int, float]] = {
    "upbit:order": (12, 1.0),
    "upbit:query": (30, 1.0),
    "kis:order": (20, 1.0),
    "kis_paper:order": (2, 1.0),
    "alpaca": (200, 60.0),
}
SAFETY = 0.8

Sleep = Callable[[float], Awaitable[None]]


def budget(group: str, limits: dict[str, tuple[int, float]] | None = None) -> tuple[int, float]:
    """그룹이 윈도 안에 쓸 수 있는 호출 수(한도의 80%, 내림, 최소 1)와 윈도 길이."""
    limit, window = (limits or EXCHANGE_LIMITS)[group]
    return max(1, math.floor(limit * SAFETY)), window


class RateLimiter(Protocol):
    """호출 전에 한도 여유가 생길 때까지 기다린다."""

    async def acquire(self, group: str) -> None:
        """group의 호출 1건 자리를 잡는다."""
        ...


class NoLimiter:
    """한도 없음 (페이퍼·테스트)."""

    async def acquire(self, group: str) -> None:
        """바로 통과."""


class SlidingWindowLimiter:
    """프로세스 내 슬라이딩 윈도. clock·sleep을 주입해 테스트에서 시간을 돌린다."""

    def __init__(
        self,
        limits: dict[str, tuple[int, float]] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.limits = limits or EXCHANGE_LIMITS
        self._clock = clock
        self._sleep = sleep
        self._calls: dict[str, deque[float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, group: str) -> None:
        """윈도 안 호출 수가 예산 미만이 될 때까지 기다린 뒤 기록한다."""
        cap, window = budget(group, self.limits)
        q = self._calls.setdefault(group, deque())
        async with self._locks.setdefault(group, asyncio.Lock()):  # 그룹끼리는 서로 막지 않는다
            while True:
                now = self._clock()
                while q and now - q[0] >= window:
                    q.popleft()
                if len(q) < cap:
                    q.append(now)
                    return
                await self._sleep(q[0] + window - now)


class RedisSlidingWindowLimiter:
    """Redis 정렬 집합으로 여러 프로세스가 한도를 나눠 쓴다. 클라이언트는 redis.asyncio 호환 객체."""

    def __init__(
        self,
        redis: Any,
        limits: dict[str, tuple[int, float]] | None = None,
        *,
        prefix: str = "qp:rl:",
        clock: Callable[[], float] = time.time,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.redis = redis
        self.limits = limits or EXCHANGE_LIMITS
        self.prefix = prefix
        self._clock = clock
        self._sleep = sleep

    async def acquire(self, group: str) -> None:
        """먼저 자리를 넣고 세어 본 뒤, 넘치면 빼고 기다린다(확인-후-추가 경쟁 없음)."""
        cap, window = budget(group, self.limits)
        key = self.prefix + group
        while True:
            now = self._clock()
            member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
            await self.redis.zremrangebyscore(key, 0, now - window)
            await self.redis.zadd(key, {member: now})
            if await self.redis.zcard(key) <= cap:
                await self.redis.expire(key, math.ceil(window) + 1)
                return
            await self.redis.zrem(key, member)
            oldest = await self.redis.zrange(key, 0, 0, withscores=True)
            wait = (oldest[0][1] + window - now) if oldest else window
            await self._sleep(max(wait, 0.001))
