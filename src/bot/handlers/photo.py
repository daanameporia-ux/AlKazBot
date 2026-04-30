"""Photo intake — deferred Vision unless explicitly requested.

Policy (owner request 2026-04-20): the bot must NOT parse every photo
that lands in chat. Photos come in constantly (screenshots of incoming
SMS, Sber balance popups, memes, casual pics) and most of them are NOT
accounting events.

A photo fires Vision only when ONE of these is true:
  * caption contains `@Al_Kazbot` (explicit mention);
  * caption (or a tight context window) contains a trigger keyword
    from `trigger_keywords` — bot was addressed by nickname;
  * user replies to the photo with an @-mention asking to look at it
    (handled elsewhere via mentions.py — not here).

Everything else: log to message_log for context, silent no-op on LLM.
If a later trigger says "разбери фото выше", `media_context` downloads
the stored `file_id` and attaches the image to the analyzer then.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, now_ts
from src.config import settings
from src.core.batch_processor import (
    make_flush_handler as _mk,  # noqa: F401 — warm up the import chain
)
from src.core.keyword_match import find_hits
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="photo")


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


async def _caption_triggers_vision(caption: str, bot_username: str | None) -> bool:
    """True iff the photo's caption invites the bot to look at it.

    Criteria (OR'd):
      * @-mention of the bot;
      * any active trigger keyword appears as substring.
    """
    if not caption:
        return False
    if bot_username and f"@{bot_username.lower()}" in caption.lower():
        return True
    hits = await find_hits(caption)
    return bool(hits)


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    photos = message.photo or []
    if not photos:
        return

    # Gate: photo is analysed only when the user explicitly addresses
    # the bot via caption. Otherwise the photo is just context.
    caption = message.caption or ""
    me = await message.bot.me()
    if not await _caption_triggers_vision(caption, me.username):
        log.info(
            "photo_ignored_no_trigger",
            user_id=message.from_user.id,
            caption_preview=caption[:80],
        )
        return

    trigger = BufferedMessage(
        tg_message_id=message.message_id,
        tg_user_id=message.from_user.id,
        display_name=message.from_user.full_name,
        text=caption or "разбери фото",
        received_at=now_ts(),
    )
    buf = get_batch_buffer()
    await buf.flush_now(
        message.chat.id,
        trigger=trigger,
        trigger_kind="photo",
    )
    log.info(
        "photo_triggered_deferred_vision",
        user_id=message.from_user.id,
        caption_preview=caption[:80],
    )
