"""P1-03 CandleStore: SQLite 인메모리로 upsert·load·캐시, 현지시간↔UTC 왕복."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.core.events import BarClosed
from quantpilot.core.models import Market
from quantpilot.core.repos import CandleRepo
from quantpilot.data.loader import COLS
from quantpilot.data.store import CandleStore
from quantpilot.db.models import Base, CandleRow
from quantpilot.db.repo import SqlCandleRepo

T = datetime(2026, 9, 30, 9, 0)  # 업비트 현지(KST)


def bar(i: int, close: float = 100.0, symbol: str = "KRW-BTC", tf: str = "1m") -> BarClosed:
    return BarClosed(
        Market.UPBIT, symbol, tf, T + timedelta(minutes=i), close, close + 1, close - 1, close, 2.5
    )


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class CountingRepo:
    def __init__(self, inner: SqlCandleRepo) -> None:
        self.inner = inner
        self.loads = 0

    async def upsert(self, bars, *, source="ws"):
        return await self.inner.upsert(bars, source=source)

    async def load(self, market, symbol, tf, start, end):
        self.loads += 1
        return await self.inner.load(market, symbol, tf, start, end)


def test_sql_repo_satisfies_protocol():
    repo: CandleRepo = SqlCandleRepo(async_sessionmaker())
    assert hasattr(repo, "upsert") and hasattr(repo, "load")


async def test_upsert_then_load_roundtrip(sessions):
    store = CandleStore(SqlCandleRepo(sessions), Market.UPBIT)
    assert await store.upsert([bar(0, 100), bar(1, 101), bar(2, 102)]) == 3
    df = await store.load("KRW-BTC", "1m", T, T + timedelta(minutes=2))
    assert list(df.columns) == COLS
    assert list(df.index) == [T, T + timedelta(minutes=1)]  # end는 제외
    assert df.index.tz is None and df.index.name == "ts"
    assert df["close"].tolist() == [100.0, 101.0]
    assert df["volume"].tolist() == [2.5, 2.5]


async def test_db_stores_utc(sessions):
    await SqlCandleRepo(sessions).upsert([bar(0)], source="rest")
    async with sessions() as s:
        row = (await s.scalars(select(CandleRow))).one()
    ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=UTC)
    assert ts == datetime(2026, 9, 30, 0, 0, tzinfo=UTC)  # 09:00 KST = 00:00 UTC
    assert row.source == "rest"


async def test_upsert_same_bar_overwrites(sessions):
    repo = SqlCandleRepo(sessions)
    store = CandleStore(repo, Market.UPBIT)
    await store.upsert([bar(0, 100)])
    await store.upsert([bar(0, 200)])
    bars = await repo.load(Market.UPBIT, "KRW-BTC", "1m", T, T + timedelta(hours=1))
    assert [b.close for b in bars] == [200.0]
    assert bars[0] == bar(0, 200)


async def test_load_filters_symbol_and_tf(sessions):
    store = CandleStore(SqlCandleRepo(sessions), Market.UPBIT)
    await store.upsert([bar(0), bar(0, symbol="KRW-ETH"), bar(0, tf="5m")])
    df = await store.load("KRW-BTC", "1m", T, T + timedelta(hours=1))
    assert len(df) == 1


async def test_cache_hit_and_invalidation(sessions):
    repo = CountingRepo(SqlCandleRepo(sessions))
    store = CandleStore(repo, Market.UPBIT)
    await store.upsert([bar(0, 100)])
    end = T + timedelta(hours=1)
    first = await store.load("KRW-BTC", "1m", T, end)
    first.loc[T, "close"] = -1  # 돌려준 사본을 고쳐도 캐시는 그대로
    again = await store.load("KRW-BTC", "1m", T, end)
    assert repo.loads == 1
    assert again["close"].tolist() == [100.0]
    await store.upsert([bar(0, symbol="KRW-ETH")])  # 다른 심볼: 캐시 유지
    await store.load("KRW-BTC", "1m", T, end)
    assert repo.loads == 1
    await store.upsert([bar(1, 101)])  # 같은 심볼·주기: 캐시 폐기
    df = await store.load("KRW-BTC", "1m", T, end)
    assert repo.loads == 2
    assert df["close"].tolist() == [100.0, 101.0]


async def test_cache_is_bounded(sessions):
    repo = CountingRepo(SqlCandleRepo(sessions))
    store = CandleStore(repo, Market.UPBIT, cache_size=2)
    for i in range(3):
        await store.load("KRW-BTC", "1m", T, T + timedelta(minutes=i + 1))
    await store.load("KRW-BTC", "1m", T, T + timedelta(minutes=1))  # 가장 오래된 것은 밀려남
    assert repo.loads == 4


async def test_empty_load_and_wrong_market(sessions):
    store = CandleStore(SqlCandleRepo(sessions), Market.UPBIT)
    df = await store.load("KRW-BTC", "1m", T, T + timedelta(hours=1))
    assert df.empty and list(df.columns) == COLS
    assert await store.upsert([]) == 0
    krx = BarClosed(Market.KRX, "005930", "1m", T, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError):
        await store.upsert([krx])
