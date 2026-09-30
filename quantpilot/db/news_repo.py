"""core.news.NewsRepo의 SQLAlchemy(async) 구현 — news_items 테이블 (02 §1.1).

뉴스 시각은 원래 UTC라 변환 없이 저장한다. SQLite가 tz를 잃어 naive로 돌아오면 UTC로 본다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantpilot.core.news import NewsItem
from quantpilot.db.models import NewsItemRow

Sessions = async_sessionmaker[AsyncSession]


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def news_to_row(item: NewsItem) -> NewsItemRow:
    """NewsItem → news_items 행. 본문은 저장하지 않는다(요약만)."""
    return NewsItemRow(
        ts=_utc(item.ts),
        source=item.source,
        symbols=list(item.symbols),
        title=item.title,
        url=item.url,
        summary=item.summary,
        risk_flags=list(item.risk_flags),
        risk_score=item.risk_score,
        raw_hash=item.raw_hash,
    )


def row_to_news(row: NewsItemRow) -> NewsItem:
    """news_items 행 → NewsItem."""
    return NewsItem(
        ts=_utc(row.ts),
        source=row.source,
        title=row.title,
        url=row.url,
        symbols=list(row.symbols or []),
        summary=row.summary,
        risk_flags=list(row.risk_flags or []),
        risk_score=row.risk_score,
        raw_hash=row.raw_hash or "",
    )


class SqlNewsRepo:
    """news_items 저장소."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def known_hashes(self, hashes: Sequence[str]) -> set[str]:
        """이미 저장된 raw_hash."""
        if not hashes:
            return set()
        async with self._sessions() as s:
            q = select(NewsItemRow.raw_hash).where(NewsItemRow.raw_hash.in_(list(hashes)))
            return {h for h in (await s.scalars(q)) if h}

    async def add(self, items: Sequence[NewsItem]) -> int:
        """raw_hash가 새로운 항목만 넣는다 (배치 안 중복도 한 번만)."""
        known = await self.known_hashes([i.raw_hash for i in items])
        rows: dict[str, NewsItemRow] = {}
        for item in items:
            if item.raw_hash not in known and item.raw_hash not in rows:
                rows[item.raw_hash] = news_to_row(item)
        if rows:
            async with self._sessions.begin() as s:
                s.add_all(rows.values())
        return len(rows)

    async def recent(self, since: datetime) -> list[NewsItem]:
        """since 이후 뉴스 (시간순)."""
        async with self._sessions() as s:
            q = select(NewsItemRow).where(NewsItemRow.ts >= _utc(since)).order_by(NewsItemRow.ts)
            return [row_to_news(r) for r in await s.scalars(q)]
