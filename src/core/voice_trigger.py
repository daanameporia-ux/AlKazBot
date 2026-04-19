"""Background voice transcription + keyword gating.

Flow:
  1. Voice handler stores OGG in `voice_messages` and spawns a task
     that calls `transcribe_and_keyword_check`.
  2. We run whisper locally (no API tokens) — transcript lands in
     `message_log` as context and the OGG bytes are wiped.
  3. The local keyword matcher scans the transcript. If nothing
     matches, we stay silent.
  4. If a keyword matches, we fire the batch analyzer with the voice
     as a trigger — Claude decides whether the voice is actually
     addressed to the bot or it's a false positive.

This mirrors how text messages are handled (see `handlers/messages`):
all messages live in context cheaply, LLM only fires on keyword hits
or explicit triggers (@-mention, reply, command).
"""

from __future__ import annotations

from aiogram import Bot
from sqlalchemy import select

from src.bot.batcher import Batch, BufferedMessage
from src.core.batch_processor import make_flush_handler
from src.core.keyword_match import find_hits
from src.core.voice_transcribe import transcribe_voice_row
from src.db.models import VoiceMessage
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


async def transcribe_only(voice_id: int) -> None:
    """Transcribe a voice row; no keyword check, no flush. Used by the
    periodic cleanup backstop."""
    try:
        async with session_scope() as session:
            await transcribe_voice_row(session, voice_id)
    except Exception:
        log.exception("voice_transcribe_failed", voice_id=voice_id)


async def transcribe_and_keyword_check(bot: Bot, voice_id: int) -> None:
    """Transcribe, then fire the analyzer ONLY if the transcript contains
    a trigger keyword. Otherwise stays silent — no LLM call."""
    try:
        async with session_scope() as session:
            text = await transcribe_voice_row(session, voice_id)
    except Exception:
        log.exception("voice_transcribe_failed", voice_id=voice_id)
        return

    if not text:
        return

    hits = await find_hits(text)
    if not hits:
        log.info("voice_no_keyword_silent", voice_id=voice_id, text_preview=text[:100])
        return

    # Keyword hit — fetch metadata for the trigger message.
    async with session_scope() as session:
        res = await session.execute(
            select(VoiceMessage).where(VoiceMessage.id == voice_id)
        )
        v = res.scalar_one_or_none()
        if v is None:
            return
        chat_id = v.chat_id
        tg_user_id = v.tg_user_id
        tg_message_id = v.tg_message_id

    log.info(
        "voice_keyword_trigger",
        voice_id=voice_id,
        hits=hits,
        text_preview=text[:100],
    )
    trigger = BufferedMessage(
        tg_message_id=tg_message_id,
        tg_user_id=tg_user_id,
        display_name=None,
        text=f"[voice] {text}",
        received_at=0.0,
    )
    batch = Batch(
        chat_id=chat_id,
        messages=[],
        trigger=trigger,
        trigger_kind="voice_keyword",
    )
    flush = make_flush_handler(bot)
    try:
        await flush(batch)
    except Exception:
        log.exception("voice_keyword_flush_failed", voice_id=voice_id)
