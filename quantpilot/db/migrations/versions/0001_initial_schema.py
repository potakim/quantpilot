"""초기 스키마 (02 문서 §1, ADR 0008)

Revision ID: 0001
Revises:
Create Date: 2026-09-30 00:03:37.121033
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """스키마를 올린다."""
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        op.execute("create extension if not exists timescaledb")

    op.create_table(
        "backtests",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "symbols",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("holdout_cutoff", sa.Date(), nullable=True),
        sa.Column("unlocked_holdout", sa.Boolean(), nullable=False),
        sa.Column(
            "cost_model",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metrics",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("equity_path", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "candles",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("tf", sa.Text(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=8, asdecimal=False), nullable=False),
        sa.Column("source", sa.Text(), server_default="ws", nullable=False),
        sa.PrimaryKeyConstraint("ts", "market", "symbol", "tf"),
    )
    op.create_table(
        "daily_reviews",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "stats",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )
    op.create_table(
        "equity_snapshots",
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("paper", sa.Boolean(), nullable=False),
        sa.Column("cash", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("equity", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.PrimaryKeyConstraint("ts", "market", "paper"),
    )
    op.create_table(
        "news_items",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "symbols",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "risk_flags",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("raw_hash", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_hash"),
    )
    op.create_index("ix_news_items_ts", "news_items", ["ts"], unique=False)
    op.create_table(
        "positions",
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8, asdecimal=False), nullable=False),
        sa.Column("avg_price", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("market", "symbol", "strategy"),
    )
    op.create_table(
        "risk_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("allocation", sa.Float(), nullable=False),
        sa.Column(
            "symbols",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("paper", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "market"),
    )
    op.create_table(
        "signals",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("price_hint", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=True),
        sa.Column("stop", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategy_configs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_ts", "signals", ["ts"], unique=False)
    op.create_table(
        "judgments",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "signal_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "answers",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column(
            "blocks",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("realized_ret_24h", sa.Float(), nullable=True),
        sa.Column("direction_hit", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_judgments_ts", "judgments", ["ts"], unique=False)
    op.create_table(
        "orders",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("broker_order_id", sa.Text(), nullable=True),
        sa.Column("signal_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8, asdecimal=False), nullable=False),
        sa.Column("limit_price", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=True),
        sa.Column("stop", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column(
            "risk_adjustments",
            sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("size_multiplier", sa.Float(), nullable=True),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("paper", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fills",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8, asdecimal=False), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("fee", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("tax", sa.Numeric(precision=20, scale=8, asdecimal=False), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("paper", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fills_ts", "fills", ["ts"], unique=False)
    op.create_table(
        "llm_verdicts",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "judgment_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("approve", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["judgment_id"],
            ["judgments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    if is_pg:
        # TimescaleDB 전용 — PK에 ts가 들어간 테이블만 하이퍼테이블 (ADR 0008)
        op.execute(
            "select create_hypertable('candles', 'ts', chunk_time_interval => interval '7 days')"
        )
        op.execute(
            "alter table candles set (timescaledb.compress, "
            "timescaledb.compress_segmentby = 'market,symbol,tf')"
        )
        op.execute("select add_compression_policy('candles', interval '30 days')")
        op.execute("select create_hypertable('equity_snapshots', 'ts')")
        op.execute("create index ix_news_items_symbols on news_items using gin (symbols)")


def downgrade() -> None:
    """스키마를 내린다."""
    op.drop_table("llm_verdicts")
    op.drop_index("ix_fills_ts", table_name="fills")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_index("ix_judgments_ts", table_name="judgments")
    op.drop_table("judgments")
    op.drop_index("ix_signals_ts", table_name="signals")
    op.drop_table("signals")
    op.drop_table("strategy_configs")
    op.drop_table("settings")
    op.drop_table("risk_events")
    op.drop_table("positions")
    op.drop_index("ix_news_items_ts", table_name="news_items")
    op.drop_table("news_items")
    op.drop_table("equity_snapshots")
    op.drop_table("daily_reviews")
    op.drop_table("candles")
    op.drop_table("backtests")
