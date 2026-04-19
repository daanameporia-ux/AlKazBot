"""Inline voice transcription via faster-whisper.

Runs on the bot's Railway container. Model is `small` multilingual, int8
quantised, pre-downloaded into the image at build time so the first call
doesn't stall. Singleton — loaded once per process.

Concurrency:
  * `_model_lock` (threading) — protects lazy model load from two
    threads double-instantiating on cold start.
  * `_voice_locks` (asyncio per-voice-id) — protects transcription of
    the *same* voice row from running whisper twice when both the
    voice handler's background task and the mention handler race.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MessageLog, VoiceMessage
from src.logging_setup import get_logger

log = get_logger(__name__)

_MODEL_NAME = "small"
_MODEL_DEVICE = "cpu"
_MODEL_COMPUTE = "int8"
_CACHE_DIR = os.environ.get("FASTER_WHISPER_CACHE_DIR", "/app/.whisper-cache")

_model = None  # type: ignore[var-annotated]
_model_lock = threading.Lock()

_voice_locks: dict[int, asyncio.Lock] = {}
_voice_locks_guard = asyncio.Lock()


def _load_model():
    global _model
    if _model is not None:
        return _model
    # Double-checked locking so two to_thread callers don't both build.
    with _model_lock:
        if _model is not None:
            return _model
        from faster_whisper import WhisperModel

        log.info("loading_whisper_model", name=_MODEL_NAME)
        _model = WhisperModel(
            _MODEL_NAME,
            device=_MODEL_DEVICE,
            compute_type=_MODEL_COMPUTE,
            download_root=_CACHE_DIR,
        )
        log.info("whisper_model_loaded")
        return _model


async def _get_voice_lock(voice_id: int) -> asyncio.Lock:
    async with _voice_locks_guard:
        lock = _voice_locks.get(voice_id)
        if lock is None:
            lock = asyncio.Lock()
            _voice_locks[voice_id] = lock
        return lock


async def _release_voice_lock(voice_id: int) -> None:
    """Best-effort cleanup so the dict doesn't grow unbounded."""
    async with _voice_locks_guard:
        lock = _voice_locks.get(voice_id)
        if lock is not None and not lock.locked():
            _voice_locks.pop(voice_id, None)


def _transcribe_sync(ogg: bytes, language: str = "ru") -> str:
    model = _load_model()
    # faster-whisper wants a filesystem path.
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(ogg)
        path = f.name
    try:
        segments, _info = model.transcribe(
            path, language=language, vad_filter=True
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        Path(path).unlink(missing_ok=True)


async def transcribe_bytes(ogg: bytes, *, language: str = "ru") -> str:
    return await asyncio.to_thread(_transcribe_sync, ogg, language)


async def transcribe_voice_row(
    session: AsyncSession, voice_id: int
) -> str | None:
    """Transcribe a specific voice_messages row, persist the text,
    wipe OGG bytes, and inject into message_log so analyze_batch sees it
    in recent history. Idempotent: if already transcribed, returns text.

    Thread-safe per voice_id — only one whisper pass even when called
    concurrently from the handler's bg task and from the mention path.
    """
    lock = await _get_voice_lock(voice_id)
    async with lock:
        try:
            res = await session.execute(
                select(VoiceMessage).where(VoiceMessage.id == voice_id)
            )
            row = res.scalar_one_or_none()
            if row is None:
                return None
            if row.transcribed_text is not None:
                return row.transcribed_text
            if not row.ogg_data:
                return None

            text = await transcribe_bytes(bytes(row.ogg_data))
            if not text:
                text = "(тишина)"

            row.transcribed_text = text
            row.transcribed_at = datetime.now(UTC)
            row.ogg_data = b""

            # Mirror into message_log so the analyzer / history sees it.
            session.add(
                MessageLog(
                    tg_message_id=row.tg_message_id,
                    tg_user_id=row.tg_user_id,
                    chat_id=row.chat_id,
                    text=f"[voice] {text}",
                    has_media=True,
                    is_bot=False,
                    is_mention=False,
                    intent_detected="voice_transcript",
                )
            )
            return text
        finally:
            await _release_voice_lock(voice_id)


async def find_recent_voice_by_user(
    session: AsyncSession,
    *,
    chat_id: int,
    tg_user_id: int,
    within_seconds: int = 600,
) -> VoiceMessage | None:
    """Most recent untranscribed voice from this user in this chat, within
    a short window. Used when the user @-mentions the bot right after a
    voice note — we pair them.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
    res = await session.execute(
        select(VoiceMessage)
        .where(
            VoiceMessage.chat_id == chat_id,
            VoiceMessage.tg_user_id == tg_user_id,
            VoiceMessage.transcribed_text.is_(None),
            VoiceMessage.created_at >= cutoff,
        )
        .order_by(VoiceMessage.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def find_voice_by_message_id(
    session: AsyncSession, *, chat_id: int, tg_message_id: int
) -> VoiceMessage | None:
    res = await session.execute(
        select(VoiceMessage).where(
            VoiceMessage.chat_id == chat_id,
            VoiceMessage.tg_message_id == tg_message_id,
        )
    )
    return res.scalar_one_or_none()
