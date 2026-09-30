"""엔진 내부 이벤트 (04 §1). 시각은 전부 시장 현지 tz-naive (core/clock.py 규칙)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quantpilot.core.models import Fill, Gate, JudgeResult, Market, Order, Side, Target


@dataclass(frozen=True)
class TradeEvent:
    """거래소 체결 1건 (웹소켓 trade). 손절 검사와 봉 집계의 입력."""

    market: Market
    symbol: str
    ts: datetime
    price: float
    qty: float
    side: Side | None = None  # 매수·매도 주도 (거래소가 주면)


@dataclass(frozen=True)
class BarClosed:
    """봉 하나가 마감됨. ts는 봉 시작 시각."""

    market: Market
    symbol: str
    timeframe: str  # 예: "1m", "1d"
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SignalEvent:
    """전략이 낸 후보 Target. kind: entry·exit·rebalance."""

    market: Market
    strategy: str
    target: Target
    kind: str
    ts: datetime
    signal_id: int | None = None


@dataclass(frozen=True)
class JudgmentEvent:
    """신호에 대한 판단 결과. 수량·가격은 담지 않는다 (불변식 #7)."""

    signal_id: int
    result: JudgeResult
    gate: Gate
    size_multiplier: float
    ts: datetime
    blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrderEvent:
    """주문 상태 변화 (접수·거부·취소)."""

    order: Order
    ts: datetime


@dataclass(frozen=True)
class FillEvent:
    """체결 확정."""

    fill: Fill


@dataclass(frozen=True)
class RiskEvent:
    """리스크 규칙 발동 (서킷브레이커·할트·정합 불일치 등, 02 risk_events)."""

    kind: str
    ts: datetime
    market: Market | None = None
    detail: dict[str, Any] = field(default_factory=dict)
