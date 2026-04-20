"""Seed the wake-words that were already agreed on in chat but never made
it into trigger_keywords:

- пёс / пес  — owner asked the bot to respond to 'пёс' (saved as KB
  preference on 2026-04-19 but the keyword matcher only reads this
  table, so the reaction silently dropped until 2026-04-20).
- шавка     — heard in prior voice notes ("Шавка ты, блять...").
- поганый   — part of "пёс поганый", common phrasing to the bot.

Idempotent via `ON CONFLICT (keyword) DO UPDATE is_active=TRUE`.

Revision ID: a1b20013wakewords
Revises: f3a10012kbclean
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op

revision = "a1b20013wakewords"
down_revision = "f3a10012kbclean"
branch_labels = None
depends_on = None


_NEW_KEYWORDS = [
    ("пёс", "owner asked to respond to 'пёс' — was KB-preference only"),
    ("пес", "variant of пёс without ё — Whisper sometimes misses it"),
    ("шавка", "team nickname (heard in prior voice notes)"),
    ("поганый", "part of 'пёс поганый' — common phrasing to the bot"),
]


def upgrade() -> None:
    for kw, notes in _NEW_KEYWORDS:
        op.execute(
            f"""
            INSERT INTO trigger_keywords (keyword, is_active, notes)
            VALUES ('{kw}', TRUE, '{notes.replace("'", "''")}')
            ON CONFLICT (keyword) DO UPDATE
              SET is_active = TRUE,
                  notes = EXCLUDED.notes
            """
        )


def downgrade() -> None:
    for kw, _ in _NEW_KEYWORDS:
        op.execute(f"UPDATE trigger_keywords SET is_active=FALSE WHERE keyword = '{kw}'")
