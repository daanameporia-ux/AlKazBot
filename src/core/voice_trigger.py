"""Transcribe a voice and trigger the batch analyzer with it.

Called from:
  * `handlers/voice.on_voice` — immediately on message arrival (background
    task, so the handler returns fast).
  * `reminders._transcribe_pending_voices` — periodic backstop if the
    handler task died before finishing.

The idea: every voice note is a first-class participant in the
conversation. After transcription we synthesize a trigger message for
the BatchBuffer / analyzer so Claude sees it as if the user typed the
text out loud.
"""

from __future__ import annotations

from aiogram import Bot

from src.bot.batcher import Batch, BufferedMessage
from src.core.batch_processor import make_flush_handler
from src.core.voice_transcribe import transcribe_voice_row
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


async def transcribe_and_trigger(bot: Bot, voice_id: int) -> None:
    """Transcribe the specified voice row, then fire a batch-analyzer flush
    with this voice as the trigger so Claude answers right away.

    Idempotent — if the voice was already transcribed by another worker we
    skip the transcription but still fire the trigger once per call.
    """
    text: str | None = None
    chat_id: int = 0
    tg_user_id: int = 0
    tg_message_id: int = 0

    try:
        async with session_scope() as session:
            text = await transcribe_voice_row(session, voice_id)
            # Pull the row for metadata (we need chat/user for trigger).
            from sqlalchemy import select

            from src.db.models import VoiceMessage

            res = await session.execute(
                select(VoiceMessage).where(VoiceMessage.id == voice_id)
            )
            v = res.scalar_one_or_none()
            if v is not None:
                chat_id = v.chat_id
                tg_user_id = v.tg_user_id
                tg_message_id = v.tg_message_id
                if text is None:
                    text = v.transcribed_text
    except Exception:
        log.exception("voice_transcribe_failed", voice_id=voice_id)
        return
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
