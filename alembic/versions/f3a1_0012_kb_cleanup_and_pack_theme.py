"""KB cleanup + seen_stickers.pack_theme.

- Delete two stale "для будущего" rule rows (KB #1 and #2).
- De-duplicate the "рапа" alias (keep the earlier id, drop the later).
- Purge pending_ops rows that are status='expired' or 'cancelled' older
  than 7 days (stale clutter from early-April testing).
- Add `seen_stickers.pack_theme` for future thematic picks (e.g. whole
  pack labelled "сбер-мем", "ругань", "устал"). NULL-safe.

Revision ID: f3a10012kbclean
Revises: 7ad47bc5c495
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a10012kbclean"
down_revision = "7ad47bc5c495"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- KB: kill the two "для будущего" junk rules (content length < 20)
    op.execute(
        """
        UPDATE knowledge_base
        SET is_active = FALSE,
            notes = COALESCE(notes, '') || ' [auto-cleaned: stale placeholder]'
        WHERE category = 'rule'
          AND (
              TRIM(content) IN ('для будущего', 'для будущего.')
              OR LENGTH(TRIM(content)) < 12
          )
          AND is_active = TRUE
        """
    )

    # ---- KB: dedup "рапа" alias (keep MIN id, deactivate the rest)
    op.execute(
        """
        WITH keeper AS (
            SELECT MIN(id) AS id
            FROM knowledge_base
            WHERE category = 'alias'
              AND LOWER(TRIM(key)) = 'рапа'
              AND is_active = TRUE
        )
        UPDATE knowledge_base kb
        SET is_active = FALSE,
            notes = COALESCE(kb.notes, '') || ' [auto-cleaned: duplicate of рапа]'
        FROM keeper
        WHERE kb.category = 'alias'
          AND LOWER(TRIM(kb.key)) = 'рапа'
          AND kb.is_active = TRUE
          AND kb.id <> keeper.id
        """
    )

    # ---- pending_ops: purge old non-pending rows (clutter from testing)
    op.execute(
        """
        DELETE FROM pending_ops
        WHERE status IN ('expired', 'cancelled')
          AND created_at < NOW() - INTERVAL '7 days'
        """
    )

    # ---- seen_stickers.pack_theme column
    op.add_column(
        "seen_stickers",
        sa.Column("pack_theme", sa.Text(), nullable=True),
    )
    # Index on (pack_theme) for quick lookup — NULL values are sparse by default.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_seen_stickers_pack_theme
        ON seen_stickers (pack_theme)
        WHERE pack_theme IS NOT NULL
        """
    )

    # Seed: "kontorapidarasov" pack = сбер-мемы (owner explicitly said so).
    op.execute(
        """
        UPDATE seen_stickers
        SET pack_theme = 'сбер-мем'
        WHERE sticker_set ILIKE '%kontorapidarasov%'
          AND pack_theme IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_seen_stickers_pack_theme")
    op.drop_column("seen_stickers", "pack_theme")

    # KB + pending_ops cleanup is intentionally not reversed — rows are
    # soft-deleted (is_active=False), the cancelled pending_ops are truly
    # removed but they were expired anyway. Recreating them would be
    # meaningless.
