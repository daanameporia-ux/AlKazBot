"""Deferred PDF/photo loading for explicit "look above" requests.

Incoming media is only logged as Telegram metadata. The actual download,
PDF extraction, or Vision input happens here, when the current trigger
message clearly asks the bot to inspect recent attachments.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from sqlalchemy import or_, select

from src.bot.batcher import Batch
from src.core.pdf_ingest import ALIEN_PDF_HINT, SBER_HINT, extract_pdf_text, is_sber_statement
from src.db.models import MessageLog
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)

MEDIA_LOOKBACK_HOURS = 2
MAX_MEDIA_ITEMS = 5
MAX_IMAGE_ITEMS = 2
MAX_PDF_ITEMS = 4
MAX_PDF_BYTES = 15 * 1024 * 1024
PDF_TEXT_CHAR_CAP = 20_000
PDF_TOTAL_CHAR_CAP = 45_000
PDF_EXTRACT_TIMEOUT_SEC = 45

MEDIA_WORDS = (
    "pdf",
    "пдф",
    "документ",
    "файл",
    "выписк",
    "картин",
    "фото",
    "фотк",
    "скрин",
    "изображен",
)
MEDIA_ACTION_WORDS = (
    "разбери",
    "разобрать",
    "посмотри",
    "глянь",
    "прочитай",
    "считай",
    "что там",
    "что на",
    "выше",
    "предыдущ",
    "прошл",
    "скинул",
    "скидывал",
)


@dataclass(slots=True)
class MediaContext:
    text: str = ""
    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    source_message_ids: list[int] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.text or self.content_blocks)


def asks_for_recent_media(text: str, *, trigger_has_media: bool = False) -> bool:
    """Detect explicit requests to inspect media.

    If the trigger message itself carries a photo/PDF and is addressed to
    the bot, the surrounding handler already established intent; no extra
    keyword is needed here.
    """
    if trigger_has_media:
        return True
    lo = (text or "").lower()
    if not lo:
        return False
    has_action = any(w in lo for w in MEDIA_ACTION_WORDS)
    has_media_word = any(w in lo for w in MEDIA_WORDS)
    has_above_reference = "выше" in lo or "предыдущ" in lo
    return has_action and (has_media_word or has_above_reference)


async def collect_requested_media_context(
    *,
    bot: Bot | None,
    batch: Batch,
) -> MediaContext | None:
    if bot is None or batch.trigger is None:
        return None

    trigger_id = batch.trigger.tg_message_id
    async with session_scope() as session:
        trigger = await _find_message(session, batch.chat_id, trigger_id)
        reply_media = (
            await _find_message(session, batch.chat_id, trigger.reply_to_tg_message_id)
            if trigger and trigger.reply_to_tg_message_id
            else None
        )
        trigger_has_media = bool(
            trigger and trigger.media_type in ("photo", "pdf") and trigger.media_file_id
        ) or bool(
            reply_media
            and reply_media.media_type in ("photo", "pdf")
            and reply_media.media_file_id
        )
        if not asks_for_recent_media(batch.trigger.text, trigger_has_media=trigger_has_media):
            return None

        rows = await _select_media_rows(session, batch.chat_id, trigger)

    if not rows:
        return None

    ctx = MediaContext(
        text=(
            "# Вложения по текущему запросу\n"
            "Эти файлы скачаны и прочитаны только потому, что текущий trigger "
            "явно попросил разобрать фото/PDF выше или само обращение содержит вложение."
        )
    )
    pdf_chars = 0
    image_count = 0
    pdf_count = 0

    for row in rows:
        if not row.media_file_id:
            continue
        if row.media_type == "photo":
            if image_count >= MAX_IMAGE_ITEMS:
                continue
            if await _append_image(bot, row, ctx):
                image_count += 1
            continue
        if row.media_type == "pdf":
            if pdf_count >= MAX_PDF_ITEMS or pdf_chars >= PDF_TOTAL_CHAR_CAP:
                continue
            used = await _append_pdf(
                bot,
                row,
                ctx,
                remaining_chars=max(PDF_TOTAL_CHAR_CAP - pdf_chars, 0),
            )
            if used:
                pdf_chars += used
                pdf_count += 1

    return ctx if ctx.has_content else None


async def _find_message(session, chat_id: int, tg_message_id: int) -> MessageLog | None:
    res = await session.execute(
        select(MessageLog).where(
            MessageLog.chat_id == chat_id,
            MessageLog.tg_message_id == tg_message_id,
        )
    )
    return res.scalar_one_or_none()


async def _select_media_rows(
    session,
    chat_id: int,
    trigger: MessageLog | None,
) -> list[MessageLog]:
    media_filter = (
        MessageLog.chat_id == chat_id,
        MessageLog.media_type.in_(("photo", "pdf")),
        MessageLog.media_file_id.isnot(None),
    )
    reply_to = trigger.reply_to_tg_message_id if trigger else None
    if reply_to:
        res = await session.execute(
            select(MessageLog)
            .where(*media_filter)
            .where(
                or_(
                    MessageLog.tg_message_id == reply_to,
                    MessageLog.tg_message_id == (trigger.tg_message_id if trigger else None),
                )
            )
            .order_by(MessageLog.id.asc())
        )
        rows = list(res.scalars().all())
        if rows:
            return rows[:MAX_MEDIA_ITEMS]

    cutoff = datetime.now(UTC) - timedelta(hours=MEDIA_LOOKBACK_HOURS)
    before_id = trigger.id if trigger else None
    q = (
        select(MessageLog)
        .where(*media_filter)
        .where(MessageLog.created_at >= cutoff)
        .order_by(MessageLog.id.desc())
        .limit(MAX_MEDIA_ITEMS)
    )
    if before_id is not None:
        q = q.where(MessageLog.id <= before_id)
    res = await session.execute(q)
    rows = list(res.scalars().all())
    rows.reverse()
    return rows


async def _append_image(bot: Bot, row: MessageLog, ctx: MediaContext) -> bool:
    try:
        buf = io.BytesIO()
        await bot.download(row.media_file_id, destination=buf)
    except Exception:
        log.exception("deferred_photo_download_failed", tg_message_id=row.tg_message_id)
        ctx.text += f"\n\n[Фото id={row.tg_message_id}] не удалось скачать."
        return False

    img_b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    ctx.content_blocks.append(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": img_b64,
            },
        }
    )
    ctx.text += (
        f"\n\n[Фото id={row.tg_message_id}, size={row.media_file_size or '?'}B] "
        "Изображение приложено отдельным image-блоком. Если на нём нет учётной "
        "операции, отвечай chat_only."
    )
    if row.tg_message_id:
        ctx.source_message_ids.append(row.tg_message_id)
    return True


async def _append_pdf(
    bot: Bot,
    row: MessageLog,
    ctx: MediaContext,
    *,
    remaining_chars: int,
) -> int:
    if row.media_file_size and row.media_file_size > MAX_PDF_BYTES:
        ctx.text += (
            f"\n\n[PDF id={row.tg_message_id}, file={row.media_file_name or '?'}] "
            f"пропущен: {row.media_file_size // 1024 // 1024} МБ больше лимита."
        )
        return 0

    try:
        buf = io.BytesIO()
        await bot.download(row.media_file_id, destination=buf)
    except Exception:
        log.exception("deferred_pdf_download_failed", tg_message_id=row.tg_message_id)
        ctx.text += f"\n\n[PDF id={row.tg_message_id}] не удалось скачать."
        return 0

    try:
        cap = min(PDF_TEXT_CHAR_CAP, remaining_chars)
        text = await asyncio.wait_for(
            asyncio.to_thread(extract_pdf_text, buf.getvalue(), max_chars=cap),
            timeout=PDF_EXTRACT_TIMEOUT_SEC,
        )
    except TimeoutError:
        log.warning("deferred_pdf_extract_timeout", tg_message_id=row.tg_message_id)
        ctx.text += f"\n\n[PDF id={row.tg_message_id}] парсился слишком долго, пропущен."
        return 0
    except Exception:
        log.exception("deferred_pdf_extract_failed", tg_message_id=row.tg_message_id)
        ctx.text += f"\n\n[PDF id={row.tg_message_id}] не удалось прочитать как текст."
        return 0

    if not text:
        ctx.text += f"\n\n[PDF id={row.tg_message_id}] пустой или сканированный."
        return 0

    is_sber = is_sber_statement(text)
    hint = SBER_HINT if is_sber else ALIEN_PDF_HINT
    title = row.media_file_name or "unknown.pdf"
    ctx.text += (
        f"\n\n[PDF id={row.tg_message_id}, file={title}, "
        f"size={row.media_file_size or '?'}B, is_sber={is_sber}]\n"
        f"{hint}\n\n{text}"
    )
    if row.tg_message_id:
        ctx.source_message_ids.append(row.tg_message_id)
    return len(text)
