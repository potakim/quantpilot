import pandas as pd
import pytest

from quantpilot.backtest import ZERO, AttemptTracker, Backtester, CostModel, preset
from quantpilot.core.models import Market, Target
from quantpilot.data import synthetic as S
from quantpilot.strategies import REGISTRY, create
from quantpilot.strategies.base import Context, ParamSpec, Strategy


class BuyAndHold(Strategy):
    name = "bah"
    market = Market.US
    symbols = ("X",)
    warmup_bars = 1

    @classmethod
    def params_schema(cls):
        return []

    def on_bar(self, ctx: Context):
        return [Target("X", 1.0)] if not ctx.positions.get("X") else []


def test_synthetic_seed_is_stable_across_processes():
    """내장 hash()는 PYTHONHASHSEED마다 바뀐다. 합성 데이터는 프로세스가 달라도 같아야 한다."""
    import os
    import subprocess
    import sys

    code = (
        "from quantpilot.data import synthetic as S;"
        "print(S.seed_of('SPY'), S.universe(('SPY',), periods=50)['SPY']['close'].iloc[-1])"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outs) == 1


def test_sells_fill_before_buys_in_same_bar():
    """전략이 매수를 먼저 적어도 엔진은 매도부터 체결해 현금이 놀지 않아야 한다 (청산 우선)."""
    idx = pd.date_range("2021-01-01", periods=3)
    flat = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}, index=idx
    )

    class Switch(Strategy):
        name = "switch"
        market = Market.US
        symbols = ("A", "B")
        warmup_bars = 1

        @classmethod
        def params_schema(cls):
            return []

        def on_bar(self, ctx):
            if ctx.ts == idx[0]:
                return [Target("A", 1.0)]
            if ctx.ts == idx[1]:
                return [Target("B", 1.0), Target("A", 0.0)]  # 매수를 먼저 적는다
            return []

    res = Backtester(ZERO, 1_000_000, holdout_months=0, allow_zero_cost=True).run(
        Switch(), {"A": flat, "B": flat.copy()}
    )
    buy_b = [f for f in res.fills if f.symbol == "B"]
    assert buy_b and buy_b[0].qty * buy_b[0].price == pytest.approx(1_000_000)


def test_zero_cost_requires_explicit_flag():
    with pytest.raises(ValueError):
        Backtester(ZERO)
    Backtester(ZERO, allow_zero_cost=True)


def test_buy_and_hold_matches_price_return_minus_costs():
    df = S.daily(1, periods=400, start="2020-01-01")
    cost = CostModel(fee_rate=0.001, slippage_rate=0.0, sell_tax_rate=0.0)
    res = Backtester(cost, 1_000_000, holdout_months=0).run(BuyAndHold(), {"X": df})
    first_close, last_close = float(df["close"].iloc[0]), float(df["close"].iloc[-1])
    expected = last_close / (first_close * 1.001) - 1  # 현금 전부로 매수(수수료 포함), 매도 없음
    assert res.metrics.total_return == pytest.approx(expected, rel=1e-6)
    assert len(res.fills) == 1


def test_holdout_cuts_last_12_months():
    df = S.daily(2, periods=800, start="2020-01-01")
    res = Backtester(preset("us"), holdout_months=12).run(BuyAndHold(), {"X": df})
    assert res.holdout_cutoff is not None
    assert res.equity.index[-1] <= df.index[-1] - pd.DateOffset(months=12)
    res2 = Backtester(preset("us"), holdout_months=12, unlock_holdout=True).run(
        BuyAndHold(), {"X": df}
    )
    assert res2.equity.index[-1] == df.index[-1]


def test_price_hint_is_clipped_to_bar_range():
    df = S.daily(3, periods=50, start="2021-01-01")

    class Bad(BuyAndHold):
        def on_bar(self, ctx):
            bar = ctx.current("X")
            return (
                [Target("X", 1.0, price=float(bar["high"]) * 2)]
                if not ctx.positions.get("X")
                else []
            )

    res = Backtester(preset("us"), holdout_months=0).run(Bad(), {"X": df})
    assert res.warnings and "클립" in res.warnings[0]
    assert res.fills[0].price <= float(df["high"].max()) * (1 + preset("us").slippage_rate)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_registered_strategy_runs(name, tmp_path):
    strat = create(name)
    if strat.timeframe == "5m":
        data = {s: S.intraday_5m(11, days=30) for s in strat.symbols}
        hold = 0
    else:
        data = S.universe(strat.symbols, periods=1500, start="2018-01-01")
        hold = 6
    res = Backtester(preset(strat.market), holdout_months=hold).run(
        strat, data, attempts=AttemptTracker(tmp_path / "a.json")
    )
    assert len(res.equity) > 100
    assert res.metrics.max_drawdown <= 0
    assert res.attempts["distinct_attempts"] == 1


def test_attempt_tracker_warns_after_seven(tmp_path):
    t = AttemptTracker(tmp_path / "a.json")
    for k in range(8):
        info = t.record("vol_breakout", {"k": 0.3 + 0.05 * k}, ("KRW-BTC",))
    assert info["distinct_attempts"] == 8 and info["overfit_warning"]
    info = t.record("vol_breakout", {"k": 0.3}, ("KRW-BTC",))  # 같은 조합 재실행은 안 센다
    assert info["distinct_attempts"] == 8


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_cli_backtest_runs_on_synthetic(name, tmp_path, monkeypatch):
    """CLI 합성 데이터 경로 (5분봉 전략은 seed_of를 쓴다)."""
    from quantpilot import cli
    from quantpilot.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert cli.main(["backtest", name]) == 0


def test_param_validation():
    with pytest.raises(ValueError):
        create("vol_breakout", k=5.0)
    with pytest.raises(ValueError):
        create("vol_breakout", nope=1)
    assert isinstance(create("vol_breakout").params_schema()[0], ParamSpec)


def test_vol_breakout_no_lookahead_on_entry_day():
    """진입 봉의 종가를 바꿔도 목표가·진입 여부가 바뀌면 안 된다 (고가만 본다)."""
    strat = create("vol_breakout")
    strat.symbols = ("A",)
    df = S.daily(5, periods=60, start="2022-01-01", vol=0.05)
    df2 = df.copy()
    df2.loc[df2.index[-1], "close"] = df2["close"].iloc[-1] * 0.5
    ctx = lambda d: Context(d.index[-1], {"A": d}, {}, 1e6)
    t1, t2 = strat.on_bar(ctx(df)), strat.on_bar(ctx(df2))
    assert [(t.weight, t.price) for t in t1] == [(t.weight, t.price) for t in t2]
