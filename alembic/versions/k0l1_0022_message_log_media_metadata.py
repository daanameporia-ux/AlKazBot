"""Store Telegram media metadata in message_log for deferred parsing.

Revision ID: k0l10022msgmedia
Revises: j9k00021cabconstr
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k0l10022msgmedia"
down_revision = "j9k00021cabconstr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message_log", sa.Column("media_type", sa.Text(), nullable=True))
    op.add_column("message_log", sa.Column("media_file_id", sa.Text(), nullable=True))
    op.add_column(
        "message_log", sa.Column("media_file_unique_id", sa.Text(), nullable=True)
    )
    op.add_column("message_log", sa.Column("media_file_name", sa.Text(), nullable=True))
    op.add_column("message_log", sa.Column("media_mime_type", sa.Text(), nullable=True))
    op.add_column("message_log", sa.Column("media_file_size", sa.Integer(), nullable=True))
    op.create_index(
        "ix_message_log_media_lookup",
        "message_log",
        ["chat_id", "media_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_message_log_media_lookup", table_name="message_log")
    op.drop_column("message_log", "media_file_size")
    op.drop_column("message_log", "media_mime_type")
    op.drop_column("message_log", "media_file_name")
    op.drop_column("message_log", "media_file_unique_id")
    op.drop_column("message_log", "media_file_id")
    op.drop_column("message_log", "media_type")
