"""Add `on_hold` value to clients.poa_status — для проблемных доверок.

Context (2026-05-02): owner попросил отдельную «стопку проблемных» —
клиентов у которых баланс есть (или мог бы быть), но снять прямо сейчас
нельзя по объективной причине: проблема с паспортом, блок, ждём
обращения и т.д. Они не «активные на снятие сегодня», но и не
закрытые — снимем когда условия позволят.

Use cases:
  - паспорт-проблема (Рудак, Михнюк — баланс есть, паспорт устарел)
  - не дают посмотреть баланс (Кравцов — паспорт-проблема, банк блок)
  - ждём «настоящую» доверку / обращение / документы
  - временный технический блок на счёте

Status semantics:
  - has_balance остаётся за «снимать сегодня/завтра, баланс есть»
  - on_hold = «отложено по причине, снимем после»

Revision ID: l1m20023onhold
Revises: k0l10022msgmedia
Create Date: 2026-05-02
"""

from __future__ import annotations

from alembic import op

revision = "l1m20023onhold"
down_revision = "k0l10022msgmedia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_clients_poa_status", "clients", type_="check")
    op.create_check_constraint(
        "ck_clients_poa_status",
        "clients",
        "poa_status IN ('unchecked','has_balance','no_balance','not_found','withdrawn','on_hold')",
    )


def downgrade() -> None:
    # Move any on_hold rows back to has_balance before tightening constraint.
    op.execute(
        "UPDATE clients SET poa_status='has_balance' WHERE poa_status='on_hold'"
    )
    op.drop_constraint("ck_clients_poa_status", "clients", type_="check")
    op.create_check_constraint(
        "ck_clients_poa_status",
        "clients",
        "poa_status IN ('unchecked','has_balance','no_balance','not_found','withdrawn')",
    )
