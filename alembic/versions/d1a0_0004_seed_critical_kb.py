"""seed critical KB facts from prod observations

Revision ID: d1a00004kb
Revises: 8fe3ebe5800f
Create Date: 2026-04-18

Pre-populates the knowledge_base with facts that have proven critical in the
first day of the bot's operation — formats, aliases, and rules we've seen
the LLM mis-interpret without explicit hints.

Idempotent via ON CONFLICT on (category, key) if key is present, otherwise
on content hash. Since knowledge_base doesn't have a unique constraint we
rely on a sentinel note='seed:critical' to skip if already inserted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a00004kb"
down_revision: str | None = "8fe3ebe5800f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (category, key or None, content)
FACTS = [
    (
        "pattern",
        "X/Y=Z",
        "Формат обмена: X/Y=Z — X это РУБЛИ (много, обычно 6+ цифр), "
        "Y это USDT (меньше в ~80 раз), Z это КУРС (двузначное число 80-100). "
        "Пример: 280000/3480=80.46 → 280 000 ₽ обменены на 3480 USDT по курсу 80.46. "
        "НЕ путай второе и третье число.",
    ),
    (
        "alias",
        "рапа",
        "Рапира — биржа обмена наличного рубля на USDT.",
    ),
    (
        "alias",
        "Tpay",
        "TapBank — платёжка, принимающая рубли и отдающая USDT.",
    ),
    (
        "alias",
        "Merk",
        "Mercurio — ещё одна платёжка USDT.",
    ),
    (
        "alias",
        "нал",
        "Наличные (рубли).",
    ),
    (
        "alias",
        "додеп",
        "Дополнительный депозит партнёра (partner_deposit).",
    ),
    (
        "alias",
        "откуп",
        "Обмен рублей на USDT (синоним для exchange).",
    ),
    (
        "alias",
        "пятерик",
        "5000 рублей.",
    ),
    (
        "rule",
        None,
        "35% от каждого POA-снятия делится между партнёрами, пропорции КАЖДЫЙ РАЗ разные — "
        "не предполагать default, спрашивать если не указано.",
    ),
    (
        "rule",
        None,
        "Эквайринг (acquiring) обычно ежедневно, ~5000₽. "
        "Если его нет 2+ дня — напомнить.",
    ),
    (
        "preference",
        None,
        "Суммы в отчётах и превью округлять до $1 для USDT и до 100₽ для больших рублёвых. "
        "Для ₽-сумм меньше 10 000 — показывать точно.",
    ),
    (
        "preference",
        None,
        "Перед записью exchange ВСЕГДА проверь арифметику: amount_rub / fx_rate ≈ amount_usdt "
        "(допуск 0.5%). Если не сходится — confidence < 0.7 и ambiguities.",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    # Skip if already seeded
    res = bind.execute(
        sa.text("SELECT COUNT(*) FROM knowledge_base WHERE notes = 'seed:critical'")
    ).scalar()
    if res and res > 0:
        return
    for category, key, content in FACTS:
        bind.execute(
            sa.text(
                """
                INSERT INTO knowledge_base
                    (category, key, content, confidence, is_active, usage_count, notes)
                VALUES (:cat, :key, :content, 'confirmed', TRUE, 0, 'seed:critical')
                """
            ),
            {"cat": category, "key": key, "content": content},
        )


def downgrade() -> None:
    op.execute("DELETE FROM knowledge_base WHERE notes = 'seed:critical'")
