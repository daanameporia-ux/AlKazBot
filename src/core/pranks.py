"""Admin pranks — the playful side of the bot.

Behind `ENABLE_PRANKS=true`. Once per scheduled tick the bot has a low
chance (~1% per hour → ~1 prank a few days) to:
  * pin a jokey message to the group chat, or
  * (future) swap the chat avatar

Kept intentionally tame — pranks never touch accounting data.
"""

from __future__ import annotations

import contextlib
import random

from aiogram import Bot

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)

PRANK_MESSAGES = [
    "Напоминаю: если кабинет горит, не паникуем — я всё запомнил.",
    "Неофициальная стата: 73% операций я угадываю с первой. Докинь ещё, будет 80.",
    "Тык-тык, я живой. Если бы я умер, не было бы этого сообщения.",
    "Отчёт — сила. Беспорядок — смерть. /report когда уже?",
    "Скучаю по цифрам. Кидай курс / снятие / что угодно — разберу.",
    "Внимание, исторический факт: я был обучен на вашей же переписке. Страшно.",
]

# Probability per hourly tick; 1% → roughly 1 prank per 4 days.
PRANK_CHANCE = 0.01


async def maybe_prank(bot: Bot) -> None:
    if not settings.enable_pranks:
        return
    if not settings.main_chat_id:
        return
    if random.random() >= PRANK_CHANCE:
        return
    text = random.choice(PRANK_MESSAGES)
    try:
        sent = await bot.send_message(settings.main_chat_id, text)
        # Try to pin — best-effort, ignore if bot lacks rights or method fails.
        with contextlib.suppress(Exception):
            await bot.pin_chat_message(
                settings.main_chat_id, sent.message_id, disable_notification=True
            )
        log.info("prank_fired", message_id=sent.message_id)
    except Exception:
        log.exception("prank_send_failed")
