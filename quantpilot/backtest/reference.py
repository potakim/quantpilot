"""관문 G1용 독립 기준 구현 (ADR 0024) — 전략·엔진 코드를 쓰지 않고 docs/05 규칙만으로 계산한다.

공개 기준 수치가 없는 전략은 "엔진이 규칙대로 도는가"를 이 구현과의 대조로 확인한다.
엔진(TickRunner) 결과와 이 구현이 크게 다르면 둘 중 하나가 규칙을 잘못 옮긴 것이다.

변동성 돌파 (05 §1), 일봉:
- 어제 산 것은 오늘 시가에 판다 (슬리피지·수수료 적용)
- 목표가 = 오늘 시가 + 전일 변동폭 × K. 오늘 고가가 목표가에 닿으면 목표가에 산다
- 비중 = min(max_weight, min(1, target_vol / 전일 변동폭%) × 이평 스코어) / 코인 수
- 이평 스코어 = 전일 종가가 이평 위에 있는 개수 / 이평 개수 (전일까지의 종가로)
- 사이징 기준 평가액 = 시가 청산 직후의 현금 + 남은 보유분(직전 종가). 오늘 종가는 쓰지 않는다
- 준비 기간(warmup)은 코인마다 따로 센다
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd


def reference_vol_breakout(
    data: Mapping[str, pd.DataFrame],
    *,
    k: float = 0.5,
    target_vol: float = 0.01,
    ma_windows: Sequence[int] = (3, 5, 10, 20),
    max_weight: float = 1.0,
    warmup: int = 25,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    initial_cash: float = 10_000_000.0,
    min_trade_frac: float = 0.002,
) -> tuple[pd.Series, int]:
    """일봉 OHLC → (종가 기준 평가액 곡선, 진입 횟수)."""
    syms = list(data)
    n_syms = max(1, len(syms))
    timeline = sorted(set().union(*[set(df.index) for df in data.values()]))
    cash = float(initial_cash)
    qty = dict.fromkeys(syms, 0.0)
    last_close: dict[str, float] = {}
    curve: dict[pd.Timestamp, float] = {}
    entries = 0
    for t in timeline:
        today = {s: df for s, df in data.items() if t in df.index}
        # 1) 어제 산 것은 오늘 시가에 청산
        for s, df in today.items():
            if qty[s] > 0:
                gross = qty[s] * float(df.at[t, "open"]) * (1 - slippage_rate)
                cash += gross * (1 - fee_rate)
                qty[s] = 0.0
        # 2) 사이징 기준 평가액 (오늘 종가 미사용)
        equity = cash + sum(qty[s] * last_close.get(s, 0.0) for s in syms)
        # 3) 돌파 진입
        for s, df in today.items():
            i = df.index.get_loc(t)
            if i < warmup:
                continue
            p1 = df.iloc[i - 1]
            rng = float(p1["high"] - p1["low"])
            if rng <= 0:
                continue
            target = float(df.at[t, "open"]) + rng * k
            if float(df.at[t, "high"]) < target:
                continue
            closes = df["close"].iloc[:i]
            score = sum(closes.iloc[-1] > closes.tail(w).mean() for w in ma_windows) / len(
                ma_windows
            )
            vol_w = min(1.0, target_vol / (rng / float(p1["close"])))
            weight = min(max_weight, vol_w * score) / n_syms
            value = weight * equity
            if weight <= 0 or value < min_trade_frac * equity:
                continue
            px = target * (1 + slippage_rate)
            q = min(value / px, cash / (px * (1 + fee_rate)))
            if q <= 0:
                continue
            cash -= q * px * (1 + fee_rate)
            qty[s] += q
            entries += 1
        for s, df in today.items():
            last_close[s] = float(df.at[t, "close"])
        curve[t] = cash + sum(qty[s] * last_close.get(s, 0.0) for s in syms)
    return pd.Series(curve, dtype=float), entries


def cagr(equity: pd.Series, base: float | None = None) -> float:
    """연복리 수익률 (달력 연수 기준)."""
    eq = equity.dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    start = float(base) if base else float(eq.iloc[0])
    return float((eq.iloc[-1] / start) ** (1 / years) - 1) if years > 0 else 0.0


def max_drawdown(equity: pd.Series, freq: str | None = None) -> float:
    """최대 낙폭(음수). freq="ME"면 월말 값만으로 (공개 자료의 월간 MDD와 같은 방식)."""
    eq = equity.dropna()
    if freq:
        eq = eq.resample(freq).last().dropna()
    return float((eq / eq.cummax() - 1).min())


def monthly_sharpe(equity: pd.Series) -> float:
    """월말 수익률로 계산한 연환산 샤프 (무위험 0)."""
    r = equity.dropna().resample("ME").last().pct_change().dropna()
    return float(r.mean() / r.std() * 12**0.5) if len(r) > 1 and r.std() > 0 else 0.0


def within(value: float, ref: float, rel: float) -> bool:
    """|value - ref| <= rel × |ref|."""
    return abs(value - ref) <= rel * abs(ref)
