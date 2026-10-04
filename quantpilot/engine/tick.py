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

일봉 모드(ADR 0025): `daily`(DailyRollup)를 주면 일봉·월간 전략에는 분봉 대신 거래일 일봉(마지막 행 = 진행 중인
오늘 봉)을 넘기고, (전략, 심볼)마다 거래일당 진입 한 번·청산 한 번만 처리한다. 실시간 엔진 전용이며 백테스트는
일봉을 직접 재생하므로 쓰지 않는다.

섀도 원장(06 §6.2, ADR 0016): `shadow` executor를 주면 ON이 받은 전략 신호를 그대로 게이팅 없이(배수 1.0)
섀도 계좌에도 낸다. 판단은 한 번만 부르고, 섀도는 자기 risk.check → submit·포지션·손절선을 따로 가진다.
섀도의 체결·주문은 버스에 발행하지 않는다(화면·알림이 실제 페이퍼 원장과 섞이지 않게).

신호 id(t18): `record_signal`을 주면 ON 원장 신호를 먼저 기록해 받은 id를 SignalEvent와 그 신호로 만든 ON 주문에
싣는다. 그래야 OrderExecutor가 signals.outcome(ordered·filled·risk_rejected)을 남긴다. 섀도 주문에는 싣지 않는다.

전략 설정(ADR 0032): 실시간 엔진은 `apply_configs`로 켜기/끄기·배분·파라미터를 넘긴다. 사이징·진입 판정의 기준은
평가액 × 배분이고, 꺼졌거나 배분 0인 전략은 진입만 버린다(청산·손절·시간 청산은 그대로). 리스크 검사에는 계좌
전체 평가액을 넘긴다. `apply_configs`를 부르지 않는 백테스트는 모든 전략이 켜짐·배분 1.0이다.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

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
from quantpilot.engine.daily import DailyRollup
from quantpilot.engine.history import BarHistory
from quantpilot.strategies.base import Context, Strategy

log = logging.getLogger(__name__)
RULE_DAYS = 35  # 규칙 미충족 집계를 남기는 거래일 수 (ADR 0027)


@dataclass(frozen=True)
class TickSnapshot:
    """틱 시작 시점(주문 전)의 평가액. 사이징과 회전율 계산의 기준."""

    market: Market
    ts: datetime
    equity: float


@dataclass
class _Book:
    """원장 하나: ON(실제 게이팅) 또는 섀도(게이팅 OFF)."""

    executor: OrderExecutor
    shadow: bool = False
    stops: dict[str, float] = field(default_factory=dict)


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
        shadow: OrderExecutor | None = None,
        record_signal: Callable[[SignalEvent], Awaitable[int]] | None = None,
        daily: DailyRollup | None = None,
    ):
        if getattr(risk, "unrestricted", False) and not executor.is_paper:
            raise ValueError("규칙 미적용 리스크 게이트는 페이퍼 브로커와만 쓸 수 있다 (ADR 0010)")
        if shadow is not None and (not shadow.is_paper or shadow is executor):
            raise ValueError("섀도 원장은 별도의 페이퍼 executor여야 한다 (07 문서, ADR 0016)")
        names = [s.name for s in strategies]
        if len(set(names)) != len(names):
            raise ValueError(f"전략 이름 중복: {names}")
        self.market = Market(market)
        self.strategies = list(strategies)
        self.feature_builder = feature_builder
        self.pipeline = pipeline
        self.executor = executor
        self.shadow = shadow
        self.record_signal = record_signal
        self.risk = risk
        self.clock = clock
        self.bus = bus
        self.cost = cost
        self.history = history or BarHistory()
        self.allow_short = allow_short
        self.min_trade_frac = min_trade_frac
        self._on = _Book(executor)
        self._off = _Book(shadow, shadow=True) if shadow is not None else None
        self.shadow_signals = 0  # 섀도에 넘긴 전략 신호 수 = ON이 발행한 전략 신호 수
        self.warnings: list[str] = []  # 백테스트 결과에 실린다
        # 실시간 분봉으로 일봉 전략을 돌릴 때만 (ADR 0025). 백테스트는 일봉을 직접 넣으므로 None
        self.daily = daily
        self._entered: dict[
            tuple[str, str], pd.Timestamp
        ] = {}  # (전략, 심볼) → 진입을 처리한 거래일
        self._exited: dict[
            tuple[str, str], pd.Timestamp
        ] = {}  # (전략, 심볼) → 청산을 처리한 거래일
        # 규칙 미충족 집계 (ADR 0027): 거래일 → 전략 → {evaluated: 평가한 종목, signaled: 진입 target 낸 종목}
        self.rule_days: dict[str, dict[str, dict[str, set[str]]]] = {}
        self.rule_dirty = False  # MarketEngine이 우편함에 쓴 뒤 False로 돌린다
        self.done_dirty = False  # 처리 표시가 바뀌었다 → 우편함에 쓴다 (ADR 0028)
        # 전략 설정 (ADR 0032): 이름 → (켜짐, 배분). None이면 백테스트 = 모두 켜짐·배분 1.0
        self._configs: dict[str, tuple[bool, float]] | None = None
        self._params: dict[
            str, dict[str, Any]
        ] = {}  # 마지막으로 반영한 설정 params (바뀔 때만 다시 만든다)

    # ---------- 전략 설정 (ADR 0032) ----------
    def apply_configs(self, configs: Mapping[str, Mapping[str, Any]]) -> None:
        """유효 전략 설정 {이름: {enabled, allocation, params}}을 반영한다. 없는 전략은 꺼짐·배분 0."""
        from quantpilot.strategies import create

        self._configs = {
            s.name: (
                bool(configs.get(s.name, {}).get("enabled", False)),
                max(0.0, float(configs.get(s.name, {}).get("allocation") or 0.0)),
            )
            for s in self.strategies
        }
        for i, s in enumerate(self.strategies):
            params = dict(configs.get(s.name, {}).get("params") or {})
            if self._params.get(s.name, {}) == params:
                continue
            try:
                self.strategies[i] = create(s.name, **params)
            except (TypeError, ValueError) as e:
                log.error(
                    "전략 파라미터 반영 실패 — 이전 파라미터 유지",
                    extra={"strategy": s.name, "error": str(e)},
                )
                continue
            self._params[s.name] = params
            log.info("전략 파라미터 반영", extra={"strategy": s.name, "params": params})

    def allocation(self, name: str) -> float:
        """사이징에 쓰는 배분 (평가액 × 배분 = 배정 자본)."""
        return 1.0 if self._configs is None else self._configs.get(name, (False, 0.0))[1]

    def entries_allowed(self, name: str) -> bool:
        """켜져 있고 배분이 0보다 큰 전략만 비중을 늘릴 수 있다."""
        if self._configs is None:
            return True
        enabled, alloc = self._configs.get(name, (False, 0.0))
        return enabled and alloc > 0

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
        # 1. 봉 반영 — 지정가 진입의 사이징 기준(보유분의 직전 가격)은 이 봉 종가를 반영하기 전에 잡아 둔다
        prev_marks = {id(b): self._marks(b.executor) for b in self._books()}
        for ev in evs:
            self.history.append(ev)
            if self.daily is not None and ev.timeframe != "1d":
                self.daily.add(ev)
            for b in self._books():
                b.executor.mark(ev.symbol, ev.close, ev.ts)
        bars = self.history.view()
        daily_bars = self.daily.view() if self.daily is not None else None
        equity = self.executor.equity()
        equities = {id(b): b.executor.equity() for b in self._books()}
        await self.bus.publish("tick", TickSnapshot(self.market, ts, equity))

        # 2. 전략 평가
        positions = dict(self.executor.positions())
        cands: list[_Candidate] = []
        for s in self.strategies:
            sbars = daily_bars if daily_bars is not None and self._daily_mode(s) else bars
            if not self._ready(s, sbars):
                continue
            if s.timeframe == "1M" and not self.clock.is_last_session_of_month(ts):
                continue
            ctx = Context(
                ts=pd.Timestamp(ts), bars=sbars, positions=positions, equity=equity, params=s.params
            )
            base = equity * self.allocation(s.name)
            got = [
                _Candidate(s, t, ctx, self._increases(t, sbars, positions, base))
                for t in s.on_bar(ctx) or []
            ]
            if self.entries_allowed(s.name):
                self._note_rules(s, sbars, got, ts)
            else:  # 꺼짐·배분 0: 비중을 늘리려는 target은 버리고 청산·축소만 남긴다 (ADR 0032)
                got = [c for c in got if not self._increases(c.target, sbars, positions, equity)]
            cands += [c for c in got if not self._done_today(c, ts)] if self._daily_mode(s) else got
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
        # 전량 청산(비중 0) → 그 밖의 축소 → 늘리기. 같은 날 '청산 후 재진입'의 진입은 평가 시점에 보유분이
        # 남아 있어 축소로 분류되므로, 전량 청산을 그보다 먼저 처리해야 진입 기준 평가액이 청산을 반영한다
        cands.sort(key=lambda c: 0 if c.target.weight == 0 else (2 if c.grows else 1))
        entry_equity: dict[int, float] = {}  # 지정가 진입 사이징 기준 — 이 봉에서 한 번만 잡는다
        for c in cands:
            t = c.target
            if t.symbol not in bars or self.history.last_ts(t.symbol) != pd.Timestamp(ts):
                continue  # 이 봉에 데이터 없는 심볼
            if c.ctx is not None:
                # 진입 여부는 앞서 처리한 청산을 반영해 다시 본다. 평가 시점에는 어제 산 보유분이 남아 있어
                # 같은 날 '청산 후 재진입'의 진입이 진입으로 분류되지 않고 판단 모델·사이징 규칙을 건너뛰었다
                now_pos = dict(self.executor.positions())
                base = equities[id(self._on)] * self.allocation(c.strategy.name)
                c = replace(c, grows=self._increases(t, c.ctx.bars, now_pos, base))
                if c.grows and not self.entries_allowed(c.strategy.name):
                    continue
            if self._daily_mode(c.strategy):
                self._mark_done(c, ts)
            # 체결 기준가는 지금 들어온 봉의 범위 안에서 정한다. 일봉 모드도 오늘 일봉이 아니라 이 분봉이다 —
            # 돌파한 그 분이면 목표가에, 이미 지난 가격(늦은 시작·할트 해제·늦은 첫 분봉)이면 현재가 쪽에 붙는다 (ADR 0028)
            ref = self._ref_price(t, bars[t.symbol].iloc[-1], ts)
            books = [
                (b, self._sizing_equity(b, c, equities, prev_marks, entry_equity))
                for b in self._books()
            ]
            await self._act(c, ref, ts, books)

    async def on_time_exit(self, strategy_name: str) -> None:
        """scheduler가 호출. 그 전략의 열린 포지션을 전부 시장가 청산한다 (판단 모델 없음)."""
        s = self._strategy(strategy_name)
        ts = self.clock.now()
        held: dict[str, list[tuple[_Book, float]]] = {}
        for b in self._books():
            eq = b.executor.equity()
            for sym, p in b.executor.positions().items():
                if p.strategy == strategy_name and p.is_open:
                    held.setdefault(sym, []).append((b, eq))
        for sym, books in held.items():
            t = Target(sym, 0.0, reason="time_exit")
            ref = books[0][0].executor.last_price(sym)
            await self._act(_Candidate(s, t, None, False), ref, ts, books)

    async def on_stop_check(self, ev: TradeEvent) -> None:
        """체결가마다 호출. 진입 때 받은 stop을 이탈하면 즉시 청산한다 (판단 모델·LLM 없음)."""
        for b in self._books():
            b.executor.mark(ev.symbol, ev.price, ev.ts)
            stop = b.stops.get(ev.symbol)
            pos = b.executor.positions().get(ev.symbol)
            if stop is None or pos is None or not pos.is_open:
                continue
            breached = ev.price <= stop if pos.qty > 0 else ev.price >= stop
            if not breached:
                continue
            s = self._strategy(pos.strategy)
            t = Target(ev.symbol, 0.0, reason=f"stop {stop:.6g} 이탈")
            c = _Candidate(s, t, None, False)
            await self._act(c, ev.price, ev.ts, [(b, b.executor.equity())])

    # ---------- 4~8단계 ----------
    async def _act(
        self, c: _Candidate, ref: float, ts: datetime, books: Sequence[tuple[_Book, float]]
    ) -> None:
        """신호 1건을 원장별로 처리한다. books는 (원장, 틱 시작 평가액) — 섀도만 있는 청산도 온다."""
        s, t = c.strategy, c.target
        if t.weight < 0 and not self.allow_short:
            return
        if c.ctx is not None and t.weight == 0 and not self._held(t.symbol, books):
            return  # 보유 없는 원장들에 대한 '비중 0'은 할 일이 없다 — 신호로 남기지 않는다 (ADR 0027)
        on_book = any(b is self._on for b, _ in books)
        kind = "rebalance" if s.timeframe == "1M" else ("entry" if c.grows else "exit")
        signal = SignalEvent(self.market, s.name, t, kind, ts)
        if on_book:
            sid = await self._record(signal)
            if sid is not None:
                signal = replace(signal, signal_id=sid)
            await self.bus.publish("signal", signal)
            if c.ctx is not None and any(b is self._off for b, _ in books):
                self.shadow_signals += 1  # 전략 신호만 센다 (손절·시간 청산은 원장별 규칙)

        # 4~5. 판단 파이프라인 — 진입에만. 섀도는 결과와 무관하게 배수 1.0 (게이팅 OFF)
        mult = 1.0
        if c.grows and on_book:
            state = self.feature_builder.build(
                symbol=t.symbol, strategy=s.name, target=t, ctx=c.ctx
            )
            je = await self.pipeline.evaluate(signal, state)
            if je.state is None:
                je = replace(je, state=state)  # judgments.state 기록용 (P1-12 EventRecorder)
            await self.bus.publish("judgment", je)
            mult = je.size_multiplier
        for b, equity in books:
            m = 1.0 if b.shadow else mult
            if c.grows and m <= 0:
                continue
            await self._order(b, c, ref, equity, m, ts, None if b.shadow else signal.signal_id)

    async def _record(self, signal: SignalEvent) -> int | None:
        """신호를 기록하고 id를 돌려준다. 기록 실패는 로그만 남긴다 — 기록 때문에 매매가 멈추면 안 된다."""
        if self.record_signal is None:
            return None
        try:
            return await self.record_signal(signal)
        except Exception:
            log.exception(
                "signal_record_failed",
                extra={"symbol": signal.target.symbol, "strategy": signal.strategy},
            )
            return None

    async def _order(
        self,
        b: _Book,
        c: _Candidate,
        ref: float,
        equity: float,
        mult: float,
        ts: datetime,
        signal_id: int | None = None,
    ) -> None:
        """6~8단계: 사이징 → risk.check → submit → 발행(섀도는 발행 안 함)."""
        s, t = c.strategy, c.target
        ex = b.executor
        # 6. 사이징 — 배정 자본(평가액 × 배분) 기준. 리스크 검사는 아래에서 계좌 전체 평가액으로 (ADR 0032)
        pos = ex.positions().get(t.symbol)
        sized = self._size(t, ref, equity * self.allocation(s.name), mult, pos, ex)
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
            paper=ex.is_paper,
            signal_id=signal_id,
        )

        # 7. risk.check → submit
        res = await ex.execute(
            order,
            equity=equity,
            price=ref,
            positions=dict(ex.positions()),
            horizon=s.horizon,
            intraday_exposure=self._intraday_exposure(ex),
        )

        # 8. 발행
        if isinstance(res, Fill):
            if not b.shadow:
                await self.bus.publish("fill", FillEvent(res))
            now_pos = ex.positions().get(t.symbol)
            if now_pos is None or not now_pos.is_open:
                b.stops.pop(t.symbol, None)
            elif c.grows and t.stop is not None:
                b.stops[t.symbol] = t.stop
        else:
            if not b.shadow:
                await self.bus.publish("order", OrderEvent(res, order.ts or ts))
            if res.reject_reason:
                log.info(
                    "주문 거부",
                    extra={
                        "symbol": t.symbol,
                        "strategy": s.name,
                        "reason": res.reject_reason,
                        "shadow": b.shadow,
                    },
                )

    def _size(
        self,
        t: Target,
        ref: float,
        equity: float,
        mult: float,
        pos: Position | None,
        ex: OrderExecutor,
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
        cash = ex.cash()
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
    def _held(symbol: str, books: Sequence[tuple[_Book, float]]) -> bool:
        """처리할 원장 중 하나라도 이 심볼 포지션을 들고 있는가."""
        for b, _ in books:
            pos = b.executor.positions().get(symbol)
            if pos is not None and pos.is_open:
                return True
        return False

    def _note_rules(
        self,
        s: Strategy,
        bars: Mapping[str, pd.DataFrame],
        got: Sequence[_Candidate],
        ts: datetime,
    ) -> None:
        """거래일별로 평가한 종목과 진입 target을 낸 종목을 모은다 (규칙 미충족 집계, ADR 0027)."""
        day = (
            (self.daily.day_of(ts) if self._daily_mode(s) else pd.Timestamp(ts)).date().isoformat()
        )
        evaluated = {sym for sym in s.symbols if sym in bars and len(bars[sym]) >= s.warmup_bars}
        signaled = {c.target.symbol for c in got if c.grows}
        if day not in self.rule_days:
            self.rule_days[day] = {}
            for old in sorted(self.rule_days)[:-RULE_DAYS]:
                del self.rule_days[old]
        rec = self.rule_days[day].setdefault(s.name, {"evaluated": set(), "signaled": set()})
        if not (evaluated <= rec["evaluated"] and signaled <= rec["signaled"]):
            rec["evaluated"] |= evaluated
            rec["signaled"] |= signaled
            self.rule_dirty = True

    def _daily_mode(self, s: Strategy) -> bool:
        """실시간 분봉으로 일봉·월간 전략을 돌리는 중인가 (ADR 0025)."""
        return self.daily is not None and s.timeframe in ("1d", "1M")

    def _done_today(self, c: _Candidate, ts: datetime) -> bool:
        """일봉 전략은 거래일마다 심볼별로 진입 한 번, 청산(비중 축소) 한 번만 처리한다.

        백테스트의 일봉 1개 = 실시간의 하루 동안 분봉 수백 개라서, 같은 일봉 규칙이 매분 다시 같은 target을
        낸다. 진입은 처음 돌파한 분에 한 번(판단 모델도 한 번), 청산은 그 거래일에 처음 평가될 때 한 번이다.
        """
        key = (c.strategy.name, c.target.symbol)
        seen = self._entered if c.grows else self._exited
        return seen.get(key) == self.daily.day_of(ts)

    def _mark_done(self, c: _Candidate, ts: datetime) -> None:
        key = (c.strategy.name, c.target.symbol)
        (self._entered if c.grows else self._exited)[key] = self.daily.day_of(ts)
        self.done_dirty = True

    def done_today(self, now: datetime) -> dict[str, Any]:
        """오늘 거래일의 처리 표시 — 우편함에 저장해 재시작 뒤 되살린다 (ADR 0028)."""
        day = self.daily.day_of(now)

        def pick(seen: dict[tuple[str, str], pd.Timestamp]) -> list[list[str]]:
            return sorted([s, sym] for (s, sym), d in seen.items() if d == day)

        return {
            "day": day.date().isoformat(),
            "entered": pick(self._entered),
            "exited": pick(self._exited),
        }

    def restore_state(self, now: datetime, done: Mapping[str, Any] | None = None) -> None:
        """재시작 직후: 손절선과 오늘 처리 표시를 되살린다 (ADR 0028).

        손절선은 브로커가 저장한 포지션의 stop에서 온다. 처리 표시는 우편함 기록(done)을 쓰고, 그에 더해
        오늘 거래일에 연 포지션은 진입·청산 모두 처리된 것으로 본다 — 기록 직전에 죽었을 때를 막는다.
        어제 이전에 연 포지션은 표시하지 않는다(그날 첫 평가에서 시간 청산해야 한다).
        """
        for b in self._books():
            for sym, p in b.executor.positions().items():
                if p.is_open and p.stop is not None:
                    b.stops[sym] = p.stop
        if self.daily is None:
            return
        day = self.daily.day_of(now)
        if done and done.get("day") == day.date().isoformat():
            for s, sym in done.get("entered", []):
                self._entered[(s, sym)] = day
            for s, sym in done.get("exited", []):
                self._exited[(s, sym)] = day
        for b in self._books():
            for sym, p in b.executor.positions().items():
                if p.is_open and p.opened_at is not None and self.daily.day_of(p.opened_at) == day:
                    self._entered[(p.strategy, sym)] = day
                    self._exited[(p.strategy, sym)] = day

    @staticmethod
    def _ready(s: Strategy, bars: Mapping[str, pd.DataFrame]) -> bool:
        """전략 심볼 중 하나라도 준비 기간을 채웠으면 평가한다 (ADR 0026).

        심볼별 준비 여부는 전략이 확인한다. 전부를 기다리면 새로 상장한 심볼 하나 때문에 다른 심볼까지 멈춘다.
        """
        return any(len(bars[sym]) >= s.warmup_bars for sym in s.symbols if sym in bars)

    @staticmethod
    def _marks(ex: OrderExecutor) -> dict[str, float]:
        """보유 심볼의 현재 평가 가격 (봉 반영 전 = 직전 가격)."""
        out: dict[str, float] = {}
        for sym, p in ex.positions().items():
            if p.is_open:
                try:
                    out[sym] = ex.last_price(sym)
                except KeyError:
                    continue
        return out

    @staticmethod
    def _sizing_equity(
        b: _Book,
        c: _Candidate,
        equities: Mapping[int, float],
        prev_marks: Mapping[int, dict],
        entry_equity: dict[int, float],
    ) -> float:
        """사이징 기준 평가액 (ADR 0026).

        지정가(Target.price) 진입은 장중 그 가격에 닿는 순간의 결정이므로, 이 봉의 종가를 쓰면 룩어헤드다.
        그래서 이 봉의 청산을 모두 처리한 직후의 현금 + 남은 보유분 × 직전 가격을 한 번 잡아, 그 봉의
        모든 지정가 진입이 같은 값을 쓴다 (먼저 산 심볼의 오늘 종가가 다음 심볼 크기에 섞이지 않게).
        종가 체결(price=None)과 청산은 종가 시점의 결정이라 틱 시작 평가액(종가 반영)을 그대로 쓴다.
        """
        if not c.grows or c.target.price is None:
            return equities[id(b)]
        if (
            id(b) not in entry_equity
        ):  # 이 봉의 첫 지정가 진입 = 청산은 모두 끝났고 진입은 아직 없다
            ex, prev = b.executor, prev_marks[id(b)]
            entry_equity[id(b)] = ex.cash() + sum(
                p.qty * prev.get(sym, ex.last_price(sym))
                for sym, p in ex.positions().items()
                if p.is_open
            )
        return entry_equity[id(b)]

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

    def _intraday_exposure(self, ex: OrderExecutor) -> float:
        """horizon=intraday 전략들의 현재 포지션 가치 합 (RiskManager 단타 합산 상한용)."""
        intraday = {s.name for s in self.strategies if s.horizon == "intraday"}
        return sum(
            p.qty * ex.last_price(sym)
            for sym, p in ex.positions().items()
            if p.strategy in intraday
        )

    def _books(self) -> list[_Book]:
        return [self._on] if self._off is None else [self._on, self._off]

    def _strategy(self, name: str) -> Strategy:
        for s in self.strategies:
            if s.name == name:
                return s
        raise KeyError(f"등록되지 않은 전략: {name!r}")
