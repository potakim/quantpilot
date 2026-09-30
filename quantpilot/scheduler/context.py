"""잡이 공유하는 의존성 묶음. main이 만들어 `registry.install`로 넣고, 테스트는 가짜를 넣는다."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from quantpilot.core.models import Market
from quantpilot.core.ports import EngineLink, EventBus
from quantpilot.engine.tick import TickRunner

log = logging.getLogger(__name__)

# 하트비트가 이보다 오래되면 엔진이 죽었다고 본다 (04 §8)
STALE_AFTER = timedelta(seconds=90)

BackupFactory = Callable[[Market, str], Awaitable[TickRunner]]
AccountSource = Callable[[Market], Awaitable["tuple[float, float] | None"]]
Reviewer = Callable[[dict[str, Any]], Awaitable["tuple[str, float | None]"]]
Hook = Callable[["JobContext"], Awaitable[None]]


class Notifier(Protocol):
    """알림 (P1-10 텔레그램 전까지 로그)."""

    async def send(self, level: str, text: str) -> None:
        """level: info·warning·critical."""
        ...


class LogNotifier:
    """알림을 로그로만 남긴다."""

    async def send(self, level: str, text: str) -> None:
        """level에 맞는 로그 레벨로 남긴다."""
        log.log(logging.getLevelName(level.upper()), text, extra={"notify": level})


class LogBus:
    """Redis 허브(P1-12) 전까지 이벤트를 로그로만 남긴다."""

    async def publish(self, topic: str, event: object) -> None:
        """이벤트 1건을 info 로그로."""
        log.info("event", extra={"topic": topic, "event": event})


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class JobContext:
    """잡 실행에 필요한 부품. 없는 부품(None)을 쓰는 잡은 건너뛴다."""

    link: EngineLink
    backup: BackupFactory
    markets: tuple[Market, ...] = (Market.UPBIT,)
    notifier: Notifier = field(default_factory=LogNotifier)
    bus: EventBus = field(default_factory=LogBus)
    utcnow: Callable[[], datetime] = _utcnow
    candles: Any = None  # CandleRepo
    judgments: Any = None  # SqlJudgmentRepo (pending_realized·set_realized)
    config: Any = None  # ConfigRepo
    ops: Any = None  # SqlOpsRepo
    ledger: Any = None  # Ledger
    account: AccountSource | None = None  # 시장별 (현금, 평가액)
    news: Any = None  # data.news.NewsCollector
    reviewer: Reviewer | None = None  # 일일 리뷰 요약 (Claude, P1-08 이후)
    hooks: dict[str, Hook] = field(default_factory=dict)  # 아직 부품이 없는 잡의 본문
    stale_after: timedelta = STALE_AFTER
    backup_armed: set[Market] = field(default_factory=set)


async def run_hook(ctx: JobContext, name: str) -> None:
    """부품이 아직 없는 잡: 연결된 본문이 있으면 부르고, 없으면 건너뛴다고 남긴다."""
    hook = ctx.hooks.get(name)
    if hook is None:
        log.info("job skipped: not wired", extra={"job": name})
        return
    await hook(ctx)
