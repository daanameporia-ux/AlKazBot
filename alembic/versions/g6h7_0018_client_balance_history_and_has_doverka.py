"""Add `client_balance_history` table + `cabinets.has_doverka` field.

Context (2026-04-29):
- Bot kept misclassifying balance reports («Аймурат 62к карта») as
  poa_withdrawal, applier blew up on missing client_share_pct,
  user got error popups in Telegram and lost data.
- New intent `client_balance` records the snapshot to a dedicated
  history table so we can answer «какой баланс у Мицкевич?» later.
- `cabinets.has_doverka` finally lets /report distinguish full-priced
  cabinets from no-POA stock that should be valued by the prepayment
  remainder average.

Revision ID: g6h70018balhist
Revises: e5f60017vocabkb
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g6h70018balhist"
down_revision = "e5f60017vocabkb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_balance_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "client_id",
            sa.Integer,
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "source",
            sa.Text,
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('card','sber_account','unknown','cash','other')",
            name="ck_client_balance_history_source",
        ),
    )
    op.create_index(
        "ix_client_balance_history_client_created",
        "client_balance_history",
        ["client_id", "created_at"],
    )

    op.add_column(
        "cabinets",
        sa.Column(
            "has_doverka",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Backfill: existing 14 cabinets in Karen's pack — set has_doverka per
    # what we know on 2026-04-29.  Kazakh confirmed the breakdown:
    #   • с доверкой (готовые / отработанные / в работе):
    #       karen-01 Кокоскерия (без доверки, выебан)  → false
    #       karen-02 Алан (28к + 5к перевыпуск)        → true
    #       karen-03 Байрамов (без доверки)            → false
    #       karen-04 Даут                              → true
    #       karen-06 Хеция                             → true
    #       karen-07 Салакая Мизан                     → true
    #       karen-08 Меласанов                         → true
    #       karen-09 Джопуа                            → true
    #       karen-10 Анатолий                          → true
    #       karen-12 Куджба (доверка довезена 28.04)   → true
    #       karen-13 Габлая Лоида                      → true
    #   • без доверки (на складе, ждём от Карена):
    #       karen-05 Габлая Ривекка                    → false
    #       karen-11 Какоба                            → false
    #       karen-14 Бабка                             → false
    op.execute(
        """
        UPDATE cabinets SET has_doverka = TRUE
        WHERE auto_code IN (
            'karen-02-bigvava-alan',
            'karen-04-daut',
            'karen-06-khetsia',
            'karen-07-salakaya-mizan',
            'karen-08-melasanov',
            'karen-09-dzhopua',
            'karen-10-sagutinov',
            'karen-12-kudzhba',
            'karen-13-ghablaya-loida'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("cabinets", "has_doverka")
    op.drop_index(
        "ix_client_balance_history_client_created",
        table_name="client_balance_history",
    )
    op.drop_table("client_balance_history")
