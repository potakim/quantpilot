"""게이팅 OFF 섀도 원장: orders·fills.shadow (06 §6.2, ADR 0016)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("orders", "fills")


def upgrade() -> None:
    """orders·fills에 shadow(기본 false)를 더한다. 기존 행은 모두 ON 원장."""
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.add_column(
                sa.Column("shadow", sa.Boolean(), server_default=sa.false(), nullable=False)
            )


def downgrade() -> None:
    """섀도 행(체결 → 주문 순)을 지운 뒤 shadow 칼럼을 없앤다. ON 원장만 남는다."""
    for t in reversed(TABLES):
        op.execute(sa.text(f"delete from {t} where shadow = true"))
        with op.batch_alter_table(t) as b:
            b.drop_column("shadow")
