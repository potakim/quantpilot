"""관문 G1 독립 기준 구현 (ADR 0024) — 엔진과 대조, 지표 보조 함수."""

from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd
import pytest

from quantpilot.backtest import Backtester, preset
from quantpilot.backtest.reference import (
    cagr,
    max_drawdown,
    monthly_sharpe,
    reference_vol_breakout,
    within,
)
from quantpilot.core.models import Side
from quantpilot.data import synthetic as S
from quantpilot.strategies import create

SYMS = ("KRW-BTC", "KRW-ETH", "KRW-XRP")


def test_reference_does_not_reuse_strategy_or_engine_code():
    """독립 구현이어야 대조의 의미가 있다 — 전략·엔진·백테스터를 import하지 않는다."""
    src = (Path(__file__).parents[1] / "quantpilot/backtest/reference.py").read_text(
        encoding="utf-8"
    )
    for banned in ("quantpilot.strategies", "quantpilot.engine", "backtest.engine", "Backtester"):
        assert banned not in src


def test_engine_matches_reference_vol_breakout_on_synthetic():
    """같은 데이터·같은 비용에서 엔진과 독립 구현이 체결 단위로 같다 (ADR 0026 이후 완전 일치)."""
    data = S.universe(SYMS, periods=900, start="2019-01-01", vol=0.04)
    strat = create("vol_breakout")
    strat.symbols = SYMS
    res = Backtester(preset(strat.market), holdout_months=0).run(strat, data)
    cost = preset(strat.market)
    ref_eq, ref_entries = reference_vol_breakout(
        data, fee_rate=cost.fee_rate, slippage_rate=cost.slippage_rate
    )
    entries = sum(1 for f in res.fills if f.side == Side.BUY)
    assert ref_entries > 50 and entries == ref_entries
    pd.testing.assert_series_equal(
        res.equity.astype(float), ref_eq, check_names=False, check_freq=False, rtol=1e-9
    )


def test_engine_matches_reference_with_staggered_listing():
    """늦게 상장한 심볼이 준비 기간을 채우는 동안에도 다른 심볼은 매매한다 (ADR 0026)."""
    early = S.daily(1, periods=400, start="2020-01-01", vol=0.04)
    late = S.daily(2, periods=200, start="2020-07-19", vol=0.04)  # 같은 날 끝나도록 나중에 시작
    data = {"KRW-BTC": early, "KRW-SOL": late}
    strat = create("vol_breakout")
    strat.symbols = tuple(data)
    res = Backtester(preset(strat.market), holdout_months=0).run(strat, data)
    ref_eq, ref_entries = reference_vol_breakout(data)
    listed = late.index[0]
    warm = late.index[strat.warmup_bars]
    btc_during_warmup = [
        f
        for f in res.fills
        if f.symbol == "KRW-BTC" and f.side == Side.BUY and listed <= pd.Timestamp(f.ts) < warm
    ]
    assert btc_during_warmup  # SOL 준비 기간에도 BTC는 진입한다
    assert sum(1 for f in res.fills if f.side == Side.BUY) == ref_entries
    assert res.equity.iloc[-1] == pytest.approx(ref_eq.iloc[-1], rel=1e-9)


def test_same_day_reentry_is_judged_as_entry(monkeypatch):
    """어제 산 것을 오늘 시가에 팔고 다시 돌파하면, 그 매수도 진입으로 판단 파이프라인을 거친다.

    평가 시점엔 어제 보유분이 남아 있어 '비중 축소'로 분류돼 판단 모델을 건너뛰던 문제의 회귀 방지.
    """
    from quantpilot.judgment.stub import StubPipeline

    calls = []
    orig = StubPipeline.evaluate

    async def spy(self, signal, state):
        calls.append(signal.target.symbol)
        return await orig(self, signal, state)

    monkeypatch.setattr(StubPipeline, "evaluate", spy)
    data = {"KRW-BTC": S.daily(3, periods=300, start="2020-01-01", vol=0.05)}
    strat = create("vol_breakout")
    strat.symbols = ("KRW-BTC",)
    res = Backtester(preset(strat.market), holdout_months=0).run(strat, data)
    buys = [f for f in res.fills if f.side == Side.BUY]
    days = [pd.Timestamp(f.ts).normalize() for f in buys]
    assert any(
        b - a == pd.Timedelta(days=1) for a, b in itertools.pairwise(days)
    )  # 연속 진입이 있다
    assert len(calls) == len(buys)


def test_reference_sizes_on_open_not_close():
    """마지막 두 날 연속 돌파 — 마지막 날 종가를 바꿔도 그날 진입 수량은 그대로다.

    수량이 종가와 무관하면 최종 평가액 변화는 종가 변화에 정확히 비례한다. 어제 보유분을 오늘
    종가로 평가해 사이징하면(엔진의 현재 관례, ADR 0024) 수량이 종가에 따라 바뀌어 비례가 깨진다.
    """
    df = S.daily(7, periods=60, start="2020-01-01", vol=0.03)
    for d in df.index[-2:]:
        df.loc[d, "high"] = df.at[d, "open"] * 1.5  # 확실한 돌파
    finals = []
    for bump in (0.0, 0.1, 0.2):
        x = df.copy()
        last = x.index[-1]
        x.loc[last, "close"] = df.at[last, "close"] * (1 + bump)
        x.loc[last, "high"] = max(x.at[last, "high"], x.at[last, "close"])
        eq, _ = reference_vol_breakout({"A": x})
        finals.append(eq.iloc[-1])
    assert finals[1] > finals[0]
    assert (finals[2] - finals[0]) == pytest.approx(2 * (finals[1] - finals[0]), rel=1e-9)


def test_monthly_drawdown_ignores_intra_month_dip():
    """월말 값만 보면 한 달 안의 일시 급락은 보이지 않는다 (공개 자료의 MDD 방식)."""
    idx = pd.date_range("2020-01-01", "2020-03-31", freq="D")
    eq = pd.Series(100.0, index=idx)
    eq.loc["2020-02-10":"2020-02-20"] = 70.0  # 2월 중순 −30% 후 회복
    assert max_drawdown(eq) == pytest.approx(-0.30)
    assert max_drawdown(eq, "ME") == pytest.approx(0.0)


def test_metric_helpers():
    idx = pd.date_range("2020-01-01", periods=366 * 2, freq="D")
    eq = pd.Series([100 * 1.1 ** (i / 366) for i in range(len(idx))], index=idx)
    assert cagr(eq) == pytest.approx(0.1, rel=1e-2)
    assert monthly_sharpe(eq) > 0
    assert within(0.083, 0.091, 0.2) and not within(0.06, 0.091, 0.2)
