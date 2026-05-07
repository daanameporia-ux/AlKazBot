"""Add `clients.owner_name` — заказчик/владелец POA-доверки.

Context (2026-05-06): команда работает с доверенностями от разных
поставщиков-заказчиков (например «Лол» приносит свою партию). Чтобы
агрегировать снятия по заказчику и понимать кому что вернуть/отдать,
вводим текстовое поле owner_name. Nullable — старые клиенты могут
не иметь явного владельца.

Revision ID: o4p50026clntown
Revises: n3o40025kbcat
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o4p50026clntown"
down_revision = "o4p50026search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("owner_name", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_clients_owner_name",
        "clients",
        ["owner_name"],
        postgresql_where=sa.text("owner_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_clients_owner_name", table_name="clients")
    op.drop_column("clients", "owner_name")
