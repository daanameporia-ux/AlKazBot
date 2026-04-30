"""Add `message_log.reply_to_tg_message_id` to capture Telegram reply chain.

Context (2026-04-30): owner asked the bot «верни его на склад» as a
Telegram REPLY to a specific bot message. Bot lost the reply context
because the middleware never persisted `msg.reply_to_message.message_id`.
Result: bot saw isolated «верни его на склад» with no anchor and asked
«кого именно». Now we save the parent message id so analyzer can
resolve «его / её / этот» references via the reply chain.

Revision ID: i8j90020replyto
Revises: h7i80019poasts
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i8j90020replyto"
down_revision = "h7i80019poasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_log",
        sa.Column("reply_to_tg_message_id", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "ix_message_log_reply_to",
        "message_log",
        ["chat_id", "reply_to_tg_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_message_log_reply_to", table_name="message_log")
    op.drop_column("message_log", "reply_to_tg_message_id")
