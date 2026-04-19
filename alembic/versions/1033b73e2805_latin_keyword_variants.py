"""seed Latin-transliteration variants for keyword matching

Revision ID: 1033b73e2805
Revises: e8e607e380f8
Create Date: 2026-04-19

Whisper sometimes transcribes Russian nicknames in Latin letters
("Алкаш" → "Alkaz", "Ержан" → "Erzhan") even with a Cyrillic
initial_prompt. These Latin variants are seeded so substring matching
catches them regardless of transcription drift.

Kept out of the Whisper prompt itself (see `voice_transcribe._is_cyrillic_word`)
to avoid reinforcing the Latin bias.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1033b73e2805"
down_revision: str | None = "e8e607e380f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LATIN_VARIANTS = [
    ("alkaz", "Latin variant of алказ / алкаш — Whisper safety net"),
    ("alkash", "Latin variant of алкаш — Whisper safety net"),
    ("erzhan", "Latin variant of ержан — Whisper safety net"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for kw, note in LATIN_VARIANTS:
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
    for kw, _ in LATIN_VARIANTS:
        bind.execute(
            sa.text("DELETE FROM trigger_keywords WHERE keyword = :k"),
            {"k": kw.lower()},
        )
