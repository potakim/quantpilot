"""TickRunner (P1-04, 04 §7, ADR 0002·0010)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import pytest

from quantpilot.backtest import ZERO, Backtester, preset
from quantpilot.core.events import BarClosed, TradeEvent
from quantpilot.core.models import Market, Side, Target
from quantpilot.data import synthetic as S
from quantpilot.engine.history import BarHistory
from quantpilot.engine.replay import (
    CollectingBus,
    DirectExecutor,
    ReplayClock,
    StubFeatureBuilder,
    UnrestrictedRisk,
)
from quantpilot.engine.tick import TickRunner
from quantpilot.execution.paper import PaperBroker
from quantpilot.execution.risk import RiskManager
from quantpilot.judgment.stub import StubPipeline
from quantpilot.strategies import create
from quantpilot.strategies.base import Context, Strategy


class CountingPipeline(StubPipeline):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0

    async def evaluate(self, signal, state):
        self.calls += 1
        return await super().evaluate(signal, state)


class Scripted(Strategy):
    """ts → Target 목록을 그대로 내는 테스트 전략."""

    market = Market.US
    warmup_bars = 1

    def __init__(self, name: str, symbols: tuple[str, ...], script: dict, horizon: str = "swing"):
        self.name, self.symbols, self.script, self.horizon = name, symbols, script, horizon
        super().__init__()

    @classmethod
    def params_schema(cls):
        return []

    def on_bar(self, ctx: Context):
        return list(self.script.get(ctx.ts, []))


def _runner(strategies, *, risk=None, cash=1_000_000, cost=ZERO, pipeline=None, month_last=()):
    broker = PaperBroker(Market.US, cost, cash)
    risk = risk or UnrestrictedRisk()
    bus = CollectingBus()
    clock = ReplayClock(month_last)
    runner = TickRunner(
        Market.US,
        strategies,
        StubFeatureBuilder("us"),
        pipeline or CountingPipeline(),
        DirectExecutor(broker, risk),
        risk,
        clock,
        bus,
        cost=cost,
    )
    return runner, broker, bus, clock


def _bar(sym: str, ts, px: float = 100.0) -> BarClosed:
    return BarClosed(Market.US, sym, "1d", pd.Timestamp(ts).to_pydatetime(), px, px, px, px, 1.0)


def _feed(
    runner: TickRunner, clock: ReplayClock, frames: dict[str, pd.DataFrame], *, one_by_one: bool
):
    """실전 경로: 빈 히스토리에 BarClosed를 하나씩(또는 시각 묶음으로) 넣는다."""
    by_ts: dict[pd.Timestamp, list[BarClosed]] = {}
    for sym, df in frames.items():
        for ts, o, h, lo, c, v in df.itertuples(name=None):
            by_ts.setdefault(ts, []).append(
                BarClosed(runner.market, sym, "1d", ts.to_pydatetime(), o, h, lo, c, v)
            )

    async def go():
        for ts in sorted(by_ts):
            clock.set(ts.to_pydatetime())
            if one_by_one:
                for ev in by_ts[ts]:
                    await runner.on_bar_closed(ev)
            else:
                await runner.on_bars_closed(by_ts[ts])

    asyncio.run(go())


def _sig(fills):
    return [(f.symbol, f.side, round(f.qty, 9), round(f.price, 9), f.ts) for f in fills]


# ---------- 불변식 #2: 같은 데이터 → 같은 체결 ----------
@pytest.mark.parametrize("name", ["vol_breakout", "gtaa"])
def test_backtest_and_live_feed_produce_identical_fills(name):
    """Backtester.run(미리 적재) == TickRunner에 BarClosed를 실시간처럼 흘린 결과 (ADR 0002)."""
    strat = create(name)
    data = S.universe(strat.symbols, periods=400, start="2020-01-01")
    bt = Backtester(preset(strat.market), holdout_months=0)
    res = bt.run(create(name), data)
    assert len(res.fills) > 10

    _, _, month_last = bt._prepare(data)
    market = Market(strat.market)
    broker = PaperBroker(market, preset(market), bt.initial_cash)
    risk = UnrestrictedRisk()
    clock = ReplayClock(month_last)
    runner = TickRunner(
        market,
        [create(name)],
        StubFeatureBuilder(market.value),
        StubPipeline(),
        DirectExecutor(broker, risk),
        risk,
        clock,
        CollectingBus(),
        cost=preset(market),
        history=BarHistory(),  # 빈 히스토리 — 봉마다 append 경로
    )
    _feed(runner, clock, data, one_by_one=len(strat.symbols) == 1)
    assert _sig(broker.ledger) == _sig(res.fills)
    assert broker.equity() == pytest.approx(float(res.equity.iloc[-1]), rel=1e-12)


def test_history_seeded_from_store_then_appended():
    """실전: CandleStore로 시드한 과거 봉 뒤에 마감 봉이 붙고, 역행 봉은 거부한다."""
    df = S.daily(1, periods=5, start="2021-01-01")
    h = BarHistory({"X": df}, preloaded=False)
    assert len(h.view()["X"]) == 5
    nxt = df.index[-1] + pd.Timedelta(days=1)
    h.append(_bar("X", nxt, 123.0))
    assert h.view()["X"].index[-1] == nxt and h.view()["X"]["close"].iloc[-1] == 123.0
    with pytest.raises(ValueError):
        h.append(_bar("X", df.index[0]))


# ---------- 청산 우선 ----------
def test_opposite_targets_from_two_strategies_exit_first():
    """같은 시각, 등록 순으로 A가 진입을 먼저 내도 B의 청산이 먼저 체결된다 (04 §7)."""
    d0, d1 = pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-05")
    a = Scripted("a", ("Y",), {d1: [Target("Y", 1.0)]})
    b = Scripted("b", ("X",), {d0: [Target("X", 1.0)], d1: [Target("X", 0.0)]})
    runner, broker, _, clock = _runner([a, b])

    async def go():
        for ts in (d0, d1):
            clock.set(ts.to_pydatetime())
            await runner.on_bars_closed([_bar("X", ts), _bar("Y", ts)])

    asyncio.run(go())
    sides = [(f.symbol, f.side) for f in broker.ledger]
    assert sides == [("X", Side.BUY), ("X", Side.SELL), ("Y", Side.BUY)]
    assert broker.ledger[-1].qty * broker.ledger[-1].price == pytest.approx(1_000_000)


# ---------- 3단계: 할트면 진입 제거, 청산 유지 ----------
def test_halt_drops_entries_keeps_exits_and_skips_judgment():
    d0, d1 = pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-05")
    s = Scripted(
        "s", ("X", "Y"), {d0: [Target("X", 0.2)], d1: [Target("X", 0.0), Target("Y", 0.2)]}
    )
    rm = RiskManager()
    pipe = CountingPipeline()
    runner, broker, _, clock = _runner([s], risk=rm, pipeline=pipe)

    async def go():
        clock.set(d0.to_pydatetime())
        await runner.on_bars_closed([_bar("X", d0), _bar("Y", d0)])
        rm.halted_reason = "api_errors:3"
        clock.set(d1.to_pydatetime())
        await runner.on_bars_closed([_bar("X", d1), _bar("Y", d1)])

    asyncio.run(go())
    assert [(f.symbol, f.side) for f in broker.ledger] == [("X", Side.BUY), ("X", Side.SELL)]
    assert pipe.calls == 1  # d0 진입 1회뿐. 청산·할트로 제거된 진입은 판단 모델을 안 부른다


# ---------- 불변식 #9: risk.check → submit ----------
def test_every_order_goes_through_risk_check_before_submit():
    calls: list[str] = []

    class SpyRisk(RiskManager):
        def check(self, order, **kw):
            calls.append(f"check:{order.symbol}")
            return super().check(order, **kw)

    class SpyBroker(PaperBroker):
        def submit(self, order):
            calls.append(f"submit:{order.symbol}")
            return super().submit(order)

    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X", "Y"), {d0: [Target("X", 0.2), Target("Y", 0.9)]})
    risk = SpyRisk()
    broker = SpyBroker(Market.US, ZERO, 1_000_000)
    clock = ReplayClock()
    runner = TickRunner(
        Market.US, [s], StubFeatureBuilder("us"), StubPipeline(), DirectExecutor(broker, risk),
        risk, clock, CollectingBus(), cost=ZERO,
    )  # fmt: skip
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0), _bar("Y", d0)]))
    assert calls == ["check:X", "submit:X", "check:Y", "submit:Y"]
    # Y는 종목 비중 상한 25%로 줄어든다 — 리스크 게이트가 수량을 정한다
    y = next(f for f in broker.ledger if f.symbol == "Y")
    assert y.qty * y.price == pytest.approx(250_000)


def test_unrestricted_risk_refuses_live_broker():
    class LiveBroker(PaperBroker):
        @property
        def is_paper(self) -> bool:
            return False

    broker = LiveBroker(Market.US, ZERO, 1.0)
    with pytest.raises(ValueError, match="페이퍼"):
        TickRunner(
            Market.US, [], StubFeatureBuilder("us"), StubPipeline(),
            DirectExecutor(broker, UnrestrictedRisk()), UnrestrictedRisk(), ReplayClock(),
            CollectingBus(), cost=ZERO,
        )  # fmt: skip


# ---------- 손절·시간 청산: 판단 모델 없음 ----------
def test_stop_check_exits_on_breach_without_judgment():
    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X",), {d0: [Target("X", 0.5, stop=95.0)]}, horizon="intraday")
    pipe = CountingPipeline()
    runner, broker, _, clock = _runner([s], pipeline=pipe)
    clock.set(d0.to_pydatetime())

    async def go():
        await runner.on_bars_closed([_bar("X", d0)])
        t = pd.Timestamp("2021-01-05 09:31").to_pydatetime()  # 시장 현지 tz-naive
        await runner.on_stop_check(TradeEvent(Market.US, "X", t, 96.0, 1.0))  # 이탈 전
        assert len(broker.ledger) == 1
        await runner.on_stop_check(TradeEvent(Market.US, "X", t, 94.0, 1.0))

    asyncio.run(go())
    assert [f.side for f in broker.ledger] == [Side.BUY, Side.SELL]
    assert broker.ledger[-1].price == 94.0 and not broker.positions()
    assert pipe.calls == 1  # 진입 때 1회뿐


def test_time_exit_closes_only_that_strategys_positions():
    d0 = pd.Timestamp("2021-01-04")
    a = Scripted("a", ("X",), {d0: [Target("X", 0.3)]})
    b = Scripted("b", ("Y",), {d0: [Target("Y", 0.3)]})
    pipe = CountingPipeline()
    runner, broker, _, clock = _runner([a, b], pipeline=pipe)
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0), _bar("Y", d0)]))
    asyncio.run(runner.on_time_exit("a"))
    assert set(broker.positions()) == {"Y"}
    assert pipe.calls == 2


# ---------- 판단 게이팅 ----------
def test_gating_on_halves_size_for_half_gate():
    """StubJudge는 피처가 없으면 확신도 0.75 → HALF. 게이팅 ON이면 목표 비중의 절반만 산다."""
    d0 = pd.Timestamp("2021-01-04")
    s = Scripted("s", ("X",), {d0: [Target("X", 0.4)]})
    runner, broker, bus, clock = _runner([s], pipeline=StubPipeline(gating=True))
    clock.set(d0.to_pydatetime())
    asyncio.run(runner.on_bars_closed([_bar("X", d0)]))
    f = broker.ledger[0]
    assert f.qty * f.price == pytest.approx(200_000)
    judged = [e for t, e in bus.events if t == "judgment"]
    assert judged and judged[0].size_multiplier == 0.5


# ---------- 매도 수량 캡 (ADR 0010) ----------
def test_sells_never_exceed_held_quantity_with_slippage():
    """0단계는 슬리피지만큼 보유량보다 더 팔아 미세 음수 포지션을 남겼다."""
    strat = create("vol_breakout")
    data = S.universe(strat.symbols, periods=300, start="2020-01-01")
    res = Backtester(preset(strat.market), holdout_months=0).run(strat, data)
    held: dict[str, float] = {}
    for f in res.fills:
        q = held.get(f.symbol, 0.0)
        if f.side == Side.SELL:
            assert f.qty <= q + 1e-12
        held[f.symbol] = q + (f.qty if f.side == Side.BUY else -f.qty)
    assert all(abs(q) < 1e-9 or q > 0 for q in held.values())


def test_apply_risk_caps_symbol_weight_in_backtest():
    """apply_risk=True면 실제 RiskManager가 걸린다: GEM의 100% 한 종목 → 25% 상한."""
    strat = create("gem")
    data = S.universe(strat.symbols, periods=800, start="2018-01-01")
    free = Backtester(preset(strat.market), holdout_months=0).run(create("gem"), data)
    capped = Backtester(preset(strat.market), holdout_months=0, apply_risk=True).run(
        create("gem"), data
    )
    first_buy = next(f for f in capped.fills if f.side == Side.BUY)
    # 상한은 기준가로 계산되고, 체결가에는 슬리피지가 붙는다
    assert first_buy.gross <= 0.25 * 10_000_000 * (1 + preset(strat.market).slippage_rate) + 1e-6
    assert next(f for f in free.fills if f.side == Side.BUY).gross > 0.9 * 10_000_000


# ---------- 실시간 배선 (engine/main.py) ----------
def test_market_engine_groups_bars_of_one_bucket_into_one_tick():
    """A 봉은 다음 체결로 먼저 확정되고 B 봉은 타이머로 늦게 확정돼도, 전략은 그 시각에 한 번만 평가된다."""
    from quantpilot.data.aggregator import CandleAggregator
    from quantpilot.engine.main import MarketEngine

    class SpyRunner:
        def __init__(self):
            self.batches: list[list[str]] = []
            self.stop_checks = 0

        async def on_stop_check(self, ev):
            self.stop_checks += 1

        async def on_bars_closed(self, evs):
            self.batches.append(sorted(e.symbol for e in evs))

    class Clock:
        now_ts = datetime(2021, 1, 4, 10, 0, 30)  # noqa: DTZ001 — 시장 현지 tz-naive

        def now(self):
            return self.now_ts

    clock, spy = Clock(), SpyRunner()
    eng = MarketEngine(spy, CandleAggregator("1m", Market.UPBIT), clock)  # type: ignore[arg-type]

    def tr(sym, hh_mm_ss, px=100.0):
        h, m, s = hh_mm_ss
        return TradeEvent(Market.UPBIT, sym, datetime(2021, 1, 4, h, m, s), px, 1.0)  # noqa: DTZ001

    async def go():
        await eng.on_trade(tr("A", (10, 0, 10)))
        await eng.on_trade(tr("B", (10, 0, 20)))
        await eng.on_trade(tr("A", (10, 1, 0)))  # A의 10:00 봉 즉시 확정
        clock.now_ts = datetime(2021, 1, 4, 10, 1, 1)  # noqa: DTZ001 — 아직 grace(2초) 안
        assert await eng.on_timer() == 0
        clock.now_ts = datetime(2021, 1, 4, 10, 1, 2)  # noqa: DTZ001
        assert await eng.on_timer() == 2

    asyncio.run(go())
    assert spy.batches == [["A", "B"]]
    assert spy.stop_checks == 3


def test_build_upbit_paper_wires_paper_broker_and_real_risk_manager():
    from quantpilot.engine.main import build_upbit_paper

    eng = build_upbit_paper()
    assert eng.runner.executor.is_paper
    assert isinstance(eng.runner.risk, RiskManager)
    assert [s.name for s in eng.runner.strategies] == ["vol_breakout"]
