"""공통 데이터 모델. 외부 의존성 없이 dataclass만 사용한다 (전략·백테스터·브로커가 공유)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Market(str, Enum):
    UPBIT = "upbit"  # 코인 KRW 마켓
    KRX = "krx"  # 국내주식·ETF
    US = "us"  # 미국주식·ETF


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Target:
    """전략이 한 봉에서 원하는 목표 비중.

    weight: 배정 자본 대비 목표 비중 (0.0 ~ 1.0). 0이면 청산.
    price:  None이면 봉 종가 체결. 값이 있으면 그 가격에 체결(봉의 고가·저가 안에 있어야 함).
            변동성 돌파처럼 장중 특정 가격 도달 시 진입하는 전략이 쓴다.
    reason: 사후 리뷰·AI 판단 입력용 짧은 설명.
    stop:   손절가(있으면). RiskManager가 1% 룰 수량 계산에 쓴다.
    """

    symbol: str
    weight: float
    price: float | None = None
    reason: str = ""
    stop: float | None = None


@dataclass
class Order:
    symbol: str
    side: Side
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    strategy: str = ""
    reason: str = ""
    stop: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: OrderStatus = OrderStatus.PENDING
    reject_reason: str = ""


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    tax: float
    ts: datetime
    strategy: str = ""
    reason: str = ""

    @property
    def gross(self) -> float:
        return self.qty * self.price

    @property
    def cost(self) -> float:
        return self.fee + self.tax


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0
    opened_at: datetime | None = None
    strategy: str = ""

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > 1e-12

    def market_value(self, price: float) -> float:
        return self.qty * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_price) * self.qty


@dataclass
class JudgeResult:
    """판단 모델(Jev/Laya) 한 번 호출의 결과. 확률과 확신도만 준다 — 수량·가격은 절대 주지 않는다."""

    answers: dict  # 예: {"regime": {"trend_up": 0.79, ...}, "news_risk": 0.12, ...}
    confidence: float  # 0~1
    latency_ms: float = 0.0
    model: str = ""
    cost_usd: float = 0.0
    raw: dict | None = None


class Gate(str, Enum):
    HOLD = "hold"  # 진입 안 함
    HALF = "half"  # 절반 사이징
    FULL = "full"  # 전량
