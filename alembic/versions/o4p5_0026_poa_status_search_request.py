"""Add `search_request` value to clients.poa_status — отдельная стопка на
обращение в банк по розыску счёта.

Context (2026-05-04): для не-найденных через стандартный поиск клиентов
команда пишет официальное обращение в Сбер на розыск счёта (КУАР с
паспортными данными → банк ищет по своей базе → возвращает реквизиты).
Это **отдельный workflow** от `not_found`:

  - `not_found` = клиента физически не нашли в системе по обычному
    поиску, обращение НЕ оформляется (закрытый случай).
  - `search_request` = знаем что клиент скорее всего есть и деньги у
    него лежат, но обычный поиск не пробил → надо оформлять обращение,
    ждать ответ банка.

Backfill: Шидловский, Чернецкий, Андукс — упомянуты владельцем 02.05
в чате как кандидаты на розыск, теперь явно фиксируем.

Revision ID: o4p50026search
Revises: n3o40025kbcat
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op

revision = "o4p50026search"
down_revision = "n3o40025kbcat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_clients_poa_status", "clients", type_="check")
    op.create_check_constraint(
        "ck_clients_poa_status",
        "clients",
        "poa_status IN ('unchecked','has_balance','no_balance','not_found',"
        "'withdrawn','on_hold','search_request')",
    )

    # Backfill 3 клиента — owner request 04.05.2026.
    # ON CONFLICT — на случай если имена уже добавлены руками.
    op.execute(
        """
        INSERT INTO clients (name, poa_status, notes) VALUES
            ('Шидловский', 'search_request',
             'POA-клиент. Обычный поиск не нашёл — нужно официальное обращение в банк на розыск счёта. Добавлен 04.05.2026 распоряжением владельца.'),
            ('Чернецкий', 'search_request',
             'POA-клиент. Обычный поиск не нашёл — нужно официальное обращение в банк на розыск счёта. Добавлен 04.05.2026 распоряжением владельца.'),
            ('Андукс', 'search_request',
             'POA-клиент. Обычный поиск не нашёл — нужно официальное обращение в банк на розыск счёта. Добавлен 04.05.2026 распоряжением владельца.')
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "UPDATE clients SET poa_status='not_found' WHERE poa_status='search_request'"
    )
    op.drop_constraint("ck_clients_poa_status", "clients", type_="check")
    op.create_check_constraint(
        "ck_clients_poa_status",
        "clients",
        "poa_status IN ('unchecked','has_balance','no_balance','not_found',"
        "'withdrawn','on_hold')",
    )
