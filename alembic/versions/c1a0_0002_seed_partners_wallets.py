"""seed partners and wallets

Revision ID: c1a00002seed
Revises: b4fbd8da6908
Create Date: 2026-04-18 00:00:00

Populates the two business-critical constants: the partner duo (Казах, Арбуз)
and the five working-capital locations. Idempotent via ON CONFLICT so running
the migration twice is safe (e.g. on Railway re-deploys before state is
consistent).
"""

from __future__ import annotations

from typing import Union
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a00002seed"
down_revision: str | None = "b4fbd8da6908"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Telegram user ids confirmed by the user (Казах = owner).
PARTNERS = [
    {"name": "Казах", "tg_user_id": 6885525649},
    {"name": "Арбуз", "tg_user_id": 7220305943},
]

# Wallets match sber26-bot-SPEC.md §"Бизнес-контекст → Оборотный капитал".
WALLETS = [
    {"code": "tapbank",       "name": "TapBank",              "currency": "USDT"},
    {"code": "mercurio",      "name": "Mercurio",             "currency": "USDT"},
    {"code": "rapira",        "name": "Rapira",               "currency": "USDT"},
    {"code": "sber_balances", "name": "Сбер-реквизиты (RUB)", "currency": "RUB"},
    {"code": "cash",          "name": "Наличные",             "currency": "RUB"},
]


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO partners (name, tg_user_id, is_active)
            VALUES (:name, :tg_user_id, TRUE)
            ON CONFLICT (name) DO UPDATE
                SET tg_user_id = EXCLUDED.tg_user_id,
                    is_active  = TRUE
            """
        ),
        PARTNERS,
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO wallets (code, name, currency, is_active)
            VALUES (:code, :name, :currency, TRUE)
            ON CONFLICT (code) DO UPDATE
                SET name      = EXCLUDED.name,
                    currency  = EXCLUDED.currency,
                    is_active = TRUE
            """
        ),
        WALLETS,
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM wallets WHERE code IN "
        "('tapbank','mercurio','rapira','sber_balances','cash')"
    )
    op.execute("DELETE FROM partners WHERE name IN ('Казах','Арбуз')")
