"""Background voice transcription.

Policy: a voice note alone does NOT trigger an LLM reply. It only gets
transcribed so the text lands in `message_log` as recent context. The
bot answers a voice only when the user subsequently @-mentions or
replies — that path runs through `handlers/mentions._transcribe_linked_voice`
and fires the analyzer explicitly with a mention trigger.

This keeps token spend predictable and matches user expectation:
"голосовые копятся, отвечаю когда меня тегают".
"""

from __future__ import annotations

from src.core.voice_transcribe import transcribe_voice_row
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


async def transcribe_only(voice_id: int) -> None:
    """Transcribe a voice row; no flush, no LLM call."""
    try:
        async with session_scope() as session:
            await transcribe_voice_row(session, voice_id)
    except Exception:
        log.exception("voice_transcribe_failed", voice_id=voice_id)
