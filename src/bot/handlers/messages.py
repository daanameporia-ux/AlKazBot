"""Catch-all for non-command, non-mention text messages.

Policy: no more automatic "3-minute silence" or "8-message pile-up"
flush. The bot only answers when:
  * user @-mentions / replies (see `mentions.py`)
  * user sends a slash-command (see `commands.py`)
  * **OR** the text contains one of the configured trigger keywords
    (`trigger_keywords` table, managed via `/keywords`).

Everything else is logged via `MessageLoggingMiddleware` but never
touches the LLM.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, is_main_group, now_ts
from src.config import settings
from src.core.keyword_match import find_hits
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="messages")


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


# Text/caption only — other media types (voice/photo/document/sticker) are
# owned by their own routers.
@router.message(F.text | F.caption)
async def on_message(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    if not is_main_group(message.chat.id):
        return
    text = message.text or message.caption or ""
    if not text:
        return

    hits = await find_hits(text)
    if not hits:
        # Message logged by middleware already; no LLM call.
        return

    log.info(
        "keyword_trigger",
        user_id=message.from_user.id,
        hits=hits,
        text_preview=text[:100],
    )
    trigger = BufferedMessage(
        tg_message_id=message.message_id,
        tg_user_id=message.from_user.id,
        display_name=message.from_user.full_name,
        text=text,
        received_at=now_ts(),
    )
    buf = get_batch_buffer()
    await buf.flush_now(message.chat.id, trigger=trigger, trigger_kind="keyword")
