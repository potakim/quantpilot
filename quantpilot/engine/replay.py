"""TickRunner를 백테스트·페이퍼로 돌리기 위한 부품 (ADR 0010).

- ReplayClock: 재생 중인 봉 시각을 '현재'로 알려주는 가짜 시계
- DirectExecutor: risk.check → broker.submit (재시도·레이트리밋·원장은 P1-05 OrderExecutor 몫)
- UnrestrictedRisk: 계좌 규칙을 적용하지 않는 게이트. 페이퍼 브로커와만 조합 가능 (TickRunner가 검사)
- StubFeatureBuilder: 피처 없는 최소 state (실제 FeatureBuilder는 P1-06)
- CollectingBus: 발행된 이벤트를 메모리에 모은다
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from quantpilot.core.models import Fill, Order, OrderStatus, Position, Target
from quantpilot.core.ports import RiskGate
from quantpilot.execution.broker import BrokerAdapter
from quantpilot.execution.risk import RiskDecision
from quantpilot.judgment.base import State


class ReplayClock:
    """재생 시계. month_last는 데이터 타임라인의 월별 마지막 봉 시각."""

    def __init__(self, month_last: Iterable[datetime] = ()):
        self._month_last = {pd.Timestamp(t) for t in month_last}
        self._now: datetime | None = None

    def set(self, ts: datetime) -> None:
        """재생 시각을 옮긴다."""
        self._now = ts

    def now(self) -> datetime:
        """현재 재생 시각."""
        if self._now is None:
            raise RuntimeError("ReplayClock.set()이 먼저 호출돼야 한다")
        return self._now

    def is_last_session_of_month(self, ts: datetime) -> bool:
        """ts가 타임라인에서 그 달의 마지막 봉인가."""
        return pd.Timestamp(ts) in self._month_last


class UnrestrictedRisk:
    """계좌 규칙(RiskRules)을 적용하지 않는 게이트. 0단계와 같은 '전략 자체 성과' 백테스트용.

    RiskRules를 바꾸지 않는다(불변식 #6과 무관). 실브로커와 묶으면 TickRunner가 거부한다.
    """

    unrestricted = True
    halted_reason = ""

    def check(self, order: Order, **_: Any) -> RiskDecision:
        """항상 요청 수량 그대로 허용."""
        return RiskDecision(True, order.qty, "unrestricted")


class DirectExecutor:
    """risk.check → broker.submit. 모든 주문이 이 순서로만 브로커에 닿는다 (불변식 #9)."""

    def __init__(self, broker: BrokerAdapter, risk: RiskGate):
        self.broker = broker
        self.risk = risk

    @property
    def is_paper(self) -> bool:
        """가상 체결 브로커인가."""
        return self.broker.is_paper

    def mark(self, symbol: str, price: float, ts: datetime) -> None:
        """페이퍼 브로커에 새 시세를 알린다. 실브로커는 거래소 시세를 쓰므로 무시."""
        on_price = getattr(self.broker, "on_price", None)
        if on_price is not None:
            on_price(symbol, price, ts)

    async def execute(
        self,
        order: Order,
        *,
        equity: float,
        price: float,
        positions: dict[str, Position],
        horizon: str,
        intraday_exposure: float,
    ) -> Fill | Order:
        """리스크 게이트 통과분만 제출한다. price는 체결 기준가(페이퍼는 이 가격에 슬리피지 적용)."""
        d = self.risk.check(
            order,
            equity=equity,
            price=price,
            positions=positions,
            horizon=horizon,
            intraday_exposure=intraday_exposure,
            now=order.ts or datetime.now(UTC),
        )
        if not d.allowed:
            order.status, order.reject_reason = OrderStatus.REJECTED, f"risk:{d.reason}"
            return order
        order.qty = d.qty
        order.risk_adjustments = list(getattr(d, "adjustments", []))
        on_price = getattr(self.broker, "on_price", None)
        if on_price is None:
            return self.broker.submit(order)
        # 페이퍼: 체결 기준가(Target.price 등)로 잠시 호가를 맞추고, 제출 뒤 원래 시세로 되돌린다
        prev = self.broker.last_price(order.symbol)
        on_price(order.symbol, price, order.ts)
        try:
            return self.broker.submit(order)
        finally:
            on_price(order.symbol, prev, order.ts)

    def last_price(self, symbol: str) -> float:
        """마지막 시세."""
        return self.broker.last_price(symbol)

    def positions(self) -> Mapping[str, Position]:
        """열린 포지션."""
        return self.broker.positions()

    def cash(self) -> float:
        """가용 현금."""
        return self.broker.cash()

    def equity(self) -> float:
        """현금 + 평가액."""
        return self.broker.equity()


class StubFeatureBuilder:
    """피처 없는 최소 state. 실제 등급·백분위 피처는 P1-06 FeatureBuilder."""

    def __init__(self, market: str):
        self.market = market

    def build(self, *, symbol: str, strategy: str, target: Target, ctx: Any) -> State:
        """전략 이름과 target 사유만 담는다."""
        return State(self.market, symbol, strategy, signal=target.reason)


class CollectingBus:
    """발행된 (topic, event)를 순서대로 모은다. topics를 주면 그 topic만."""

    def __init__(self, topics: Iterable[str] | None = None):
        self.topics = set(topics) if topics is not None else None
        self.events: list[tuple[str, object]] = []

    async def publish(self, topic: str, event: object) -> None:
        """이벤트 1건 기록."""
        if self.topics is None or topic in self.topics:
            self.events.append((topic, event))
