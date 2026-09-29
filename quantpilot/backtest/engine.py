"""이벤트 리플레이 백테스터.

- 전략의 `on_bar`를 봉마다(월간 전략은 월말 봉에만) 호출하고, 반환된 Target을 같은 봉 안에서 체결한다.
- 체결가: Target.price가 있으면 그 가격(봉의 고가·저가 범위로 클립), 없으면 종가. 여기에 CostModel의
  슬리피지·수수료·세금을 반드시 적용한다. 비용 0은 allow_zero_cost=True를 명시해야만 가능하다.
- 홀드아웃: 마지막 holdout_months 개월은 기본적으로 잘라낸다. unlock_holdout=True는 실전 전환 직전 1회용.
- 실전과 같은 전략 코드를 쓴다. 전략에 '백테스트 모드' 분기가 없다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from quantpilot.backtest import metrics as M
from quantpilot.backtest.costs import ZERO, CostModel
from quantpilot.core.models import Fill, Position, Side, Target
from quantpilot.strategies.base import Context, Strategy

log = logging.getLogger(__name__)

REQUIRED_COLS = ("open", "high", "low", "close", "volume")


@dataclass
class BacktestResult:
    strategy: str
    params: dict
    metrics: M.Metrics
    equity: pd.Series
    fills: list[Fill]
    holdout_cutoff: pd.Timestamp | None
    warnings: list[str] = field(default_factory=list)
    attempts: dict | None = None

    def summary(self) -> dict:
        d = {"strategy": self.strategy, "params": self.params, **self.metrics.to_dict(),
             "holdout_cutoff": str(self.holdout_cutoff.date()) if self.holdout_cutoff is not None else None,
             "warnings": self.warnings}
        if self.attempts:
            d["attempts"] = self.attempts
        return d


class Backtester:
    def __init__(self, cost: CostModel, initial_cash: float = 10_000_000.0, *,
                 holdout_months: int = 12, unlock_holdout: bool = False,
                 allow_zero_cost: bool = False, allow_short: bool = False,
                 min_trade_frac: float = 0.002):
        if cost == ZERO and not allow_zero_cost:
            raise ValueError("비용 0 백테스트는 allow_zero_cost=True를 명시해야 합니다 (테스트 전용)")
        self.cost = cost
        self.initial_cash = initial_cash
        self.holdout_months = holdout_months
        self.unlock_holdout = unlock_holdout
        self.allow_short = allow_short
        self.min_trade_frac = min_trade_frac

    # ---------- 데이터 준비 ----------
    def _prepare(self, data: Mapping[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], pd.Timestamp | None]:
        out: dict[str, pd.DataFrame] = {}
        for sym, df in data.items():
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                raise ValueError(f"{sym}: 컬럼 누락 {missing}")
            df = df.sort_index()
            if not isinstance(df.index, pd.DatetimeIndex):
                raise ValueError(f"{sym}: DatetimeIndex 필요")
            out[sym] = df[list(REQUIRED_COLS)].astype(float)
        end = max(df.index[-1] for df in out.values())
        cutoff = None
        if self.holdout_months > 0 and not self.unlock_holdout:
            cutoff = end - pd.DateOffset(months=self.holdout_months)
            out = {s: df[df.index <= cutoff] for s, df in out.items()}
            out = {s: df for s, df in out.items() if len(df) > 0}
        return out, cutoff

    # ---------- 실행 ----------
    def run(self, strategy: Strategy, data: Mapping[str, pd.DataFrame],
            attempts=None) -> BacktestResult:
        bars, cutoff = self._prepare(data)
        if not bars:
            raise ValueError("홀드아웃을 제외하면 데이터가 없습니다")
        timeline = sorted(set().union(*[set(df.index) for df in bars.values()]))
        monthly = strategy.timeframe == "1M"
        month_last = set()
        if monthly:
            idx = pd.DatetimeIndex(timeline)
            month_last = set(pd.Series(idx, index=idx).groupby([idx.year, idx.month]).last())

        cash = self.initial_cash
        positions: dict[str, Position] = {}
        fills: list[Fill] = []
        equity_curve: dict[pd.Timestamp, float] = {}
        trade_returns: list[float] = []
        cost_basis: dict[str, float] = {}
        warnings: list[str] = []
        turnover = 0.0
        total_costs = 0.0
        last_price: dict[str, float] = {}

        for ts in timeline:
            ts = pd.Timestamp(ts)
            visible = {}
            for sym, df in bars.items():
                k = df.index.searchsorted(ts, side="right")
                if k == 0:
                    continue
                visible[sym] = df.iloc[:k]
                if df.index[k - 1] == ts:
                    last_price[sym] = float(df["close"].iloc[k - 1])
            if not visible:
                continue

            equity = cash + sum(p.qty * last_price.get(s, p.avg_price) for s, p in positions.items())
            targets: list[Target] = []
            ready = all(len(v) >= strategy.warmup_bars for s, v in visible.items() if s in strategy.symbols) \
                and any(s in visible for s in strategy.symbols)
            if ready and (not monthly or ts in month_last):
                ctx = Context(ts=ts, bars=visible, positions=positions, equity=equity, params=strategy.params)
                targets = strategy.on_bar(ctx) or []

            for t in targets:
                if t.symbol not in visible or visible[t.symbol].index[-1] != ts:
                    continue                                   # 이 봉에 데이터 없는 심볼
                bar = visible[t.symbol].iloc[-1]
                ref = float(bar["close"]) if t.price is None else float(np.clip(t.price, bar["low"], bar["high"]))
                if t.price is not None and not (bar["low"] <= t.price <= bar["high"]):
                    warnings.append(f"{ts.date()} {t.symbol}: 지정가 {t.price:.4g} 봉 범위 밖 → 클립")
                if t.weight < 0 and not self.allow_short:
                    continue
                pos = positions.get(t.symbol) or Position(t.symbol, strategy=strategy.name)
                target_value = t.weight * equity
                current_value = pos.qty * ref
                delta_value = target_value - current_value
                if abs(delta_value) < self.min_trade_frac * equity and t.weight != 0:
                    continue
                if t.weight == 0 and not pos.is_open:
                    continue
                side = Side.BUY if delta_value > 0 else Side.SELL
                px = self.cost.fill_price(ref, side)
                qty = abs(delta_value) / px
                if side == Side.BUY and qty * px * (1 + self.cost.fee_rate) > cash + 1e-9:
                    qty = max(0.0, cash / (px * (1 + self.cost.fee_rate)))   # 현금 한도 (수수료 포함)
                if qty <= 0:
                    continue
                gross = qty * px
                fee, tax = self.cost.fee(gross), self.cost.tax(gross, side)
                total_costs += fee + tax
                turnover += gross / max(equity, 1e-9)

                if side == Side.BUY:
                    cash -= gross + fee
                    new_qty = pos.qty + qty
                    pos.avg_price = (pos.avg_price * pos.qty + px * qty) / new_qty if new_qty else 0.0
                    pos.qty = new_qty
                    pos.opened_at = pos.opened_at or ts
                    cost_basis[t.symbol] = cost_basis.get(t.symbol, 0.0) + gross + fee
                else:
                    cash += gross - fee - tax
                    basis_before = cost_basis.get(t.symbol, 0.0)
                    frac = min(1.0, qty / pos.qty) if pos.qty else 0.0
                    realized_basis = basis_before * frac
                    cost_basis[t.symbol] = basis_before - realized_basis
                    pos.qty -= qty
                    if realized_basis > 0:
                        trade_returns.append((gross - fee - tax) / realized_basis - 1)
                    if not pos.is_open:
                        pos.qty, pos.avg_price, pos.opened_at = 0.0, 0.0, None
                        cost_basis.pop(t.symbol, None)
                positions[t.symbol] = pos
                fills.append(Fill(order_id="bt", symbol=t.symbol, side=side, qty=qty, price=px,
                                  fee=fee, tax=tax, ts=ts.to_pydatetime(), strategy=strategy.name,
                                  reason=t.reason))

            equity_curve[ts] = cash + sum(p.qty * last_price.get(s, p.avg_price) for s, p in positions.items())

        eq = pd.Series(equity_curve).sort_index()
        m = M.compute(eq, trade_returns, total_costs, turnover, initial_equity=self.initial_cash)
        att = attempts.record(strategy.name, strategy.params, strategy.symbols) if attempts else None
        if att and att["overfit_warning"]:
            warnings.append(f"과최적화 경고: 서로 다른 파라미터 {att['distinct_attempts']}회 시도 (> {att['warn_after']})")
        return BacktestResult(strategy.name, dict(strategy.params), m, eq, fills, cutoff, warnings, att)
