"""Incoming document (PDF) handler.

Right now only PDFs are handled — specifically Sberbank statements.
Other file types are ignored (could be extended later for XLSX, images-as-
files, etc.).

Flow:
  1. Download the file via Bot API.
  2. Extract text with pdfminer.six.
  3. Inject a synthetic "document message" into the batch buffer tagged
     with a Sber-statement hint, then flush_now so the analyzer produces
     preview cards for every operation in the statement.
"""

from __future__ import annotations

import asyncio
import io

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, is_main_group, now_ts
from src.config import settings
from src.core.pdf_ingest import (
    ALIEN_PDF_HINT,
    SBER_HINT,
    extract_pdf_text,
    is_sber_statement,
)
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="documents")

# Size hard-cap before even downloading. Real statements weigh <1 MB
# (one-month = ~300 KB). Anything above 15 MB is either a scan (not our
# territory) or a DoS attempt. Railway container has ~512 MB RAM — a 50 MB
# malformed PDF through pdfminer eats the process.
MAX_PDF_BYTES = 15 * 1024 * 1024
# Soft deadline for the pdfminer.extract_text call — a malformed PDF can
# spin pdfminer indefinitely.
PDF_EXTRACT_TIMEOUT_SEC = 60


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


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

    await message.reply(
        f"Принял PDF «{doc.file_name}» ({doc.file_size // 1024 if doc.file_size else '?'} КБ). "
        "Разбираю, через 10-20 сек покажу что нашёл…"
    )

    # Download
    try:
        buf = io.BytesIO()
        await message.bot.download(doc, destination=buf)
    except Exception:
        log.exception("pdf_download_failed")
        await message.reply("Не смог скачать файл. Попробуй ещё раз.")
        return

    pdf_bytes = buf.getvalue()
    try:
        # pdfminer is CPU-bound and synchronous — off to a worker thread so
        # we don't freeze polling while a fat statement is parsed. Wrap in
        # wait_for so a malformed PDF can't stall us forever.
        text = await asyncio.wait_for(
            asyncio.to_thread(extract_pdf_text, pdf_bytes),
            timeout=PDF_EXTRACT_TIMEOUT_SEC,
        )
    except TimeoutError:
        log.warning("pdf_extract_timeout", file_name=doc.file_name)
        await message.reply(
            "PDF парсится слишком долго — либо битый, либо гигантский. Не взял."
        )
        return
    except Exception:
        log.exception("pdf_extract_failed")
        await message.reply(
            "Не смог прочитать PDF. Возможно это скан — сфотографируй и пришли фото."
        )
        return

    if not text:
        await message.reply("PDF пустой или сканированный. Фото пришли, разберусь.")
        return

    is_sber = is_sber_statement(text)
    # Non-Sber PDFs = random bank statements / contracts / receipts. Never
    # auto-parse into operations; treat as an analysis-only doc.
    hint = SBER_HINT if is_sber else ALIEN_PDF_HINT
    header = (
        f"[PDF-документ: {doc.file_name}, {doc.file_size or '?'}B, "
        f"is_sber={is_sber}]\n"
        + hint
        + "\n\n"
        + text
    )

    # Synthesise a buffered-message entry for the analyzer. This travels
    # alongside any passive messages already in the buffer for this chat.
    body_msg = BufferedMessage(
        tg_message_id=message.message_id,
        tg_user_id=message.from_user.id,
        display_name=message.from_user.full_name,
        text=header,
        received_at=now_ts(),
    )

    # If we're not in the main group, still process — just not via passive
    # batching (group-level) — flush immediately as a single-message batch.
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
        "pdf_ingested",
        file_name=doc.file_name,
        chars=len(text),
        is_sber=is_sber,
    )
