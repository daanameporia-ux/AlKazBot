#!/usr/bin/env python
"""Оставить след в чате о ручном изменении БД.

Когда я (Claude через Code) делаю UPDATE/INSERT прямо в БД bypass'ом
учётного flow бота, бот не видит этого факта и при следующем упоминании
тех же имён/сумм пере-предлагает preview. Дубль работы.

Этот helper вставляет synthetic bot-message в `message_log` с описанием
того что было сделано. Запись всплывает в recent_history бота → он видит
«✓ Manually recorded …» → понимает что данные уже учтены, новые
preview не плодит.

Usage:
  python scripts/ack_manual_change.py "Шидловский, Чернецкий, Андукс
  → status=search_request. Анучкин → unchecked. Тивучкин = alias на
  Тивунчик. Все добавлены вручную через SQL/миграцию 0026."

Без аргументов берёт текст из stdin.

Текст начинается с маркера `[manual-db-ack]` чтобы бот мог его
выделить в потоке (и в KB-rule можно научить «если видишь в
recent_history `[manual-db-ack]` про X — НЕ создавай preview для X»).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import text as _sa_text
from src.config import settings
from src.db.session import session_scope


async def ack(message: str, *, chat_id: int | None = None) -> int:
    """Insert synthetic bot reply in message_log. Returns inserted id."""
    target_chat = chat_id or settings.main_chat_id
    if not target_chat:
        raise ValueError("main_chat_id is 0 — set MAIN_CHAT_ID env var")
    body = f"[manual-db-ack] {message}"
    async with session_scope() as session:
        # tg_message_id: используем синтетический отрицательный id,
        # чтобы не пересечься с реальными tg ids (positive ints).
        # Берём -unix_ts чтобы был уникален.
        synth_id = -int(datetime.now(UTC).timestamp())
        res = await session.execute(
            _sa_text(
                """
                INSERT INTO message_log
                    (tg_message_id, chat_id, text, has_media, is_bot, is_mention,
                     intent_detected, created_at)
                VALUES
                    (:mid, :cid, :body, false, true, false,
                     'manual_ack', NOW())
                RETURNING id
                """
            ),
            {"mid": synth_id, "cid": target_chat, "body": body},
        )
        return res.scalar_one()


async def main() -> None:
    msg = " ".join(sys.argv[1:]) if len(sys.argv) >= 2 else sys.stdin.read().strip()
    if not msg:
        print("usage: ack_manual_change.py <description>", file=sys.stderr)
        sys.exit(1)
    inserted = await ack(msg)
    print(f"✓ logged manual ack as message_log.id={inserted}")
    print(f"  chat_id={settings.main_chat_id}")
    print(f"  body: [manual-db-ack] {msg[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
