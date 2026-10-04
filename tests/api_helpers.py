"""API 테스트 공용 도구 (P1-12): SQLite 세션·MemoryHub·설정·앱."""

from __future__ import annotations

import shutil
import tempfile
import weakref
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
    """(engine, 세션 팩토리) — 부를 때마다 새 SQLite 파일, 세션마다 연결이 따로다 (외래 키 강제).

    이름은 예전 인메모리 시절 그대로다. 인메모리 DB는 연결 1개(StaticPool)를 모든 세션이 같이 써서,
    한 세션이 반납될 때의 rollback이 다른 세션의 커밋 전 UPDATE를 지웠다 (운영 PostgreSQL은 연결이 따로라 없는 일).
    """
    folder = tempfile.mkdtemp(prefix="qp-test-db-")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(folder, 'qp.db').as_posix()}",
        poolclass=NullPool,
        connect_args={"timeout": 30},
    )
    weakref.finalize(engine, shutil.rmtree, folder, ignore_errors=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute("pragma foreign_keys=on")
        dbapi_conn.execute("pragma journal_mode=wal")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
