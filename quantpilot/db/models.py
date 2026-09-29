"""SQLAlchemy ORM 매핑 (02 문서 §1). PostgreSQL 전용 타입은 방언별 변형으로 SQLite에서도 돈다 (ADR 0008)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# jsonb → PostgreSQL JSONB / 그 외 JSON
JsonB = JSON().with_variant(JSONB(), "postgresql")
# text[] → PostgreSQL ARRAY(text) / 그 외 JSON 배열
TextArray = JSON().with_variant(ARRAY(Text()), "postgresql")
# bigserial: SQLite는 INTEGER PRIMARY KEY여야 자동 증가한다
BigId = BigInteger().with_variant(Integer(), "sqlite")
TsTz = DateTime(timezone=True)
Money = Numeric(20, 8, asdecimal=False)
Qty = Numeric(24, 8, asdecimal=False)


class Base(DeclarativeBase):
    """모든 ORM 모델의 기반."""


# ── 1.1 시세 ────────────────────────────────────────────


class CandleRow(Base):
    __tablename__ = "candles"

    ts: Mapped[datetime] = mapped_column(TsTz, primary_key=True)
    market: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    tf: Mapped[str] = mapped_column(Text, primary_key=True)
    open: Mapped[float] = mapped_column(Money)
    high: Mapped[float] = mapped_column(Money)
    low: Mapped[float] = mapped_column(Money)
    close: Mapped[float] = mapped_column(Money)
    volume: Mapped[float] = mapped_column(Qty)
    source: Mapped[str] = mapped_column(Text, server_default="ws")


class NewsItemRow(Base):
    __tablename__ = "news_items"
    __table_args__ = (Index("ix_news_items_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TsTz)
    source: Mapped[str] = mapped_column(Text)
    symbols: Mapped[list[str]] = mapped_column(TextArray, default=list)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    risk_flags: Mapped[list[str]] = mapped_column(TextArray, default=list)
    risk_score: Mapped[float | None] = mapped_column(Float)
    raw_hash: Mapped[str | None] = mapped_column(Text, unique=True)


# ── 1.2 전략·설정 ───────────────────────────────────────


class StrategyConfigRow(Base):
    __tablename__ = "strategy_configs"
    __table_args__ = (UniqueConstraint("name", "market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    allocation: Mapped[float] = mapped_column(Float)
    symbols: Mapped[list[str]] = mapped_column(TextArray)
    paper: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        TsTz, server_default=func.now(), onupdate=func.now()
    )


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JsonB)
    updated_at: Mapped[datetime] = mapped_column(
        TsTz, server_default=func.now(), onupdate=func.now()
    )


# ── 1.3 신호·판단 ───────────────────────────────────────


class SignalRow(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TsTz)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategy_configs.id"))
    market: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float)
    price_hint: Mapped[float | None] = mapped_column(Money)
    stop: Mapped[float | None] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    outcome_reason: Mapped[str | None] = mapped_column(Text)


class JudgmentRow(Base):
    __tablename__ = "judgments"
    __table_args__ = (Index("ix_judgments_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    ts: Mapped[datetime] = mapped_column(TsTz)
    provider: Mapped[str] = mapped_column(Text)
    state: Mapped[dict[str, Any]] = mapped_column(JsonB)
    answers: Mapped[dict[str, Any]] = mapped_column(JsonB)
    confidence: Mapped[float] = mapped_column(Float)
    gate: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list[str]] = mapped_column(TextArray, default=list)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    realized_ret_24h: Mapped[float | None] = mapped_column(Float)
    direction_hit: Mapped[bool | None] = mapped_column(Boolean)


class LlmVerdictRow(Base):
    __tablename__ = "llm_verdicts"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    judgment_id: Mapped[int] = mapped_column(ForeignKey("judgments.id"))
    model: Mapped[str] = mapped_column(Text)
    approve: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    prompt_hash: Mapped[str | None] = mapped_column(Text)


# ── 1.4 주문·체결·포지션 ────────────────────────────────


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    broker_order_id: Mapped[str | None] = mapped_column(Text)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    ts: Mapped[datetime] = mapped_column(TsTz)
    market: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Qty)
    limit_price: Mapped[float | None] = mapped_column(Money)
    stop: Mapped[float | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    risk_adjustments: Mapped[list[str]] = mapped_column(TextArray, default=list)
    size_multiplier: Mapped[float | None] = mapped_column(Float)
    strategy: Mapped[str] = mapped_column(Text)
    paper: Mapped[bool] = mapped_column(Boolean)


class FillRow(Base):
    __tablename__ = "fills"
    __table_args__ = (Index("ix_fills_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    ts: Mapped[datetime] = mapped_column(TsTz)
    market: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Qty)
    price: Mapped[float] = mapped_column(Money)
    fee: Mapped[float] = mapped_column(Money, default=0)
    tax: Mapped[float] = mapped_column(Money, default=0)
    strategy: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    paper: Mapped[bool] = mapped_column(Boolean)


class PositionRow(Base):
    __tablename__ = "positions"

    market: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    strategy: Mapped[str] = mapped_column(Text, primary_key=True)
    qty: Mapped[float] = mapped_column(Qty)
    avg_price: Mapped[float] = mapped_column(Money)
    opened_at: Mapped[datetime | None] = mapped_column(TsTz)
    stop: Mapped[float | None] = mapped_column(Money)
    updated_at: Mapped[datetime] = mapped_column(
        TsTz, server_default=func.now(), onupdate=func.now()
    )


class EquitySnapshotRow(Base):
    __tablename__ = "equity_snapshots"

    ts: Mapped[datetime] = mapped_column(TsTz, primary_key=True)
    market: Mapped[str] = mapped_column(Text, primary_key=True)
    paper: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    cash: Mapped[float] = mapped_column(Money)
    equity: Mapped[float] = mapped_column(Money)


# ── 1.5 백테스트·운영 ───────────────────────────────────


class BacktestRow(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TsTz, server_default=func.now())
    strategy: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JsonB)
    symbols: Mapped[list[str]] = mapped_column(TextArray)
    source: Mapped[str] = mapped_column(Text)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    holdout_cutoff: Mapped[date | None] = mapped_column(Date)
    unlocked_holdout: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_model: Mapped[dict[str, Any]] = mapped_column(JsonB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonB)
    attempt_no: Mapped[int] = mapped_column(Integer)
    equity_path: Mapped[str | None] = mapped_column(Text)


class RiskEventRow(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TsTz, server_default=func.now())
    kind: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonB)
    resolved_at: Mapped[datetime | None] = mapped_column(TsTz)


class DailyReviewRow(Base):
    __tablename__ = "daily_reviews"

    # 속성명이 date면 타입 어노테이션의 date를 가리므로 day로 두고 컬럼명만 date
    day: Mapped[date] = mapped_column("date", Date, primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonB)
    cost_usd: Mapped[float | None] = mapped_column(Float)
