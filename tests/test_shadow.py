"""게이팅 OFF 섀도 원장 (P1-11, 06 §6.2, ADR 0016).

ON 원장이 받은 전략 신호를 섀도 원장이 같은 수만큼 받고, 판단 결과와 무관하게 배수 1.0으로 체결한다.
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from quantpilot.backtest import ZERO, preset
from quantpilot.core.events import BarClosed, JudgmentEvent, TradeEvent
from quantpilot.core.models import Gate, JudgeResult, Market, Side, Target
from quantpilot.data import synthetic as S
from quantpilot.engine.replay import (
    CollectingBus,
    DirectExecutor,
    ReplayClock,
    StubFeatureBuilder,
    UnrestrictedRisk,
)
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.paper import PaperBroker
from quantpilot.strategies import create
from quantpilot.strategies.base import Context, Strategy


class ScriptedPipeline:
    """호출 순서대로 배수를 돌려준다 (0이면 HOLD). 목록이 끝나면 마지막 값을 반복."""

    def __init__(self, mults=(0.0,)):
        self.mults, self.calls = list(mults), 0

    async def evaluate(self, signal, state):
        m = self.mults[min(self.calls, len(self.mults) - 1)]
        self.calls += 1
        gate = Gate.HOLD if m == 0 else (Gate.HALF if m < 1 else Gate.FULL)
        return JudgmentEvent(0, JudgeResult({}, 0.4 if m == 0 else 0.95), gate, m, signal.ts)


class Scripted(Strategy):
    market = Market.US
    warmup_bars = 1

    def __init__(self, name, symbols, script, horizon="swing"):
        self.name, self.symbols, self.script, self.horizon = name, symbols, script, horizon
        super().__init__()

    @classmethod
    def params_schema(cls):
        return []

    def on_bar(self, ctx: Context):
        return list(self.script.get(ctx.ts, []))


def _runner(strategies, pipeline, *, market=Market.US, cost=ZERO, cash=1_000_000):
    on = PaperBroker(market, cost, cash)
    off = PaperBroker(market, cost, cash)
    risk = UnrestrictedRisk()
    bus, clock = CollectingBus(), ReplayClock()
    runner = TickRunner(
        market, strategies, StubFeatureBuilder(market.value), pipeline,
        DirectExecutor(on, risk), risk, clock, bus, cost=cost,
        shadow=DirectExecutor(off, UnrestrictedRisk()),
    )  # fmt: skip
    return runner, on, off, bus, clock


def _bar(sym, ts, px=100.0):
    return BarClosed(Market.US, sym, "1d", pd.Timestamp(ts).to_pydatetime(), px, px, px, px, 1.0)


def test_held_entry_still_fills_in_shadow_with_full_size():
    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X",), {d0: [Target("X", 0.4)]})
    pipe = ScriptedPipeline([0.0])
    runner, on, off, bus, clock = _runner([s], pipe)
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0)]))
    assert on.ledger == []
    assert len(off.ledger) == 1
    assert off.ledger[0].qty * off.ledger[0].price == pytest.approx(400_000)
    assert pipe.calls == 1  # 판단은 한 번만 (섀도가 다시 부르지 않는다)
    assert runner.shadow_signals == sum(t == "signal" for t, _ in bus.events) == 1


def test_shadow_fills_and_orders_are_not_published():
    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X",), {d0: [Target("X", 0.4)]})
    runner, on, off, bus, clock = _runner([s], ScriptedPipeline([1.0]))
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0)]))
    assert len(on.ledger) == len(off.ledger) == 1
    assert [t for t, _ in bus.events].count("fill") == 1


def test_shadow_signal_count_equals_on_over_synthetic_replay():
    """vol_breakout 합성 데이터: ON이 절반을 hold해도 섀도는 같은 신호 수를 받고 hold분까지 체결한다."""
    strat = create("vol_breakout")
    market = Market(strat.market)
    data = S.universe(strat.symbols, periods=300, start="2020-01-01")
    pipe = ScriptedPipeline([0.0, 1.0] * 1000)
    cost = preset(market)
    runner, on, off, bus, clock = _runner([strat], pipe, market=market, cost=cost)
    by_ts: dict[pd.Timestamp, list[BarClosed]] = {}
    for sym, df in data.items():
        for ts, o, h, lo, c, v in df.itertuples(name=None):
            by_ts.setdefault(ts, []).append(
                BarClosed(market, sym, "1d", ts.to_pydatetime(), o, h, lo, c, v)
            )

    async def go():
        for ts in sorted(by_ts):
            clock.set(ts.to_pydatetime())
            await runner.on_bars_closed(by_ts[ts])

    asyncio.run(go())
    signals = [e for t, e in bus.events if t == "signal"]
    held = pipe.mults[: pipe.calls].count(0.0)
    assert len(signals) > 20 and held > 5
    assert runner.shadow_signals == len(signals)
    on_buys = sum(f.side == Side.BUY for f in on.ledger)
    off_buys = sum(f.side == Side.BUY for f in off.ledger)
    assert off_buys >= on_buys + held  # hold된 진입도 섀도에서는 체결
    assert off.cost is on.cost  # 불변식 #4: 같은 CostModel


def test_shadow_must_be_a_separate_paper_executor():
    class LiveBroker(PaperBroker):
        @property
        def is_paper(self) -> bool:
            return False

    risk = UnrestrictedRisk()
    ex = DirectExecutor(PaperBroker(Market.US, ZERO, 1.0), risk)
    live = DirectExecutor(LiveBroker(Market.US, ZERO, 1.0), risk)
    for shadow in (live, ex):
        with pytest.raises(ValueError, match="섀도"):
            TickRunner(
                Market.US, [], StubFeatureBuilder("us"), ScriptedPipeline(), ex, risk,
                ReplayClock(), CollectingBus(), cost=ZERO, shadow=shadow,
            )  # fmt: skip


def test_stop_in_shadow_only_position_exits_shadow_without_signal():
    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X",), {d0: [Target("X", 0.5, stop=95.0)]}, horizon="intraday")
    runner, on, off, bus, clock = _runner([s], ScriptedPipeline([0.0]))
    clock.set(d0.to_pydatetime())

    async def go():
        await runner.on_bars_closed([_bar("X", d0)])
        t = pd.Timestamp("2021-01-05 09:31").to_pydatetime()
        await runner.on_stop_check(TradeEvent(Market.US, "X", t, 94.0, 1.0))

    asyncio.run(go())
    assert on.ledger == []
    assert [f.side for f in off.ledger] == [Side.BUY, Side.SELL] and not off.positions()
    assert sum(t == "signal" for t, _ in bus.events) == 1  # 진입 신호뿐


def test_time_exit_closes_positions_in_both_books():
    d0 = pd.Timestamp("2021-01-04")
    a = Scripted("a", ("X", "Y"), {d0: [Target("X", 0.3), Target("Y", 0.3)]})
    runner, on, off, _, clock = _runner([a], ScriptedPipeline([1.0, 0.0]))
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0), _bar("Y", d0)]))
    assert set(on.positions()) == {"X"} and set(off.positions()) == {"X", "Y"}
    asyncio.run(runner.on_time_exit("a"))
    assert not on.positions() and not off.positions()
