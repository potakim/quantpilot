"""백테스트 화면 지표: 벤치마크·구간별 성과 (ADR 0033)."""

from __future__ import annotations

import pandas as pd
import pytest

from quantpilot.api.routes.backtests import benchmark_curve, period_rows


def _pts(values: list[float], start: str = "2020-01-01") -> list[dict]:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return [{"ts": t.isoformat(), "v": float(v)} for t, v in zip(idx, values, strict=True)]


def _line(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def test_period_rows_skip_windows_longer_than_curve():
    """곡선보다 긴 구간은 빼고, 초과수익 = 전략 CAGR − 벤치마크 CAGR."""
    eq = _pts(_line(100, 200, 800))  # 약 2.2년
    bench = _pts(_line(100, 150, 800))
    rows = period_rows(eq, bench)
    assert [r["key"] for r in rows] == ["all", "1y"]
    assert rows[0]["excess"] == pytest.approx(rows[0]["cagr"] - rows[0]["bench_cagr"])
    assert rows[0]["cagr"] > rows[0]["bench_cagr"] > 0
    assert rows[0]["mdd"] == 0.0 and rows[0]["bench_mdd"] == 0.0  # 계속 오르는 곡선
    assert period_rows(eq, None)[0]["bench_cagr"] is None
    assert period_rows(eq, None)[0]["bench_mdd"] is None
    assert period_rows(eq[:1], None) == []


def test_period_rows_measure_drawdown_on_full_curve():
    """낙폭은 화면 표본(500점)이 아니라 전체 곡선으로 잰다 — 하루짜리 급락도 잡힌다."""
    values = _line(100, 200, 2000)
    values[1000] = values[999] * 0.5  # 하루 반토막 후 회복
    rows = period_rows(_pts(values), None)
    assert rows[0]["mdd"] == pytest.approx(-0.5)


def test_benchmark_curve_buys_and_holds_representative_symbol():
    """대표 종목을 처음에 사서 들고 있기 (비용 없음), 전략 곡선 시각에 직전 종가로 맞춘다."""
    idx = pd.date_range("2021-01-01", periods=5, freq="D")
    close = pd.Series([10.0, 11.0, 12.0, 9.0, 10.0], index=idx)
    spy = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    other = spy * 2
    equity = pd.Series([1000.0, 1001.0, 1002.0, 1003.0], index=idx[1:])
    sym, label, curve = benchmark_curve("gem", {"ACWX": other, "SPY": spy}, equity, 1000.0)
    assert (sym, label) == ("SPY", "S&P 500 보유")
    assert list(curve.index) == list(equity.index)
    assert list(curve) == pytest.approx([1000.0, 1000 * 12 / 11, 1000 * 9 / 11, 1000 * 10 / 11])
    # 대표 종목이 데이터에 없으면 첫 종목
    sym, label, _ = benchmark_curve("gem", {"ACWX": other}, equity, 1000.0)
    assert (sym, label) == ("ACWX", "ACWX 보유")
    assert benchmark_curve("gem", {}, equity, 1000.0) is None


def test_backtest_start_date_is_honored_for_upbit_and_stale_cache(monkeypatch, tmp_path):
    """업비트는 시작일부터 필요한 개수를 받고, 캐시가 시작일보다 늦게 시작하면 다시 받는다 (ADR 0033 §11)."""
    from quantpilot.api.routes.backtests import BacktestRequest, load_data
    from quantpilot.data import loader
    from quantpilot.strategies import create

    calls: list[int] = []

    def fake_upbit(market, tf="1d", count=1000, **_):
        calls.append(count)
        idx = pd.date_range(end=pd.Timestamp.now().normalize(), periods=count, freq="D")
        px = pd.Series(100.0, index=idx)
        return pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": 1.0})

    monkeypatch.setattr(loader, "upbit_candles", fake_upbit)
    strat = create("vol_breakout")
    strat.symbols = ("KRW-BTC",)
    # 시작일 없음: 기본 개수(최근 1,000일), 캐시에 저장
    load_data(BacktestRequest(strategy="vol_breakout", source="upbit"), strat, tmp_path)
    assert calls == [1000]
    # 캐시보다 이른 시작일: 캐시는 늦게 시작하므로 시작일부터 필요한 만큼 새로 받는다
    start = (pd.Timestamp.now().normalize() - pd.Timedelta(days=2000)).date().isoformat()
    req = BacktestRequest(strategy="vol_breakout", source="upbit", start=start)
    data = load_data(req, strat, tmp_path)
    assert calls[1:] == [2030]
    assert data["KRW-BTC"].index[0] <= pd.Timestamp(start) + pd.Timedelta(days=1)
