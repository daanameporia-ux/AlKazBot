"""Transcribe a voice and trigger the batch analyzer with it.

Called from:
  * `handlers/voice.on_voice` — immediately on message arrival (background
    task, so the handler returns fast).
  * `reminders._transcribe_pending_voices` — periodic backstop if the
    handler task died before finishing.
  * `handlers/mentions._transcribe_linked_voice` — competing path when
    the user @-mentions the bot right after a voice; mention wins the
    flush claim so we don't double-analyze.

Deduplication:
  * The `voice_messages.analyzed` column is an atomic claim flag.
  * Whoever wins `claim_voice_flush()` is responsible for firing the
    batch analyzer. Everyone else stays silent.
  * The background task in `transcribe_and_trigger` waits 2 s before
    claiming — giving an @-mention a chance to arrive and claim first.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from sqlalchemy import select, update

from src.bot.batcher import Batch, BufferedMessage
from src.core.batch_processor import make_flush_handler
from src.core.voice_transcribe import transcribe_voice_row
from src.db.models import VoiceMessage
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)

_VOICE_FLUSH_DEBOUNCE_SEC = 2.0


async def claim_voice_flush(voice_id: int) -> bool:
    """Atomic claim: the first caller to flip `analyzed` from False to True
    wins and should fire the batch analyzer. Subsequent callers get False
    and must stay silent — otherwise we'd run the LLM twice per voice.
    """
    async with session_scope() as session:
        res = await session.execute(
            update(VoiceMessage)
            .where(
                VoiceMessage.id == voice_id,
                VoiceMessage.analyzed.is_(False),
            )
            .values(analyzed=True)
            .returning(VoiceMessage.id)
        )
        return res.scalar_one_or_none() is not None


async def _fetch_meta(voice_id: int) -> tuple[str | None, int, int, int]:
    async with session_scope() as session:
        res = await session.execute(
            select(VoiceMessage).where(VoiceMessage.id == voice_id)
        )
        v = res.scalar_one_or_none()
        if v is None:
            return (None, 0, 0, 0)
        return (v.transcribed_text, v.chat_id, v.tg_user_id, v.tg_message_id)


async def transcribe_and_trigger(bot: Bot, voice_id: int) -> None:
    """Transcribe voice → short debounce → claim → fire analyzer.

    If a mention handler claims the flush during the debounce window we
    drop out silently; only one LLM call per voice.
    """
    try:
        async with session_scope() as session:
            text = await transcribe_voice_row(session, voice_id)
    except Exception:
        log.exception("voice_transcribe_failed", voice_id=voice_id)
        return

    if not text:
        return

    # Give the mention path a beat to claim — if the user is typing a
    # follow-up @-mention, we want the mention's flush, not ours.
    try:
        await asyncio.sleep(_VOICE_FLUSH_DEBOUNCE_SEC)
    except asyncio.CancelledError:
        log.info("voice_flush_cancelled_during_debounce", voice_id=voice_id)
        return

    if not await claim_voice_flush(voice_id):
        log.info("voice_flush_yielded_to_mention", voice_id=voice_id)
        return

    text, chat_id, tg_user_id, tg_message_id = await _fetch_meta(voice_id)
    if not text or not chat_id:
        return

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
        trigger_kind="voice_note",
    )
    flush = make_flush_handler(bot)
    try:
        await flush(batch)
    except Exception:
        log.exception("voice_trigger_flush_failed", voice_id=voice_id)
