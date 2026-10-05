"""P1-01 DB 계층: alembic 마이그레이션, dataclass↔ORM 왕복, repo 단위 테스트(SQLite 인메모리)."""

# 엔진 시각은 시장 현지 tz-naive가 규칙이다 (CLAUDE.md, ADR 0009)
# ruff: noqa: DTZ001

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")
pytest.importorskip("aiosqlite")

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.core.models import (
    Fill,
    JudgeResult,
    Market,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Target,
)
from quantpilot.core.repos import (
    ConfigRepo,
    JudgmentRepo,
    Ledger,
    PositionRepo,
    SignalRepo,
)
from quantpilot.db import mappers
from quantpilot.db.models import Base, SettingRow
from quantpilot.db.repo import (
    SqlConfigRepo,
    SqlJudgmentRepo,
    SqlLedger,
    SqlPositionRepo,
    SqlSignalRepo,
)

ROOT = Path(__file__).resolve().parents[1]
TS = datetime(2026, 9, 29, 0, 5)


def alembic_cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


# ── 마이그레이션 ──────────────────────────────────────────


def test_alembic_upgrade_head_matches_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    command.upgrade(alembic_cfg(url), "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names()) - {"alembic_version"}
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()

    assert tables == set(Base.metadata.tables)
    assert len(tables) == 14  # 02 문서 §1의 테이블 수
    assert diff == []  # 마이그레이션 == ORM 모델


def test_alembic_downgrade_base_drops_everything(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = alembic_cfg(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(url)
    with engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) == {"alembic_version"}
    engine.dispose()


# ── dataclass ↔ ORM 왕복 ────────────────────────────────


def make_order(**kw) -> Order:
    base = {
        "symbol": "KRW-BTC",
        "side": Side.BUY,
        "qty": 0.12345678,
        "type": OrderType.LIMIT,
        "limit_price": 95_000_000.0,
        "strategy": "vol_breakout",
        "stop": 93_000_000.0,
        "status": OrderStatus.FILLED,
        "market": Market.UPBIT,
        "signal_id": None,
        "broker_order_id": "uuid-abc",
        "ts": TS,
        "risk_adjustments": ["capped_by_1pct"],
        "size_multiplier": 0.5,
        "paper": True,
    }
    base.update(kw)
    return Order(**base)


def make_fill(order_id: str, **kw) -> Fill:
    base = {
        "order_id": order_id,
        "symbol": "KRW-BTC",
        "side": Side.BUY,
        "qty": 0.12345678,
        "price": 95_047_500.0,
        "fee": 5867.0,
        "tax": 0.0,
        "ts": TS,
        "strategy": "vol_breakout",
        "reason": "k=0.5 돌파",
        "market": Market.UPBIT,
        "paper": True,
    }
    base.update(kw)
    return Fill(**base)


def test_order_roundtrip_in_memory():
    o = make_order()
    assert mappers.row_to_order(mappers.order_to_row(o)) == o


def test_fill_roundtrip_in_memory():
    f = make_fill("o1")
    assert mappers.row_to_fill(mappers.fill_to_row(f)) == f


def test_position_roundtrip_in_memory():
    p = Position("KRW-BTC", 0.5, 90_000_000.0, TS, "vol_breakout", Market.UPBIT, 88_000_000.0)
    assert mappers.row_to_position(mappers.position_to_row(p)) == p


def test_aware_timestamp_is_stored_as_utc_and_read_back_local_naive():
    kst = datetime(2026, 9, 29, 18, 5, tzinfo=timezone(timedelta(hours=9)))
    row = mappers.fill_to_row(make_fill("o1", ts=kst))
    assert row.ts == datetime(2026, 9, 29, 9, 5, tzinfo=UTC)
    assert mappers.from_db_ts(row.ts, Market.UPBIT) == datetime(2026, 9, 29, 18, 5)


@pytest.mark.parametrize(
    ("market", "local", "utc"),
    [
        (Market.UPBIT, datetime(2026, 9, 29, 9, 0), datetime(2026, 9, 29, 0, 0, tzinfo=UTC)),
        (Market.KRX, datetime(2026, 9, 29, 15, 20), datetime(2026, 9, 29, 6, 20, tzinfo=UTC)),
        (Market.US, datetime(2026, 7, 1, 9, 30), datetime(2026, 7, 1, 13, 30, tzinfo=UTC)),  # EDT
        (Market.US, datetime(2026, 12, 1, 9, 30), datetime(2026, 12, 1, 14, 30, tzinfo=UTC)),  # EST
    ],
    ids=["upbit-kst", "krx-kst", "us-edt", "us-est"],
)
def test_naive_timestamp_is_market_local_time(market, local, utc):
    """ADR 0009 §6: tz-naive는 그 시장 현지시간. 저장은 UTC, 읽으면 현지 naive (SQLite는 tz를 잃음)."""
    row = mappers.fill_to_row(make_fill("o1", ts=local, market=market))
    assert row.ts == utc
    assert mappers.from_db_ts(row.ts, market) == local
    assert mappers.from_db_ts(row.ts.replace(tzinfo=None), market) == local


@pytest.mark.parametrize(
    "obj",
    [make_order(market=None), make_fill("o1", market=None), Position("KRW-BTC", 1.0)],
    ids=["order", "fill", "position"],
)
def test_missing_market_is_rejected(obj):
    to_row = {
        Order: mappers.order_to_row,
        Fill: mappers.fill_to_row,
        Position: mappers.position_to_row,
    }[type(obj)]
    with pytest.raises(ValueError, match="market"):
        to_row(obj)


def test_order_without_ts_is_rejected():
    with pytest.raises(ValueError, match="ts"):
        mappers.order_to_row(make_order(ts=None))


# ── repo (SQLite 인메모리) ──────────────────────────────


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    # PostgreSQL처럼 외래 키를 강제한다
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("pragma foreign_keys=on")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_sql_repos_satisfy_core_protocols(sessions):
    # 코어는 Protocol만 안다 — 구현이 시그니처를 맞추는지 정적 대입으로 확인
    ledger: Ledger = SqlLedger(sessions)
    signals: SignalRepo = SqlSignalRepo(sessions)
    judgments: JudgmentRepo = SqlJudgmentRepo(sessions)
    positions: PositionRepo = SqlPositionRepo(sessions)
    config: ConfigRepo = SqlConfigRepo(sessions)
    for proto, impl in [
        (Ledger, ledger),
        (SignalRepo, signals),
        (JudgmentRepo, judgments),
        (PositionRepo, positions),
        (ConfigRepo, config),
    ]:
        missing = [m for m in vars(proto) if not m.startswith("_") and not hasattr(impl, m)]
        assert missing == [], f"{type(impl).__name__}에 {missing} 없음"


async def test_ledger_order_and_fill_roundtrip_through_db(sessions):
    ledger = SqlLedger(sessions)
    o = make_order()
    await ledger.save_order(o)
    assert await ledger.order(o.id) == o

    fill_id = await ledger.record(make_fill(o.id))
    assert fill_id > 0
    got = await ledger.fills(Market.UPBIT)
    assert got == [make_fill(o.id)]


async def test_ledger_save_order_updates_status(sessions):
    ledger = SqlLedger(sessions)
    o = make_order(status=OrderStatus.PENDING)
    await ledger.save_order(o)
    o.status = OrderStatus.CANCELLED
    await ledger.save_order(o)
    assert (await ledger.order(o.id)).status == OrderStatus.CANCELLED


async def test_ledger_fills_filter_by_symbol_and_since(sessions):
    ledger = SqlLedger(sessions)
    o = make_order()
    await ledger.save_order(o)
    await ledger.record(make_fill(o.id, ts=TS))
    await ledger.record(make_fill(o.id, ts=TS + timedelta(hours=1), symbol="KRW-ETH"))
    await ledger.record(make_fill(o.id, ts=TS + timedelta(hours=2)))

    btc = await ledger.fills(Market.UPBIT, symbol="KRW-BTC")
    assert [f.ts for f in btc] == [TS, TS + timedelta(hours=2)]
    recent = await ledger.fills(Market.UPBIT, since=TS + timedelta(minutes=30))
    assert [f.symbol for f in recent] == ["KRW-ETH", "KRW-BTC"]
    assert await ledger.fills(Market.KRX) == []


async def test_fill_requires_existing_order(sessions):
    with pytest.raises(IntegrityError):
        await SqlLedger(sessions).record(make_fill("no-such-order"))


async def test_signal_judgment_flow(sessions):
    config = SqlConfigRepo(sessions)
    sid = await config.upsert_strategy(
        name="vol_breakout", market=Market.UPBIT, allocation=0.3, symbols=["KRW-BTC"]
    )
    signals = SqlSignalRepo(sessions)
    target = Target("KRW-BTC", 1.0, price=95_000_000.0, reason="돌파", stop=93_000_000.0)
    sig = await signals.add(
        strategy_id=sid, market=Market.UPBIT, target=target, kind="entry", ts=TS
    )
    row = await signals.get(sig)
    assert row["outcome"] == "pending" and row["price_hint"] == 95_000_000.0

    judgments = SqlJudgmentRepo(sessions)
    result = JudgeResult(
        answers={"regime": {"trend_up": 0.79}, "news_risk": 0.12},
        confidence=0.83,
        latency_ms=420.0,
        model="stub",
        cost_usd=0.002,
        raw={"prompt_hash": "0123456789abcdef"},
    )
    jid = await judgments.add(
        signal_id=sig,
        result=result,
        state={"text": "state v1"},
        gate="half",
        blocks=[],
        ts=TS,
    )
    await signals.set_outcome(sig, "judged_hold", "confidence<0.9")
    await judgments.set_realized(jid, 0.012, True)

    j = await judgments.get(jid)
    assert j["answers"] == result.answers and j["provider"] == "stub"
    # 질문 문구 prompt_hash는 state jsonb에 함께 남는다 (ADR 0014)
    assert j["state"] == {"text": "state v1", "prompt_hash": "0123456789abcdef"}
    assert j["gate"] == "half" and j["realized_ret_24h"] == 0.012 and j["direction_hit"] is True
    s = await signals.get(sig)
    assert (s["outcome"], s["outcome_reason"]) == ("judged_hold", "confidence<0.9")


async def test_order_links_to_signal(sessions):
    sid = await SqlConfigRepo(sessions).upsert_strategy(
        name="gem", market=Market.US, allocation=0.5, symbols=["SPY"]
    )
    sig = await SqlSignalRepo(sessions).add(
        strategy_id=sid, market=Market.US, target=Target("SPY", 1.0), kind="rebalance", ts=TS
    )
    ledger = SqlLedger(sessions)
    o = make_order(market=Market.US, symbol="SPY", signal_id=sig)
    await ledger.save_order(o)
    assert (await ledger.order(o.id)).signal_id == sig


async def test_position_upsert_and_close(sessions):
    repo = SqlPositionRepo(sessions)
    p = Position("KRW-BTC", 0.5, 90_000_000.0, TS, "vol_breakout", Market.UPBIT, 88_000_000.0)
    await repo.upsert(p)
    assert await repo.all(Market.UPBIT) == [p]

    p.qty, p.avg_price = 0.8, 91_000_000.0
    await repo.upsert(p)
    assert (await repo.all(Market.UPBIT))[0].qty == 0.8

    p.qty = 0.0
    await repo.upsert(p)
    assert await repo.all(Market.UPBIT) == []
    await repo.upsert(p)  # 없는 포지션 청산도 오류 없음


async def test_config_settings_and_strategies(sessions):
    config = SqlConfigRepo(sessions)
    assert await config.get_setting("gate.hold_below", 0.5) == 0.5
    await config.set_setting("gate.hold_below", 0.55)
    await config.set_setting("gate.hold_below", 0.6)
    assert await config.get_setting("gate.hold_below") == 0.6

    a = await config.upsert_strategy(
        name="gtaa", market=Market.US, allocation=0.2, symbols=["SPY", "TLT"]
    )
    b = await config.upsert_strategy(
        name="gtaa", market=Market.US, allocation=0.4, symbols=["SPY"], enabled=True
    )
    assert a == b  # (name, market) 기준 갱신
    [s] = await config.strategies(Market.US)
    assert (s["allocation"], s["symbols"], s["enabled"]) == (0.4, ["SPY"], True)
    assert await config.strategies(Market.KRX) == []


async def test_overlap_guard_flags_two_sessions_on_one_connection(sessions, connection_overlaps):
    """연결 하나를 두 세션이 같이 쥐면 커밋 전 쓰기가 사라진다 — conftest 가드가 그 순간을 잡는다 (t40)."""
    config = SqlConfigRepo(sessions)
    await config.set_setting("seq", 1)
    assert await config.get_setting("seq") == 1
    assert connection_overlaps == []  # 차례로 쓰면 겹치지 않는다

    async with sessions.begin() as w:
        w.add(SettingRow(key="k", value=1))
        await w.flush()  # INSERT는 나갔지만 커밋 전
        assert await config.get_setting("k") == 1  # 같은 연결의 다른 세션이 읽고 반납(rollback)
    assert connection_overlaps and max(connection_overlaps) == 2
    assert await config.get_setting("k") is None  # 반납 때의 rollback이 커밋 전 쓰기를 지웠다
    connection_overlaps.clear()  # 일부러 겹쳤으니 가드가 이 테스트를 실패시키지 않게
