"""seed additional trigger keywords: алкаш, ержан

Revision ID: e8e607e380f8
Revises: 8ac1b7763a40
Create Date: 2026-04-19

Adds two more nicknames the bot should react to:
  * "алкаш"  — army-slang позывной (user-requested)
  * "ержан"  — human-name позывной (user-requested)

Idempotent seed — no schema change. Matches the style of
8ac1b7763a40_trigger_keywords_seed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8e607e380f8"
down_revision: str | None = "8ac1b7763a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_KEYWORDS = [
    ("алкаш", "позывной — user-requested"),
    ("ержан", "позывной-имя — user-requested"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for kw, note in NEW_KEYWORDS:
        bind.execute(
            sa.text(
                """
                INSERT INTO trigger_keywords (keyword, notes, is_active)
                VALUES (:k, :n, TRUE)
                ON CONFLICT (keyword) DO UPDATE
                SET is_active = TRUE
                """
            ),
            {"k": kw.lower(), "n": note},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for kw, _ in NEW_KEYWORDS:
        bind.execute(
            sa.text("DELETE FROM trigger_keywords WHERE keyword = :k"),
            {"k": kw.lower()},
        )
