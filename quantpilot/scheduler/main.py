"""scheduler 엔트리포인트 — APScheduler(AsyncIOScheduler) + SQLAlchemyJobStore (04 §8, 01 §2).

실행: `python -m quantpilot.scheduler.main` — 페이퍼 전용 (settings.paper=True가 아니면 거부).
APScheduler는 `infra` extra에만 있다. 코어·테스트는 이 모듈을 import하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from quantpilot.config import settings
from quantpilot.core.models import Market
from quantpilot.db.repo import (
    Sessions,
    SqlCandleRepo,
    SqlConfigRepo,
    SqlJudgmentRepo,
    SqlLedger,
    SqlOpsRepo,
)
from quantpilot.engine.link import SettingsEngineLink
from quantpilot.scheduler.backup import account_source, backup_factory
from quantpilot.scheduler.context import JobContext
from quantpilot.scheduler.registry import install, register

log = logging.getLogger(__name__)


def build_context(sessions: Sessions, markets: tuple[Market, ...] = (Market.UPBIT,)) -> JobContext:
    """DB 기반 JobContext. 뉴스 수집기·리뷰 모델은 부품이 준비되면 여기서 붙인다."""
    config = SqlConfigRepo(sessions)
    return JobContext(
        link=SettingsEngineLink(config),
        backup=backup_factory(sessions),
        markets=markets,
        candles=SqlCandleRepo(sessions),
        judgments=SqlJudgmentRepo(sessions),
        config=config,
        ops=SqlOpsRepo(sessions),
        ledger=SqlLedger(sessions),
        account=account_source(sessions),
    )


def sync_url(url: str) -> str:
    """SQLAlchemyJobStore는 동기 엔진을 쓴다: async 드라이버 이름을 뗀다."""
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def make_scheduler() -> Any:
    """AsyncIOScheduler. 잡 저장소를 못 만들면 메모리 저장소로 (시작 때 잡을 다시 등록하므로 동작은 같다)."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as e:  # pragma: no cover — infra extra 필요
        raise RuntimeError('APScheduler 필요: uv pip install -e ".[infra]"') from e
    try:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        stores = {"default": SQLAlchemyJobStore(url=sync_url(settings.db_url))}
    except Exception as e:  # noqa: BLE001 — 동기 드라이버 미설치 등. URL(비밀번호)은 남기지 않는다
        log.warning(
            "SQLAlchemyJobStore 불가, 메모리 저장소 사용", extra={"error": type(e).__name__}
        )
        stores = {}
    return AsyncIOScheduler(jobstores=stores, timezone="UTC")


async def run() -> None:
    """잡을 등록하고 영원히 돈다."""
    from quantpilot.db.session import make_sessions

    if not settings.paper:
        raise RuntimeError("scheduler는 settings.paper=True에서만 (실계좌 배선 전)")
    install(build_context(make_sessions(settings.db_url)))
    scheduler = make_scheduler()
    ids = register(scheduler)
    scheduler.start()
    log.info("scheduler started", extra={"jobs": len(ids)})
    await asyncio.Event().wait()


def main() -> None:
    """scheduler 실행."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
