"""Incoming PDF handler.

PDFs are not downloaded or parsed on arrival. The middleware stores their
Telegram `file_id`; this handler only triggers analysis when the PDF caption
explicitly addresses the bot. The common "sent PDF, then asked in text/voice
to parse above" path is handled later by `media_context`.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, is_main_group, now_ts
from src.config import settings
from src.core.keyword_match import find_hits
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="documents")

MAX_PDF_BYTES = 15 * 1024 * 1024


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


async def _caption_triggers_pdf(caption: str, bot_username: str | None) -> bool:
    if not caption:
        return False
    if bot_username and f"@{bot_username.lower()}" in caption.lower():
        return True
    hits = await find_hits(caption)
    return bool(hits)


@router.message(F.document.mime_type == "application/pdf")
async def on_pdf(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    doc = message.document
    if doc is None:
        return

    # Pre-download size gate.
    if doc.file_size and doc.file_size > MAX_PDF_BYTES:
        await message.reply(
            f"PDF большой ({doc.file_size // 1024 // 1024} МБ). "
            f"Лимит {MAX_PDF_BYTES // 1024 // 1024} МБ — такие не тяну. "
            "Если это скан — пришли фото страниц."
        )
        return

    caption = message.caption or ""
    me = await message.bot.me()
    if not await _caption_triggers_pdf(caption, me.username):
        log.info(
            "pdf_deferred_no_trigger",
            user_id=message.from_user.id,
            file_name=doc.file_name,
            file_size=doc.file_size,
        )
        return

    body_msg = BufferedMessage(
        tg_message_id=message.message_id,
        tg_user_id=message.from_user.id,
        display_name=message.from_user.full_name,
        text=caption or "разбери PDF",
        received_at=now_ts(),
    )

    buf_manager = get_batch_buffer()
    if is_main_group(message.chat.id):
        await buf_manager.flush_now(
            message.chat.id,
            trigger=body_msg,
            trigger_kind="document",
        )
    else:
        # Edge case: PDFs in private DMs. Use the same machinery but
        # seeded as a synthetic single-chat batch.
        await buf_manager.flush_now(
            message.chat.id,
            trigger=body_msg,
            trigger_kind="document",
        )

    log.info(
        "pdf_triggered_deferred_parse",
        file_name=doc.file_name,
        file_size=doc.file_size,
    )
