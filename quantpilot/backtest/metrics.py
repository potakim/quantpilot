"""성과 지표. 자산 곡선(equity Series, DatetimeIndex)에서 계산한다."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    start: str
    end: str
    years: float
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    volatility: float
    n_trades: int
    win_rate: float
    avg_trade_return: float
    turnover_per_year: float
    total_costs: float

    def to_dict(self) -> dict:
        return asdict(self)


def _periods_per_year(index: pd.DatetimeIndex) -> float:
    """연환산 계수 = 관측 봉 수 ÷ 달력 연수 (ADR 0023).

    주식 일봉은 ≈252, 연중무휴 코인 일봉은 ≈365, 분봉은 실제 거래 시간만큼이 저절로 나온다.
    """
    if len(index) < 2:
        return 252.0
    years = (index[-1] - index[0]).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return 252.0
    return (len(index) - 1) / years


def drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1


def compute(
    equity: pd.Series,
    trade_returns: list[float],
    total_costs: float,
    turnover: float,
    initial_equity: float | None = None,
) -> Metrics:
    equity = equity.dropna()
    if len(equity) < 2:
        raise ValueError("equity curve too short")
    start, end = equity.index[0], equity.index[-1]
    years = max((end - start).days / 365.25, 1 / 365.25)
    base = initial_equity if initial_equity else float(equity.iloc[0])
    total = float(equity.iloc[-1] / base - 1)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    rets = equity.pct_change().dropna()
    ppy = _periods_per_year(equity.index)
    vol = float(rets.std() * math.sqrt(ppy)) if len(rets) > 1 else 0.0
    sharpe = (
        float(rets.mean() / rets.std() * math.sqrt(ppy))
        if len(rets) > 1 and rets.std() > 0
        else 0.0
    )
    mdd = float(drawdown(equity).min())
    wins = [r for r in trade_returns if r > 0]
    return Metrics(
        start=str(start.date()),
        end=str(end.date()),
        years=round(years, 2),
        total_return=total,
        cagr=cagr,
        max_drawdown=mdd,
        sharpe=sharpe,
        volatility=vol,
        n_trades=len(trade_returns),
        win_rate=len(wins) / len(trade_returns) if trade_returns else 0.0,
        avg_trade_return=float(np.mean(trade_returns)) if trade_returns else 0.0,
        turnover_per_year=turnover / years,
        total_costs=total_costs,
    )
