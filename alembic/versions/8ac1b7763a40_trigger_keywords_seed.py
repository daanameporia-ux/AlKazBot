"""trigger_keywords table + seed initial keywords

Revision ID: 8ac1b7763a40
Revises: e2a00006idx
Create Date: 2026-04-19

Adds the `trigger_keywords` table used by the local keyword matcher
(src/core/keyword_match.py). The matcher scans every incoming text
message and voice transcript; if any active keyword appears as a
substring (case-insensitive), it fires the batch analyzer — otherwise
messages stay silent and nothing hits the Anthropic API.

Seeds a starter set so the matcher has something to find on day one.
The drop_index calls that autogenerate inserted have been REMOVED:
those indexes are still wanted; alembic got confused by partial
indexes created via raw SQL in the previous migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ac1b7763a40"
down_revision: str | None = "e2a00006idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Seed keywords — lowercase, substring match. Keep 3+ chars to avoid
# catching every Russian word.
SEED_KEYWORDS = [
    # User-supplied nicknames
    ("бот", "generic nickname"),
    ("цифровой пидорас", "user-supplied"),
    ("раб по подписке", "user-supplied"),
    ("бухгалтер", "role reference"),
    # Bot identity variants (without @)
    ("al_kazbot", "bot username ascii"),
    ("алказбот", "bot username cyrillic"),
    ("алказ", "short nickname"),
    ("казахский арбуз", "bot display name"),
]


def upgrade() -> None:
    op.create_table(
        "trigger_keywords",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("keyword"),
    )

    # Seed (idempotent via ON CONFLICT)
    bind = op.get_bind()
    for kw, note in SEED_KEYWORDS:
        bind.execute(
            sa.text(
                """
                INSERT INTO trigger_keywords (keyword, notes, is_active)
                VALUES (:k, :n, TRUE)
                ON CONFLICT (keyword) DO NOTHING
                """
            ),
            {"k": kw.lower(), "n": note},
        )


def downgrade() -> None:
    op.drop_table("trigger_keywords")
