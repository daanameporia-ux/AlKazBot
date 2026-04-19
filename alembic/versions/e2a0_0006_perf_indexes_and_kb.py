"""perf indexes + second round of KB facts

Revision ID: e2a00006idx
Revises: 8d208fabd07a
Create Date: 2026-04-19

Indexes born out of the audit:
  * partner_contributions(source, source_ref_id) — attach_exchange fanout
  * poa_withdrawals(client_paid, withdrawal_date) — /debts + reminders
  * message_log(chat_id, created_at DESC) — recent_history loads
  * voice_messages(transcribed_text IS NULL, created_at) — pending queue
  * audit_log(table_name, record_id) — /undo lookups

KB: additional aliases + entity templates we've seen in the team's chat.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a00006idx"
down_revision: str | None = "8d208fabd07a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEXES = [
    ("ix_partner_contributions_source_ref",
     "partner_contributions",
     "source, source_ref_id"),
    ("ix_poa_open_debt",
     "poa_withdrawals",
     "client_paid, withdrawal_date"),
    ("ix_message_log_chat_ts",
     "message_log",
     "chat_id, created_at DESC"),
    ("ix_voice_pending",
     "voice_messages",
     "created_at",
     "WHERE transcribed_text IS NULL"),
    ("ix_audit_record",
     "audit_log",
     "table_name, record_id"),
]


FACTS = [
    (
        "entity",
        "Никонов",
        "Клиент доверенностей. Приходит раз в пару недель, типовые суммы "
        "50-150к рублей. Запрашивает наличный рубль → USDT по нашему курсу.",
    ),
    (
        "entity",
        "Миша",
        "Поставщик кабинетов Сбера. Типовая цена 22-28к/кабинет. Отгружает "
        "пачками 2-5 штук против предоплаты.",
    ),
    (
        "glossary",
        "залог",
        "cabinet.status='blocked' — временная блокировка кабинета, ждём "
        "разблокировки (обычно через нотариалку).",
    ),
    (
        "glossary",
        "нотариалка",
        "Восстановление заблокированного кабинета через юристов "
        "(cabinet.status='recovered' или возврат в 'in_stock').",
    ),
    (
        "glossary",
        "отработать",
        "Списать кабинет со склада после использования: "
        "cabinet.status='worked_out'.",
    ),
    (
        "glossary",
        "додеп",
        "partner_contribution с source='poa_share' — доля партнёра от "
        "комиссии по снятию (не начальный депозит).",
    ),
    (
        "preference",
        None,
        "В отчёте /report всегда показывать разбивку оборотки по локациям "
        "(TapBank, Mercurio, Rapira, наличные, Сбер-балансы).",
    ),
    (
        "preference",
        None,
        "Суммы в превью и отчётах показывать с пробелом как разделителем "
        "тысяч: '80 750$', '517 000 ₽'. Никакой научной нотации.",
    ),
    (
        "rule",
        None,
        "Если сумма долей партнёров + client_share_pct ≠ 100% — не "
        "записывать, поставить ambiguities и переспросить.",
    ),
    (
        "rule",
        None,
        "Предоплата закрывается (status='fulfilled') когда сумма кабинетов "
        "совпадает с суммой предоплаты. Если меньше — 'partial'. Разница — "
        "warning в preview.",
    ),
    (
        "pattern",
        None,
        "'Откуп' / 'откупил' / 'закупил USDT' — это intent=exchange. "
        "Если есть X/Y=Z в сообщении — бери числа оттуда.",
    ),
    (
        "pattern",
        None,
        "'Сняли с <имя> <сумма>' / '<имя> сегодня <сумма>к' — intent="
        "poa_withdrawal. Если в том же или следующем сообщении есть доли "
        "'мне N%, <имя> M%' — приложи partner_shares.",
    ),
]


def upgrade() -> None:
    # Indexes (CREATE IF NOT EXISTS so re-apply is safe)
    for entry in INDEXES:
        if len(entry) == 4:
            name, table, cols, where = entry
            where = f" {where}"
        else:
            name, table, cols = entry
            where = ""
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols}){where}")

    # KB seed — tagged notes='seed:enrich-v2' so we don't double-insert.
    bind = op.get_bind()
    res = bind.execute(
        sa.text("SELECT COUNT(*) FROM knowledge_base WHERE notes = 'seed:enrich-v2'")
    ).scalar()
    if res and res > 0:
        return
    for category, key, content in FACTS:
        bind.execute(
            sa.text(
                """
                INSERT INTO knowledge_base
                    (category, key, content, confidence, is_active, usage_count, notes)
                VALUES (:cat, :key, :content, 'confirmed', TRUE, 0, 'seed:enrich-v2')
                """
            ),
            {"cat": category, "key": key, "content": content},
        )


def downgrade() -> None:
    for entry in INDEXES:
        name = entry[0]
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("DELETE FROM knowledge_base WHERE notes = 'seed:enrich-v2'")
