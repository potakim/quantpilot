"""FastAPI 앱 (03 문서, 1단계 P1-12). 0단계 인메모리 라우트는 없앴다 — 상태는 DB·허브에서만 읽는다.

- REST: `/api/v1/*` (routes/{system,strategies,backtests,trading,judgments,reports}.py), JWT 필수
- WS: `/api/v1/ws` (ws.py). `/health`는 인증 없이 루트에도 둔다 (07 배포 확인용)
- `create_app(...)`에 세션·허브·보정·답변기·계좌를 주입한다. 모듈의 `app`은 설정대로 만든 기본 앱

실행: `qp serve` 또는 `uvicorn quantpilot.api.app:app`
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI

from quantpilot import __version__
from quantpilot.api import errors
from quantpilot.api.auth import Auth
from quantpilot.api.deps import AccountSource, Answerer, CalibrationSource, Deps, StubAnswerer
from quantpilot.api.routes import backtests, judgments, reports, strategies, system, trading
from quantpilot.api.ws import WsHub
from quantpilot.api.ws import router as ws_router
from quantpilot.core.ports import Hub

log = logging.getLogger(__name__)

PREFIX = "/api/v1"


def default_answerer(settings: Any) -> Answerer:
    """키가 있고 anthropic이 설치돼 있으면 Claude, 아니면 스텁."""
    if getattr(settings, "anthropic_api_key", ""):
        try:
            from quantpilot.judgment.anthropic import ClaudeAnswerer

            return ClaudeAnswerer(api_key=settings.anthropic_api_key)
        except (ImportError, ValueError):
            log.warning("ClaudeAnswerer 사용 불가 — StubAnswerer로 대체")
    return StubAnswerer()


def create_app(
    *,
    settings: Any = None,
    sessions: Any = None,
    hub: Hub | None = None,
    calibration: CalibrationSource | None = None,
    answerer: Answerer | None = None,
    account_source: AccountSource | None = None,
    notifier: Any = None,
    utcnow: Callable[[], datetime] | None = None,
    ws_idle: float = 30.0,
) -> FastAPI:
    """API 앱을 만든다. 빠진 부품은 설정으로 만든다(세션은 처음 쓸 때)."""
    if settings is None:
        from quantpilot.config import settings as default_settings

        settings = default_settings
    if hub is None:
        from quantpilot.realtime.hub import make_hub

        hub = make_hub(settings)

    def sessions_factory() -> Any:
        if sessions is not None:
            return sessions
        from quantpilot.db.session import make_sessions

        return make_sessions(settings.db_url)

    auth_kw: dict[str, Any] = {"ttl": timedelta(hours=settings.jwt_ttl_hours)}
    if utcnow is not None:
        auth_kw["utcnow"] = utcnow
    deps = Deps(
        settings=settings,
        auth=Auth(settings.jwt_secret, settings.admin_password, **auth_kw),
        hub=hub,
        sessions_factory=sessions_factory,
        calibration=calibration,
        answerer=answerer or default_answerer(settings),
        account_source=account_source,
        notifier=notifier,
        ws_idle=ws_idle,
    )
    if utcnow is not None:
        deps.utcnow = utcnow

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.wshub.stop()
        for t in list(app.state.tasks):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        app.state.backtest_pool.shutdown(wait=False)

    app = FastAPI(
        title="QuantPilot",
        version=__version__,
        description="코드가 계산하고, 모델은 판단하고, 코드가 실행한다",
        lifespan=lifespan,
    )
    app.state.deps = deps
    app.state.wshub = WsHub(hub)
    app.state.tasks = set()
    app.state.backtest_pool = ThreadPoolExecutor(1, thread_name_prefix="backtest")
    errors.install(app)
    app.add_api_route("/health", system.health, methods=["GET"])  # 배포 확인용 (07)
    app.include_router(system.public, prefix=PREFIX)
    for r in (
        system.router,
        strategies.router,
        backtests.router,
        trading.router,
        judgments.router,
        reports.router,
        ws_router,
    ):
        app.include_router(r, prefix=PREFIX)
    return app


app = create_app()
