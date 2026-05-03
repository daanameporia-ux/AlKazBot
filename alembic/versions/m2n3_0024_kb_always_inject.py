"""Add `knowledge_base.always_inject` flag for tiered KB injection.

Context (2026-05-03): system prompt раздут до ~25k cached tokens (110+
KB фактов всегда грузятся). Большинство фактов — справочные «X = Y»
заметки, которые нужны только когда в чате упоминается X. Постоянно
кэшировать их = напрасный cache-write на каждый cold start.

Tiered loading:
  • always_inject = TRUE  → грузится в cached system block каждый запрос.
    Это правила/каноны/тон/математика — всё что бот ДОЛЖЕН знать всегда.
  • always_inject = FALSE → НЕ грузится в кэш. Подтягивается lazy через
    `kb_repo.search()` в uncached хвост, когда триггер из батча
    совпадает с key/content. Так справочные «помни про Карена»
    появляются только когда обсуждают Карена.

Backfill: помечаем essential rules как always_inject=true. Категория
`alias` тоже always_inject (важно для распознавания псевдонимов в чате).

Revision ID: m2n30024kbflag
Revises: l1m20023onhold
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m2n30024kbflag"
down_revision = "l1m20023onhold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base",
        sa.Column(
            "always_inject",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_knowledge_base_always_inject",
        "knowledge_base",
        ["always_inject"],
        postgresql_where=sa.text("always_inject = true"),
    )

    # Backfill: aliases всегда инжектим (псевдонимы — критично для парсинга).
    op.execute(
        "UPDATE knowledge_base SET always_inject = true WHERE category = 'alias'"
    )

    # Backfill: ключевые правила тон+корректность.
    op.execute(
        """
        UPDATE knowledge_base SET always_inject = true
        WHERE key IN (
            'math-honesty',
            'strict-scope',
            'shares-disabled-temp'
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_base_always_inject", table_name="knowledge_base")
    op.drop_column("knowledge_base", "always_inject")
