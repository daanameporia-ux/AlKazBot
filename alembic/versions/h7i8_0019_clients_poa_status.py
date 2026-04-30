"""Add `clients.poa_status` enum-via-CHECK + backfill.

Context (2026-04-30): owner asked for explicit per-client lifecycle
status visible in /balances. Five values:

    unchecked     — ещё не проверяли в системе
    has_balance   — баланс есть, ещё не сняли (любая причина)
    no_balance    — нашли в системе, но пусто
    not_found     — не находит в системе (ненаход)
    withdrawn     — сняли успешно

Status is derived/maintained by the applier:
  • client_balance with amount>0           → has_balance
  • client_balance with amount=0 + пусто   → no_balance
  • client_balance with description=ненаход → not_found
  • poa_withdrawal confirmed                → withdrawn

Backfilled per owner's 30.04 confirmation (13 clients).

Revision ID: h7i80019poasts
Revises: g6h70018balhist
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h7i80019poasts"
down_revision = "g6h70018balhist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "poa_status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'unchecked'"),
        ),
    )
    op.create_check_constraint(
        "ck_clients_poa_status",
        "clients",
        "poa_status IN ('unchecked','has_balance','no_balance','not_found','withdrawn')",
    )

    # Backfill — yesterday's 11 (corrected per owner 2026-04-30)
    # plus today's 2 (Зинкевич, Кондратенко Елена) staying as unchecked.
    op.execute(
        """
        UPDATE clients SET poa_status='withdrawn'
        WHERE name IN ('Мицкевич Сергей','Баскова','Мансуров')
        """
    )
    op.execute(
        """
        UPDATE clients SET poa_status='has_balance'
        WHERE name IN ('Аймурат','Епанчинцева')
        """
    )
    op.execute(
        """
        UPDATE clients SET poa_status='no_balance'
        WHERE name IN ('Байкалов Сергей','Борисевич','Денькевич','Войтик Артём')
        """
    )
    op.execute(
        """
        UPDATE clients SET poa_status='not_found'
        WHERE name IN ('Король','Вакальчук')
        """
    )
    # 'Зинкевич' and 'Кондратенко Елена' remain at default 'unchecked'.


def downgrade() -> None:
    op.drop_constraint("ck_clients_poa_status", "clients", type_="check")
    op.drop_column("clients", "poa_status")
