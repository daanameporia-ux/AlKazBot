"""KB seed: Карен (поставщик), glossary «выебан / шлак / комплект / в
работу / отработал», rule что входящие клиентские платежи не
записываем операциями.

Derived from live chat 2026-04-19 — 2026-04-20 where the team used
jargon the bot had no KB for: «за этот шлак 261к заплатили», «выебаны»,
«в работу», «комплекты». Bot partially misinterpreted semantics.

Idempotent via NOT EXISTS + category/content match.

Revision ID: b2c30014karen
Revises: a1b20013wakewords
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op

revision = "b2c30014karen"
down_revision = "a1b20013wakewords"
branch_labels = None
depends_on = None


_FACTS = [
    # category, key, content, confidence
    (
        "entity",
        "Карен",
        "Поставщик Сбер-кабинетов (наряду с Мишей). Отгружает "
        "пачками по 10-15 штук. Цены плавают — основной тариф "
        "~28к/кабинет, иногда по 20к (если кабинет «выебан» без "
        "доверенности). Предоплата даётся заранее под партию.",
        "confirmed",
    ),
    (
        "glossary",
        "выебан",
        "Кабинет уже отработан и списывается со склада "
        "(cabinet.status='worked_out'). Используется вместо «отработал» "
        "в команде.",
        "confirmed",
    ),
    (
        "glossary",
        "шлак",
        "Партия / пачка Сбер-кабинетов (сленг команды). «Заплатили "
        "261к за этот шлак» = предоплата 261 000 ₽ за партию.",
        "confirmed",
    ),
    (
        "glossary",
        "комплект",
        "Сбер-кабинет как инвентарная единица (ФИО + телефон + "
        "реквизиты). Синоним «кабинет».",
        "confirmed",
    ),
    (
        "glossary",
        "в работу",
        "Кабинет ставят в работу — cabinet.status in_stock → in_use. "
        "Ждём поступлений на этот кабинет. НЕ путать с «отработан» "
        "(worked_out).",
        "confirmed",
    ),
    (
        "glossary",
        "отработан",
        "Кабинет закончил цикл — cabinet.status in_use → worked_out. "
        "Синонимы: «выебан», «выебали», «списали».",
        "confirmed",
    ),
    (
        "rule",
        None,
        "Входящие клиентские платежи на Сбер-счёт (СБП от физика, "
        "перевод с карты клиента, пополнение ATM на наш кабинет) — "
        "НЕ записываются как отдельные операции. Они учитываются "
        "одной строкой в wallet_snapshot.sber_balances во время "
        "/report. Скрины СМС о поступлении — тоже не превращать в "
        "operations, просто короткая сводка в chat_reply.",
        "confirmed",
    ),
    (
        "pattern",
        None,
        "«Заплатили X за Y кабинетов» / «за пачку X» / «за шлак X» / "
        "«отдал X [имени] за Y» → intent=prepayment_given, "
        "supplier=имя поставщика, amount_rub=X, expected_cabinets=Y.",
        "confirmed",
    ),
    (
        "pattern",
        None,
        "«В работу <имена>» / «завтра в работу X, Y» / «ставлю X в "
        "работу» → intent=cabinet_in_use (НЕ worked_out). "
        "Живой баг 2026-04-19: бот путал эти смыслы.",
        "confirmed",
    ),
]


def upgrade() -> None:
    for cat, key, content, confidence in _FACTS:
        key_clause = f"AND key = '{key.replace(chr(39), chr(39)*2)}'" if key else "AND key IS NULL"
        # Use parameterised query via op.execute with an SQL literal —
        # Alembic doesn't give us bind params here, so escape single quotes.
        esc_content = content.replace("'", "''")
        esc_key = f"'{key.replace(chr(39), chr(39)*2)}'" if key else "NULL"
        op.execute(
            f"""
            INSERT INTO knowledge_base (category, key, content, confidence, is_active, usage_count)
            SELECT '{cat}', {esc_key}, '{esc_content}', '{confidence}', TRUE, 0
            WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_base
                WHERE category = '{cat}'
                  AND content = '{esc_content}'
                  AND is_active = TRUE
                  {key_clause}
            )
            """
        )


def downgrade() -> None:
    for cat, key, content, _ in _FACTS:
        esc_content = content.replace("'", "''")
        key_clause = (
            f"AND key = '{key.replace(chr(39), chr(39)*2)}'" if key else "AND key IS NULL"
        )
        op.execute(
            f"""
            UPDATE knowledge_base
            SET is_active = FALSE
            WHERE category = '{cat}'
              AND content = '{esc_content}'
              {key_clause}
            """
        )
