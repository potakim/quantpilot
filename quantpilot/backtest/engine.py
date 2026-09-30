"""이벤트 리플레이 백테스터 — 실전과 같은 `engine.tick.TickRunner`로 봉을 재생한다 (ADR 0002·0010).

- 배선: TickRunner + PaperBroker + DirectExecutor + StubPipeline(StubJudge, 게이팅 OFF) + ReplayClock.
  전략·사이징·주문 경로 코드는 페이퍼·실전과 같고, 주입하는 부품만 다르다. 전략에 '백테스트 모드' 분기가 없다.
- 리스크: 기본은 UnrestrictedRisk(계좌 규칙 미적용, 0단계와 같은 '전략 자체 성과'). apply_risk=True면 RiskManager.
- 체결가: Target.price가 있으면 그 가격(봉의 고가·저가 범위로 클립), 없으면 종가. 여기에 CostModel의
  슬리피지·수수료·세금을 반드시 적용한다. 비용 0은 allow_zero_cost=True를 명시해야만 가능하다.
- 홀드아웃: 마지막 holdout_months 개월은 기본적으로 잘라낸다. unlock_holdout=True는 실전 전환 직전 1회용.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from quantpilot.backtest import metrics as M
from quantpilot.backtest.costs import ZERO, CostModel
from quantpilot.core.events import BarClosed, FillEvent
from quantpilot.core.models import Fill, Market, Side
from quantpilot.strategies.base import Strategy

if TYPE_CHECKING:
    from quantpilot.engine.replay import ReplayClock
    from quantpilot.engine.tick import TickRunner

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
        d = {
            "strategy": self.strategy,
            "params": self.params,
            **self.metrics.to_dict(),
            "holdout_cutoff": str(self.holdout_cutoff.date())
            if self.holdout_cutoff is not None
            else None,
            "warnings": self.warnings,
        }
        if self.attempts:
            d["attempts"] = self.attempts
        return d


class Backtester:
    def __init__(
        self,
        cost: CostModel,
        initial_cash: float = 10_000_000.0,
        *,
        holdout_months: int = 12,
        unlock_holdout: bool = False,
        allow_zero_cost: bool = False,
        allow_short: bool = False,
        min_trade_frac: float = 0.002,
        apply_risk: bool = False,
    ):
        if cost == ZERO and not allow_zero_cost:
            raise ValueError(
                "비용 0 백테스트는 allow_zero_cost=True를 명시해야 합니다 (테스트 전용)"
            )
        self.cost = cost
        self.initial_cash = initial_cash
        self.holdout_months = holdout_months
        self.unlock_holdout = unlock_holdout
        self.allow_short = allow_short
        self.min_trade_frac = min_trade_frac
        self.apply_risk = apply_risk

    # ---------- 데이터 준비 ----------
    def _prepare(
        self, data: Mapping[str, pd.DataFrame]
    ) -> tuple[dict[str, pd.DataFrame], pd.Timestamp | None, set]:
        out: dict[str, pd.DataFrame] = {}
        for sym, df in data.items():
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                raise ValueError(f"{sym}: 컬럼 누락 {missing}")
            df = df.sort_index()
            if not isinstance(df.index, pd.DatetimeIndex):
                raise ValueError(f"{sym}: DatetimeIndex 필요")  # noqa: TRY004 — 입력 검증 오류는 ValueError로 통일
            out[sym] = df[list(REQUIRED_COLS)].astype(float)
        end = max(df.index[-1] for df in out.values())
        full = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in out.values()])))
        month_last = set(pd.Series(full, index=full).groupby([full.year, full.month]).last())
        cutoff = None
        if self.holdout_months > 0 and not self.unlock_holdout:
            cutoff = end - pd.DateOffset(months=self.holdout_months)
            out = {s: df[df.index <= cutoff] for s, df in out.items()}
            out = {s: df for s, df in out.items() if len(df) > 0}
            month_last = {
                t for t in month_last if t <= cutoff
            }  # 컷으로 생긴 부분월은 월말이 아니다
        return out, cutoff, month_last

    # ---------- 실행 ----------
    def run(
        self, strategy: Strategy, data: Mapping[str, pd.DataFrame], attempts=None
    ) -> BacktestResult:
        """TickRunner로 봉을 하나씩 재생한다. 실전과 같은 on_bar·사이징·risk.check→submit 경로."""
        # engine → execution → paper → backtest 순환 import를 피하려고 여기서 불러온다
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
        from quantpilot.judgment.stub import StubJudge, StubPipeline

        bars, cutoff, month_last = self._prepare(data)
        if not bars:
            raise ValueError("홀드아웃을 제외하면 데이터가 없습니다")
        market = Market(strategy.market)
        clock = ReplayClock(month_last)
        broker = PaperBroker(market, self.cost, self.initial_cash, allow_short=self.allow_short)
        risk = RiskManager() if self.apply_risk else UnrestrictedRisk()
        bus = CollectingBus(("tick", "fill"))
        runner = TickRunner(
            market,
            [strategy],
            StubFeatureBuilder(market.value),
            StubPipeline(StubJudge(), gating=False),
            DirectExecutor(broker, risk),
            risk,
            clock,
            bus,
            cost=self.cost,
            history=BarHistory(bars),
            allow_short=self.allow_short,
            min_trade_frac=self.min_trade_frac,
        )
        tf = "1d" if strategy.timeframe == "1M" else strategy.timeframe
        by_ts: dict[pd.Timestamp, list[BarClosed]] = defaultdict(list)
        for sym, df in bars.items():
            for ts, o, h, lo, c, v in df.itertuples(name=None):
                by_ts[ts].append(BarClosed(market, sym, tf, ts.to_pydatetime(), o, h, lo, c, v))
        equity = asyncio.run(self._replay(runner, clock, by_ts))

        eq = pd.Series(equity).sort_index()
        fills, trade_returns, total_costs, turnover = _fill_stats(bus.events)
        m = M.compute(eq, trade_returns, total_costs, turnover, initial_equity=self.initial_cash)
        warnings = list(runner.warnings)
        att = (
            attempts.record(strategy.name, strategy.params, strategy.symbols) if attempts else None
        )
        if att and att["overfit_warning"]:
            warnings.append(
                f"과최적화 경고: 서로 다른 파라미터 {att['distinct_attempts']}회 시도 (> {att['warn_after']})"
            )
        return BacktestResult(
            strategy.name, dict(strategy.params), m, eq, fills, cutoff, warnings, att
        )

    @staticmethod
    async def _replay(
        runner: TickRunner, clock: ReplayClock, by_ts: Mapping[pd.Timestamp, list[BarClosed]]
    ) -> dict[pd.Timestamp, float]:
        """타임라인 순서로 봉 묶음을 TickRunner에 넣고 봉마다 평가액을 기록한다."""
        equity: dict[pd.Timestamp, float] = {}
        for ts in sorted(by_ts):
            clock.set(ts.to_pydatetime())
            await runner.on_bars_closed(by_ts[ts])
            equity[ts] = runner.executor.equity()
        return equity


def _fill_stats(events: list[tuple[str, object]]) -> tuple[list[Fill], list[float], float, float]:
    """발행된 tick·fill 이벤트 → (체결, 거래별 수익률, 총비용, 회전율). 회전율은 틱 시작 평가액 기준."""
    fills: list[Fill] = []
    trade_returns: list[float] = []
    total_costs = turnover = 0.0
    tick_equity = 0.0
    held: dict[str, float] = {}
    basis: dict[str, float] = {}
    for _, ev in events:
        if not isinstance(ev, FillEvent):
            tick_equity = ev.equity  # type: ignore[attr-defined]  # TickSnapshot
            continue
        f = ev.fill
        fills.append(f)
        total_costs += f.fee + f.tax
        turnover += f.gross / max(tick_equity, 1e-9)
        qty = held.get(f.symbol, 0.0)
        if f.side == Side.BUY:
            held[f.symbol] = qty + f.qty
            basis[f.symbol] = basis.get(f.symbol, 0.0) + f.gross + f.fee
        else:
            before = basis.get(f.symbol, 0.0)
            realized = before * (min(1.0, f.qty / qty) if qty else 0.0)
            basis[f.symbol] = before - realized
            held[f.symbol] = qty - f.qty
            if realized > 0:
                trade_returns.append((f.gross - f.fee - f.tax) / realized - 1)
            if abs(held[f.symbol]) <= 1e-12:
                held[f.symbol] = 0.0
                basis.pop(f.symbol, None)
    return fills, trade_returns, total_costs, turnover
