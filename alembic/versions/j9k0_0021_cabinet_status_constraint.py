"""Add cabinet status ↔ worked_out_date consistency CHECK constraint.

Context (2026-04-30): live bug — cabinet «recovered» status was set,
but worked_out_date stayed populated from a prior worked_out state.
Internally contradictory and reports.py logic gets confused. This
constraint enforces:

  worked_out_date IS NOT NULL  ⇔  status IN ('worked_out', 'lost')

Note: 'recovered' explicitly does NOT require worked_out_date because
recovery means the cabinet is back in active use after блок, not that
it was finalized. This was the source of confusion.

Revision ID: j9k00021cabconstr
Revises: i8j90020replyto
Create Date: 2026-04-30
"""

from __future__ import annotations

from alembic import op

revision = "j9k00021cabconstr"
down_revision = "i8j90020replyto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-cleanup: anything 'recovered' or 'in_use' / 'in_stock' /
    # 'blocked' with a worked_out_date set is contradictory state from
    # before this constraint existed. Clear it.
    op.execute(
        """
        UPDATE cabinets
        SET worked_out_date = NULL
        WHERE worked_out_date IS NOT NULL
          AND status NOT IN ('worked_out', 'lost')
        """
    )
    op.create_check_constraint(
        "ck_cabinets_worked_out_date_consistency",
        "cabinets",
        "(worked_out_date IS NOT NULL) = (status IN ('worked_out','lost'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cabinets_worked_out_date_consistency",
        "cabinets",
        type_="check",
    )
