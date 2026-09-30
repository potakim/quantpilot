"""섀도 원장 영속화·엔진 배선 (P1-11, ADR 0016). SQLite 인메모리."""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from quantpilot.backtest import ZERO
from quantpilot.core.events import BarClosed, JudgmentEvent
from quantpilot.core.models import Gate, JudgeResult, Market, Position, Side, Target
from quantpilot.db.models import Base
from quantpilot.db.repo import SqlConfigRepo, SqlLedger, SqlPositionRepo
from quantpilot.engine.replay import (
    CollectingBus,
    ReplayClock,
    StubFeatureBuilder,
    UnrestrictedRisk,
)
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.executor import OrderExecutor
from quantpilot.execution.persistent_paper import PersistentPaperBroker, SettingsPositionRepo
from quantpilot.execution.ratelimit import NoLimiter
from quantpilot.execution.risk import RiskManager
from quantpilot.strategies.base import Context, Strategy

M = Market.UPBIT


class HoldPipeline:
    async def evaluate(self, signal, state):
        return JudgmentEvent(0, JudgeResult({}, 0.3), Gate.HOLD, 0.0, signal.ts)


class Once(Strategy):
    market = M
    warmup_bars = 1
    name = "once"
    symbols = ("KRW-BTC",)

    def __init__(self, ts, target):
        self.ts, self.target = ts, target
        super().__init__()

    @classmethod
    def params_schema(cls):
        return []

    def on_bar(self, ctx: Context):
        return [self.target] if ctx.ts == self.ts else []


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("pragma foreign_keys=on")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _shadow_broker(config, cash=1_000_000.0):
    return PersistentPaperBroker(
        M, ZERO, cash, positions=SettingsPositionRepo(config, M), config=config, book="shadow"
    )


async def test_held_signal_is_persisted_only_in_shadow_ledger(sessions):
    config = SqlConfigRepo(sessions)
    on_b = PersistentPaperBroker(
        M, ZERO, 1_000_000, positions=SqlPositionRepo(sessions), config=config
    )
    risk = UnrestrictedRisk()
    on = OrderExecutor(on_b, risk, SqlLedger(sessions), NoLimiter())
    off = OrderExecutor(
        _shadow_broker(config), UnrestrictedRisk(), SqlLedger(sessions, shadow=True), NoLimiter()
    )
    d0 = pd.Timestamp("2021-01-04 09:00")
    clock = ReplayClock()
    runner = TickRunner(
        M, [Once(d0, Target("KRW-BTC", 0.4))], StubFeatureBuilder("upbit"), HoldPipeline(),
        on, risk, clock, CollectingBus(), cost=ZERO, shadow=off,
    )  # fmt: skip
    clock.set(d0.to_pydatetime())
    bar = BarClosed(M, "KRW-BTC", "1m", d0.to_pydatetime(), 100, 100, 100, 100, 1.0)
    await runner.on_bars_closed([bar])

    assert await SqlLedger(sessions).fills(M) == []
    shadow_fills = await SqlLedger(sessions, shadow=True).fills(M)
    assert len(shadow_fills) == 1 and shadow_fills[0].side == Side.BUY
    # 섀도 계좌는 positions 테이블이 아니라 settings에 — ON 포지션과 섞이지 않는다
    assert await SqlPositionRepo(sessions).all(M) == []
    restored = _shadow_broker(config, cash=0.0)
    await restored.restore()
    assert restored.positions()["KRW-BTC"].qty == pytest.approx(shadow_fills[0].qty)
    assert restored.cash() == pytest.approx(600_000)
    assert await config.get_setting("paper.cash.upbit") is None  # ON 현금은 체결이 없어 그대로


async def test_settings_position_repo_upsert_and_close(sessions):
    repo = SettingsPositionRepo(SqlConfigRepo(sessions), M)
    t = pd.Timestamp("2021-01-04 09:00").to_pydatetime()
    await repo.upsert(Position("KRW-ETH", 2.0, 10.0, t, "vol_breakout", M, 9.0))
    await repo.upsert(Position("KRW-BTC", 1.0, 5.0, None, "vol_breakout", M))
    got = {p.symbol: p for p in await repo.all(M)}
    assert got["KRW-ETH"].opened_at == t and got["KRW-ETH"].stop == 9.0
    await repo.upsert(Position("KRW-ETH", 0.0, 0.0, None, "vol_breakout", M))
    assert [p.symbol for p in await repo.all(M)] == ["KRW-BTC"]
    assert await repo.all(Market.KRX) == []


def test_build_upbit_paper_wires_shadow_with_same_cost_and_own_risk():
    from quantpilot.engine.main import build_upbit_paper

    eng = build_upbit_paper()
    sh = eng.runner.shadow
    assert sh is not None and sh.is_paper and sh is not eng.runner.executor
    assert isinstance(sh.risk, RiskManager) and sh.risk is not eng.runner.risk
    assert sh.broker.cost is eng.runner.executor.broker.cost  # 불변식 #4


async def test_build_upbit_paper_with_db_uses_shadow_ledger(sessions):
    from quantpilot.engine.main import build_upbit_paper

    eng = build_upbit_paper(sessions=sessions)
    sh = eng.runner.shadow
    assert sh.ledger.shadow and not eng.runner.executor.ledger.shadow
    assert sh.signals is None  # 신호 outcome은 ON 원장이 쓴다
    assert sh.broker.cash_key == "paper.shadow.cash.upbit"
    assert sh.broker.cost is eng.runner.executor.broker.cost
