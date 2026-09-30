"""API 테스트 공용 도구 (P1-12): SQLite 인메모리 세션·MemoryHub·설정·앱."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.config import Settings
from quantpilot.db.models import Base

SECRET = "jwt-secret-for-tests-0123456789abcdef-XYZ"
PASSWORD = "admin-pw-7f3kq"
API = "/api/v1"


def make_settings(tmp: Path, **kw: Any) -> Settings:
    """테스트 설정: .env·keys.env를 읽지 않고 데이터 디렉터리는 tmp."""
    base = {
        "data_dir": tmp,
        "jwt_secret": SECRET,
        "admin_password": PASSWORD,
        "keys_file": tmp / "keys.env",
        "redis_url": "",
    }
    return Settings(_env_file=None, **{**base, **kw})


async def memory_sessions() -> tuple[Any, Any]:
    """(engine, 세션 팩토리) — 외래 키 강제 SQLite 인메모리."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute("pragma foreign_keys=on")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
