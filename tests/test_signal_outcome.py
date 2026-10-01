"""t18: TickRunner가 기록한 signal_id를 SignalEvent·Order에 실어 signals.outcome을 남긴다.

진입 신호 → 주문 → 체결이면 outcome은 ordered → filled, 리스크 거부면 risk_rejected,
게이트 hold면 judged_hold. 섀도 원장 주문에는 signal_id를 붙이지 않는다 (ADR 0016).
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from api_helpers import memory_sessions
from sqlalchemy import select

from quantpilot.backtest import ZERO
from quantpilot.core.events import BarClosed, JudgmentEvent
from quantpilot.core.models import Gate, JudgeResult, Market, Target
from quantpilot.db.models import OrderRow, SignalRow
from quantpilot.db.repo import SqlLedger, SqlSignalRepo
from quantpilot.engine.replay import ReplayClock, StubFeatureBuilder, UnrestrictedRisk
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.executor import OrderExecutor
from quantpilot.execution.paper import PaperBroker
from quantpilot.execution.ratelimit import NoLimiter
from quantpilot.execution.risk import RiskDecision
from quantpilot.judgment.stub import StubPipeline
from quantpilot.realtime.bus import EventRecorder, HubBus
from quantpilot.realtime.hub import MemoryHub
from quantpilot.strategies.base import Context, Strategy

US = Market.US
SYM = "SPY"
T0 = pd.Timestamp("2026-09-28 16:00").to_pydatetime()  # 시장 현지시간 tz-naive
T1 = pd.Timestamp("2026-09-29 16:00").to_pydatetime()


class Scripted(Strategy):
    """T1에 진입 Target 하나를 내는 테스트 전략."""

    name = "scripted"
    market = US
    symbols = (SYM,)
    horizon = "swing"
    warmup_bars = 1

    @classmethod
    def params_schema(cls):
        return []

    def on_bar(self, ctx: Context):
        return [Target(SYM, 0.5, reason="test entry")] if ctx.ts == T1 else []


class RecordingSignals(SqlSignalRepo):
    """set_outcome 호출 순서를 남기는 신호 저장소."""

    def __init__(self, sessions) -> None:
        super().__init__(sessions)
        self.calls: list[tuple[int, str]] = []

    async def set_outcome(self, signal_id: int, outcome: str, reason: str | None = None) -> None:
        self.calls.append((signal_id, outcome))
        await super().set_outcome(signal_id, outcome, reason)


class RejectRisk(UnrestrictedRisk):
    def check(self, order, **_):
        return RiskDecision(False, 0.0, "test_block")


class HoldPipeline(StubPipeline):
    async def evaluate(self, signal, state):
        jr = JudgeResult({}, 0.1, model="stub")
        return JudgmentEvent(signal.signal_id or 0, jr, Gate.HOLD, 0.0, signal.ts, ("low_conf",))


async def _run(*, risk=None, pipeline=None, shadow=False):
    engine, sessions = await memory_sessions()
    recorder = EventRecorder.from_sessions(sessions)
    bus = HubBus(MemoryHub(), US, recorder=recorder)
    signals = RecordingSignals(sessions)
    risk = risk or UnrestrictedRisk()
    ex = OrderExecutor(
        PaperBroker(US, ZERO, 1_000_000), risk, SqlLedger(sessions), NoLimiter(), signals=signals
    )
    sh = None
    if shadow:
        sh = OrderExecutor(
            PaperBroker(US, ZERO, 1_000_000),
            UnrestrictedRisk(),
            SqlLedger(sessions, shadow=True),
            NoLimiter(),
        )
    clock = ReplayClock()
    runner = TickRunner(
        US,
        [Scripted()],
        StubFeatureBuilder("us"),
        pipeline or StubPipeline(),
        ex,
        risk,
        clock,
        bus,
        cost=ZERO,
        shadow=sh,
        record_signal=recorder.signal,
    )
    for ts in (T0, T1):
        clock.set(ts)
        await runner.on_bar_closed(BarClosed(US, SYM, "1d", ts, 100, 100, 100, 100, 1.0))
    async with sessions() as s:
        sig_rows = (await s.execute(select(SignalRow))).scalars().all()
        order_rows = (await s.execute(select(OrderRow))).scalars().all()
    await engine.dispose()
    return signals.calls, sig_rows, order_rows


def test_entry_signal_outcome_goes_ordered_then_filled():
    calls, sigs, orders = asyncio.run(_run())
    assert len(sigs) == 1  # 신호는 한 번만 기록 (버스가 다시 쓰지 않는다)
    sid = sigs[0].id
    assert calls == [(sid, "ordered"), (sid, "filled")]
    assert sigs[0].outcome == "filled"
    assert [o.signal_id for o in orders] == [sid]


def test_risk_rejected_entry_signal_outcome():
    calls, sigs, _ = asyncio.run(_run(risk=RejectRisk()))
    sid = sigs[0].id
    assert calls == [(sid, "risk_rejected")]
    assert sigs[0].outcome == "risk_rejected"


def test_gate_hold_keeps_judged_hold():
    calls, sigs, orders = asyncio.run(_run(pipeline=HoldPipeline()))
    assert len(sigs) == 1 and sigs[0].outcome == "judged_hold"
    assert calls == [] and orders == []  # 버스 기록기가 judged_hold를 쓰고, 주문은 없다


def test_shadow_orders_carry_no_signal_id():
    _, sigs, orders = asyncio.run(_run(shadow=True))
    sid = sigs[0].id
    assert sorted((o.shadow, o.signal_id) for o in orders) == [(False, sid), (True, None)]
    assert sigs[0].outcome == "filled"  # outcome은 ON 원장 결과
