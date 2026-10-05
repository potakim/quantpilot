"""API가 주입받는 부품 (ADR 0017). 테스트는 SQLite 인메모리 세션·MemoryHub·가짜 LLM을 넣는다.

api 프로세스는 브로커를 들고 있지 않다 — DB·허브만 읽고, 주문은 허브 큐로 엔진에 넘긴다 (01 §2).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request

from quantpilot.api.auth import Auth
from quantpilot.core.models import Market
from quantpilot.core.ports import Hub

log = logging.getLogger(__name__)


class CalibrationSource(Protocol):
    """보정·A/B 리포트 (t13 judgment/calibration.py가 구현). api는 이 인터페이스만 안다."""

    async def calibration(self, weeks: int) -> dict[str, Any]:
        """`{brier, ece, n, buckets, by_provider}` (03 §2.5)."""
        ...

    async def ab(self, weeks: int) -> dict[str, Any]:
        """`{on:{ret, mdd, n_trades}, off:{...}, g2_pass}` (03 §2.5)."""
        ...


class Answerer(Protocol):
    """'이 판단에 대해 물어보기' 답변기. 설명만 한다 — 수량·가격을 묻지도 답하지도 않는다 (불변식 #7)."""

    async def answer(self, question: str, context: dict[str, Any]) -> tuple[str, float]:
        """(답변, 비용USD)."""
        ...


class StubAnswerer:
    """네트워크 없는 답변기. 기록된 게이트·확신도·하드블록만 되풀이한다."""

    async def answer(self, question: str, context: dict[str, Any]) -> tuple[str, float]:
        """기록 요약을 답으로 돌려준다."""
        j = context.get("judgment", {})
        blocks = ", ".join(j.get("blocks") or []) or "없음"
        text = (
            f"(stub) 게이트 {j.get('gate')}, 확신도 {j.get('confidence')}, 하드블록 {blocks}. "
            "실제 설명은 QP_ANTHROPIC_API_KEY 설정 시 Claude가 답한다."
        )
        return text, 0.0


AccountSource = Callable[[Market], Awaitable[Any]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    """앱 상태 (app.state.deps)."""

    settings: Any
    auth: Auth
    hub: Hub
    sessions_factory: Callable[[], Any]
    calibration: CalibrationSource | None = None
    answerer: Answerer = field(default_factory=StubAnswerer)
    account_source: AccountSource | None = None
    notifier: Any = None
    utcnow: Callable[[], datetime] = _utcnow
    ws_idle: float = 30.0
    _sessions: Any = None

    @property
    def sessions(self) -> Any:
        """DB 세션 팩토리 (처음 쓸 때 만든다)."""
        if self._sessions is None:
            self._sessions = self.sessions_factory()
        return self._sessions

    async def account(self, market: Market) -> Any:
        """시장 계좌(포지션·현금·평가액). 기본은 DB의 페이퍼 계좌 복원 (scheduler/backup.py).

        실전 모드(paper=False)는 실계좌가 아직 연결되지 않아(2단계) 503 `LIVE_ACCOUNT_MISSING`으로 알린다 —
        페이퍼 계좌 복원의 안전장치가 그대로 터지면 이유를 알 수 없는 500이 된다.
        """
        if self.account_source is not None:
            return await self.account_source(Market(market))
        from quantpilot.api.errors import ApiError
        from quantpilot.config import settings as global_settings
        from quantpilot.scheduler.backup import restore_account

        if not getattr(self.settings, "paper", True) or not global_settings.paper:
            raise ApiError(
                503,
                "LIVE_ACCOUNT_MISSING",
                "실전 계좌가 아직 연결되지 않았습니다 (실계좌 연결은 2단계 — 지금은 페이퍼 모드로 운영)",
            )
        return await restore_account(self.sessions, Market(market))


def get_deps(request: Request) -> Deps:
    """FastAPI 의존성: 앱의 Deps."""
    return request.app.state.deps


DepsDep = Annotated[Deps, Depends(get_deps)]
