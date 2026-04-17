"""Handlers for messages that @-mention the bot. Real parsing starts on Stage 1."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="mentions")


async def _is_mention_of_me(message: Message) -> bool:
    me = await message.bot.me()
    text = message.text or message.caption or ""
    if not text:
        return False
    return f"@{me.username}".lower() in text.lower()


@router.message()
async def on_mention(message: Message) -> None:
    if not await _is_mention_of_me(message):
        return
    log.info("mention_received", text=message.text, user_id=message.from_user.id if message.from_user else None)
    await message.reply(
        "Слышу. Парсинг и LLM-разбор приезжает на Этапе 1 — пока я только "
        "логирую что пишут в чате. /help если надо списком что уже умею."
    )
