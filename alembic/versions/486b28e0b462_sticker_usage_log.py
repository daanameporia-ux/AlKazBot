"""sticker_usage table — log every sticker send with chat context

Revision ID: 486b28e0b462
Revises: 1033b73e2805
Create Date: 2026-04-19

Adds `sticker_usage` so the bot can learn WHEN certain stickers get
sent — both from human users in the main group (for mimicking team
taste) and from the bot itself (for dedup / not-spamming-same-pack).

Each row stores:
  * sticker_file_unique_id, sticker_set, emoji — what was sent
  * tg_user_id, chat_id, tg_message_id — where
  * preceding_text — last ~3 messages before the sticker (truncated),
    so Claude can see "when humans say X, they usually drop sticker Y"
  * sent_by_bot — distinguish learning examples from the bot's own
    replies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "486b28e0b462"
down_revision: str | None = "1033b73e2805"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sticker_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sticker_file_unique_id", sa.Text(), nullable=True),
        sa.Column("sticker_set", sa.Text(), nullable=True),
        sa.Column("emoji", sa.Text(), nullable=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("preceding_text", sa.Text(), nullable=True),
        sa.Column(
            "sent_by_bot",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_sticker_usage_sticker_file_unique_id",
        "sticker_usage",
        ["sticker_file_unique_id"],
    )
    op.create_index(
        "ix_sticker_usage_sticker_set", "sticker_usage", ["sticker_set"]
    )
    op.create_index("ix_sticker_usage_emoji", "sticker_usage", ["emoji"])
    op.create_index(
        "ix_sticker_usage_tg_user_id", "sticker_usage", ["tg_user_id"]
    )
    op.create_index("ix_sticker_usage_chat_id", "sticker_usage", ["chat_id"])
    op.create_index(
        "ix_sticker_usage_created_at", "sticker_usage", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sticker_usage_created_at", table_name="sticker_usage")
    op.drop_index("ix_sticker_usage_chat_id", table_name="sticker_usage")
    op.drop_index("ix_sticker_usage_tg_user_id", table_name="sticker_usage")
    op.drop_index("ix_sticker_usage_emoji", table_name="sticker_usage")
    op.drop_index("ix_sticker_usage_sticker_set", table_name="sticker_usage")
    op.drop_index(
        "ix_sticker_usage_sticker_file_unique_id", table_name="sticker_usage"
    )
    op.drop_table("sticker_usage")
