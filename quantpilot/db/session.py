"""DB 세션 팩토리. engine·scheduler·api 엔트리포인트가 settings.db_url로 만든다."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_sessions(url: str) -> async_sessionmaker[AsyncSession]:
    """비동기 세션 팩토리를 만든다."""
    engine = create_async_engine(url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)
