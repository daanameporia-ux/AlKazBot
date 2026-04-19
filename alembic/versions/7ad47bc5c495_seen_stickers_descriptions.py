"""seen_stickers: add description columns (Vision-generated)

Revision ID: 7ad47bc5c495
Revises: 486b28e0b462
Create Date: 2026-04-19

Lets the bot store a short Russian description of each static sticker
(e.g. «офис с красной неоновой вывеской КОНТОРА ПИДАРАСОВ»), produced
by Claude Haiku Vision when the sticker is first captured. Claude in
the analyzer prompt sees this alongside emoji to pick the most
on-point sticker for the moment instead of reacting to a bare emoji
label.

Columns:
  * description        — free-text description (NULL until described)
  * description_model  — which model produced it (for audit / rerun)
  * described_at       — when it was described

Idempotent via `IF NOT EXISTS` on column add (Postgres ≥9.6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7ad47bc5c495"
down_revision: str | None = "486b28e0b462"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "seen_stickers",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "seen_stickers",
        sa.Column("description_model", sa.Text(), nullable=True),
    )
    op.add_column(
        "seen_stickers",
        sa.Column(
            "described_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    # Partial index to quickly find un-described stickers (for backfill).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_seen_stickers_undescribed "
        "ON seen_stickers (id) WHERE description IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_seen_stickers_undescribed")
    op.drop_column("seen_stickers", "described_at")
    op.drop_column("seen_stickers", "description_model")
    op.drop_column("seen_stickers", "description")
