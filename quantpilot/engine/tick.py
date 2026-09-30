"""TickRunner — 시장 하나의 틱 루프 (01 §3의 1~8단계, 04 §7, ADR 0002·0010).

백테스트·페이퍼·실전이 이 클래스 하나를 쓴다. 달라지는 것은 주입하는 executor·risk·pipeline·clock뿐이다.

한 틱의 순서
1. 봉 반영: BarHistory에 추가, executor에 종가 알림
2. 전략 평가: 등록 순으로 on_bar → Target 목록 (비어 있으면 판단 모델 호출 없음)
3. 리스크 사전 검사: 할트면 진입 target 제거 (청산은 유지)
4~5. 판단 파이프라인: 진입 target에만. size_multiplier 0이면 폐기
6. 사이징: 코드가 수량을 계산 (목표 비중 × 배수 × 평가액 − 현재 보유 가치)
7. 주문: executor.execute = risk.check → broker.submit (불변식 #9)
8. 이벤트 발행: tick·signal·judgment·order·fill·warning

청산 우선: 비중을 줄이는 target을 먼저 체결해야 그 현금으로 늘리는 target을 낼 수 있다. 여러 전략이 같은 심볼에
반대 target을 내도 청산이 먼저다. 정렬은 안정적이라 같은 그룹 안에서는 등록 순·전략이 준 순서를 지킨다.
on_time_exit·on_stop_check는 판단 모델을 거치지 않는다 (01 §3: 청산은 2·6·7만 탄다).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from quantpilot.backtest.costs import CostModel
from quantpilot.core.events import BarClosed, FillEvent, OrderEvent, SignalEvent, TradeEvent
from quantpilot.core.models import Fill, Market, Order, OrderType, Position, Side, Target
from quantpilot.core.ports import (
    Clock,
    EventBus,
    FeatureBuilder,
    JudgmentPipeline,
    OrderExecutor,
    RiskGate,
)
from quantpilot.engine.history import BarHistory
from quantpilot.strategies.base import Context, Strategy

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickSnapshot:
    """틱 시작 시점(주문 전)의 평가액. 사이징과 회전율 계산의 기준."""

    market: Market
    ts: datetime
    equity: float


@dataclass
class _Candidate:
    strategy: Strategy
    target: Target
    ctx: Context | None
    grows: bool


class TickRunner:
    """시장 하나의 틱 루프."""

    def __init__(
        self,
        market: Market,
        strategies: Sequence[Strategy],
        feature_builder: FeatureBuilder,
        pipeline: JudgmentPipeline,
        executor: OrderExecutor,
        risk: RiskGate,
        clock: Clock,
        bus: EventBus,
        *,
        cost: CostModel,
        history: BarHistory | None = None,
        allow_short: bool = False,
        min_trade_frac: float = 0.002,
    ):
        if getattr(risk, "unrestricted", False) and not executor.is_paper:
            raise ValueError("규칙 미적용 리스크 게이트는 페이퍼 브로커와만 쓸 수 있다 (ADR 0010)")
        names = [s.name for s in strategies]
        if len(set(names)) != len(names):
            raise ValueError(f"전략 이름 중복: {names}")
        self.market = Market(market)
        self.strategies = list(strategies)
        self.feature_builder = feature_builder
        self.pipeline = pipeline
        self.executor = executor
        self.risk = risk
        self.clock = clock
        self.bus = bus
        self.cost = cost
        self.history = history or BarHistory()
        self.allow_short = allow_short
        self.min_trade_frac = min_trade_frac
        self._stops: dict[str, float] = {}
        self.warnings: list[str] = []  # 백테스트 결과에 실린다

    # ---------- 이벤트 입구 ----------
    async def on_bar_closed(self, ev: BarClosed) -> None:
        """봉 1개 마감."""
        await self.on_bars_closed([ev])

    async def on_bars_closed(self, evs: Sequence[BarClosed]) -> None:
        """같은 시각에 마감된 봉 묶음. 전략은 묶음당 한 번 평가한다 (ADR 0010)."""
        if not evs:
            return
        ts = evs[0].ts
        for ev in evs:
            if Market(ev.market) != self.market:
                raise ValueError(f"{self.market.value} TickRunner에 {ev.market} 봉")
            if ev.ts != ts:
                raise ValueError(f"한 묶음의 봉 시각이 다르다: {ts} vs {ev.ts}")
        # 1. 봉 반영
        for ev in evs:
            self.history.append(ev)
            self.executor.mark(ev.symbol, ev.close, ev.ts)
        bars = self.history.view()
        equity = self.executor.equity()
        await self.bus.publish("tick", TickSnapshot(self.market, ts, equity))

        # 2. 전략 평가
        positions = dict(self.executor.positions())
        cands: list[_Candidate] = []
        for s in self.strategies:
            if not self._ready(s, bars):
                continue
            if s.timeframe == "1M" and not self.clock.is_last_session_of_month(ts):
                continue
            ctx = Context(
                ts=pd.Timestamp(ts), bars=bars, positions=positions, equity=equity, params=s.params
            )
            for t in s.on_bar(ctx) or []:
                cands.append(_Candidate(s, t, ctx, self._increases(t, bars, positions, equity)))
        if not cands:
            return

        # 3. 리스크 사전 검사 — 할트면 진입만 제거
        if self.risk.halted_reason:
            dropped = [c for c in cands if c.grows]
            if dropped:
                log.info(
                    "할트로 진입 target 제거",
                    extra={"reason": self.risk.halted_reason, "count": len(dropped)},
                )
            cands = [c for c in cands if not c.grows]

        # 청산 우선 정렬 (안정 정렬)
        cands.sort(key=lambda c: c.grows)
        for c in cands:
            t = c.target
            if t.symbol not in bars or self.history.last_ts(t.symbol) != pd.Timestamp(ts):
                continue  # 이 봉에 데이터 없는 심볼
            bar = bars[t.symbol].iloc[-1]
            ref = self._ref_price(t, bar, ts)
            await self._act(c, ref, equity, ts)

    async def on_time_exit(self, strategy_name: str) -> None:
        """scheduler가 호출. 그 전략의 열린 포지션을 전부 시장가 청산한다 (판단 모델 없음)."""
        s = self._strategy(strategy_name)
        ts = self.clock.now()
        equity = self.executor.equity()
        for sym, p in list(self.executor.positions().items()):
            if p.strategy != strategy_name or not p.is_open:
                continue
            t = Target(sym, 0.0, reason="time_exit")
            await self._act(
                _Candidate(s, t, None, False), self.executor.last_price(sym), equity, ts
            )

    async def on_stop_check(self, ev: TradeEvent) -> None:
        """체결가마다 호출. 진입 때 받은 stop을 이탈하면 즉시 청산한다 (판단 모델·LLM 없음)."""
        self.executor.mark(ev.symbol, ev.price, ev.ts)
        stop = self._stops.get(ev.symbol)
        pos = self.executor.positions().get(ev.symbol)
        if stop is None or pos is None or not pos.is_open:
            return
        breached = ev.price <= stop if pos.qty > 0 else ev.price >= stop
        if not breached:
            return
        s = self._strategy(pos.strategy)
        t = Target(ev.symbol, 0.0, reason=f"stop {stop:.6g} 이탈")
        await self._act(_Candidate(s, t, None, False), ev.price, self.executor.equity(), ev.ts)

    # ---------- 4~8단계 ----------
    async def _act(self, c: _Candidate, ref: float, equity: float, ts: datetime) -> None:
        s, t = c.strategy, c.target
        if t.weight < 0 and not self.allow_short:
            return
        kind = "rebalance" if s.timeframe == "1M" else ("entry" if c.grows else "exit")
        signal = SignalEvent(self.market, s.name, t, kind, ts)
        await self.bus.publish("signal", signal)

        # 4~5. 판단 파이프라인 — 진입에만
        mult = 1.0
        if c.grows:
            state = self.feature_builder.build(
                symbol=t.symbol, strategy=s.name, target=t, ctx=c.ctx
            )
            je = await self.pipeline.evaluate(signal, state)
            await self.bus.publish("judgment", je)
            mult = je.size_multiplier
            if mult <= 0:
                return

        # 6. 사이징
        pos = self.executor.positions().get(t.symbol)
        sized = self._size(t, ref, equity, mult, pos)
        if sized is None:
            return
        side, qty = sized
        order = Order(
            t.symbol,
            side,
            qty,
            OrderType.MARKET,
            strategy=s.name,
            reason=t.reason,
            stop=t.stop,
            market=self.market,
            ts=pd.Timestamp(ts).to_pydatetime(),
            size_multiplier=mult if c.grows else None,
            paper=self.executor.is_paper,
        )

        # 7. risk.check → submit
        res = await self.executor.execute(
            order,
            equity=equity,
            price=ref,
            positions=dict(self.executor.positions()),
            horizon=s.horizon,
            intraday_exposure=self._intraday_exposure(),
        )

        # 8. 발행
        if isinstance(res, Fill):
            await self.bus.publish("fill", FillEvent(res))
            now_pos = self.executor.positions().get(t.symbol)
            if now_pos is None or not now_pos.is_open:
                self._stops.pop(t.symbol, None)
            elif c.grows and t.stop is not None:
                self._stops[t.symbol] = t.stop
        else:
            await self.bus.publish("order", OrderEvent(res, order.ts or ts))
            if res.reject_reason:
                log.info(
                    "주문 거부",
                    extra={"symbol": t.symbol, "strategy": s.name, "reason": res.reject_reason},
                )

    def _size(
        self, t: Target, ref: float, equity: float, mult: float, pos: Position | None
    ) -> tuple[Side, float] | None:
        """목표 비중 → (방향, 수량). 거래할 게 없으면 None."""
        held = pos.qty if pos else 0.0
        delta_value = t.weight * mult * equity - held * ref
        if abs(delta_value) < self.min_trade_frac * equity and t.weight != 0:
            return None
        if t.weight == 0 and not (pos and pos.is_open):
            return None
        side = Side.BUY if delta_value > 0 else Side.SELL
        px = self.cost.fill_price(ref, side)
        qty = abs(delta_value) / px
        cash = self.executor.cash()
        if side == Side.BUY and qty * px * (1 + self.cost.fee_rate) > cash + 1e-9:
            qty = max(0.0, cash / (px * (1 + self.cost.fee_rate)))  # 현금 한도 (수수료 포함)
            # 부동소수 반올림으로 gross+fee가 현금을 1ulp 넘으면 브로커가 거부한다 → 들어갈 때까지 깎는다
            while qty > 0 and qty * px + self.cost.fee(qty * px) > cash:
                qty = math.nextafter(qty, 0.0)
        if side == Side.SELL and not self.allow_short:
            qty = min(qty, max(held, 0.0))  # 보유량 초과 매도 금지 (ADR 0010)
        if qty <= 0:
            return None
        return side, qty

    # ---------- 보조 ----------
    def _ref_price(self, t: Target, bar: pd.Series, ts: datetime) -> float:
        """체결 기준가: Target.price가 있으면 봉 고가·저가 범위로 클립, 없으면 종가."""
        if t.price is None:
            return float(bar["close"])
        if not (bar["low"] <= t.price <= bar["high"]):
            msg = f"{pd.Timestamp(ts).date()} {t.symbol}: 지정가 {t.price:.4g} 봉 범위 밖 → 클립"
            log.debug(msg, extra={"symbol": t.symbol})
            self.warnings.append(msg)
        return float(np.clip(t.price, bar["low"], bar["high"]))

    @staticmethod
    def _ready(s: Strategy, bars: Mapping[str, pd.DataFrame]) -> bool:
        if not any(sym in bars for sym in s.symbols):
            return False
        return all(len(v) >= s.warmup_bars for sym, v in bars.items() if sym in s.symbols)

    @staticmethod
    def _increases(
        t: Target,
        bars: Mapping[str, pd.DataFrame],
        positions: Mapping[str, Position],
        equity: float,
    ) -> bool:
        """이 Target이 현재 보유 가치보다 비중을 늘리는 주문인지 (진입 판정·체결 순서용)."""
        if t.weight == 0 or t.symbol not in bars:
            return False
        pos = positions.get(t.symbol)
        held = pos.qty * float(bars[t.symbol]["close"].iloc[-1]) if pos else 0.0
        return t.weight * equity > held

    def _intraday_exposure(self) -> float:
        """horizon=intraday 전략들의 현재 포지션 가치 합 (RiskManager 단타 합산 상한용)."""
        intraday = {s.name for s in self.strategies if s.horizon == "intraday"}
        return sum(
            p.qty * self.executor.last_price(sym)
            for sym, p in self.executor.positions().items()
            if p.strategy in intraday
        )

    def _strategy(self, name: str) -> Strategy:
        for s in self.strategies:
            if s.name == name:
                return s
        raise KeyError(f"등록되지 않은 전략: {name!r}")
