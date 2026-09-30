"""TickRunner가 주입받는 부품의 인터페이스 (04 §7, ADR 0010).

코어는 이 Protocol만 알고 구현은 주입받는다. 1단계 실제 구현은 각 카드 몫이다:
FeatureBuilder(P1-06), JudgmentPipeline(P1-07/08), OrderExecutor(P1-05).
백테스트·페이퍼용 구현은 `engine/replay.py`와 `judgment/stub.py`에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from quantpilot.core.events import JudgmentEvent, SignalEvent
from quantpilot.core.models import Fill, Market, Order, Position, Target


class Clock(Protocol):
    """시장 시계. 시각은 시장 현지 tz-naive (core/clock.py 규칙)."""

    def now(self) -> datetime:
        """현재 현지 시각."""
        ...

    def is_last_session_of_month(self, ts: datetime) -> bool:
        """ts가 그 달의 마지막 세션(봉)인가. 월간 전략 호출 판단용."""
        ...


class FeatureBuilder(Protocol):
    """전략 컨텍스트 → 판단 모델 입력(state). 숫자를 등급·백분위로 짧게 바꾼다."""

    def build(self, *, symbol: str, strategy: str, target: Target, ctx: Any) -> Any:
        """진입 target 하나에 대한 state를 만든다."""
        ...


class JudgmentPipeline(Protocol):
    """판단 모델(+LLM 합의)을 거쳐 사이징 배수를 정한다. 수량·가격은 정하지 않는다 (불변식 #7)."""

    async def evaluate(self, signal: SignalEvent, state: Any) -> JudgmentEvent:
        """size_multiplier 0이면 그 진입 target은 버린다."""
        ...


class RiskVerdict(Protocol):
    """risk.check 결과 (execution.risk.RiskDecision과 같은 모양)."""

    allowed: bool
    qty: float
    reason: str


class RiskGate(Protocol):
    """모든 주문이 브로커 전에 통과하는 관문 (불변식 #9)."""

    halted_reason: str

    def check(
        self,
        order: Order,
        *,
        equity: float,
        price: float,
        positions: dict[str, Position],
        horizon: str = "swing",
        intraday_exposure: float = 0.0,
        now: datetime | None = None,
    ) -> RiskVerdict:
        """주문 허용 여부와 조정된 수량."""
        ...


class OrderExecutor(Protocol):
    """risk.check → broker.submit을 수행하고 계좌 상태를 알려준다."""

    @property
    def is_paper(self) -> bool:
        """가상 체결 브로커인가."""
        ...

    def mark(self, symbol: str, price: float, ts: datetime) -> None:
        """새 시세(봉 종가·체결가)를 알린다. 페이퍼는 평가·체결 기준가로 쓴다."""
        ...

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
        """주문 1건 실행. 체결이면 Fill, 대기·거부면 상태가 갱신된 Order."""
        ...

    def last_price(self, symbol: str) -> float:
        """마지막으로 알린 시세."""
        ...

    def positions(self) -> Mapping[str, Position]:
        """열린 포지션."""
        ...

    def cash(self) -> float:
        """가용 현금."""
        ...

    def equity(self) -> float:
        """현금 + 포지션 평가액."""
        ...


class EngineLink(Protocol):
    """engine ↔ scheduler 사이 하트비트와 시간 청산 명령 (04 §8). 시각은 UTC tz-aware."""

    async def beat(self, market: Market) -> None:
        """엔진이 살아 있음을 기록한다."""
        ...

    async def last_beat(self, market: Market) -> datetime | None:
        """마지막 하트비트 시각. 없으면 None."""
        ...

    async def request_time_exit(self, market: Market, strategy: str) -> str:
        """시간 청산 명령을 넣고 명령 id를 돌려준다."""
        ...

    async def pending_time_exit(self, market: Market, strategy: str) -> str | None:
        """아직 처리되지 않은 시간 청산 명령 id. 없으면 None."""
        ...

    async def ack_time_exit(self, market: Market, strategy: str, cmd_id: str) -> None:
        """명령을 처리했다고 기록한다."""
        ...


class EventBus(Protocol):
    """이벤트 발행 (1단계 Redis pub/sub → api → 화면 WS)."""

    async def publish(self, topic: str, event: object) -> None:
        """topic 예: tick·signal·judgment·order·fill·warning."""
        ...
