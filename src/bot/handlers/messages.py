"""Catch-all for non-command, non-mention messages.

Previously a no-op. Now it's the passive intake for the batch buffer —
messages from whitelisted users in the main group accumulate and are
analyzed in bulk. Non-main chats and non-text messages are ignored.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, is_main_group, now_ts
from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="messages")


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


@router.message()
async def on_message(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    # Only accumulate from the configured main group. Private chats and
    # other groups go through the regular /commands + @-mention path.
    if not is_main_group(message.chat.id):
        return
    text = message.text or message.caption or ""
    if not text:
        return

    buf = get_batch_buffer()
    await buf.add(
        message.chat.id,
        BufferedMessage(
            tg_message_id=message.message_id,
            tg_user_id=message.from_user.id,
            display_name=message.from_user.full_name,
            text=text,
            received_at=now_ts(),
        ),
    )
