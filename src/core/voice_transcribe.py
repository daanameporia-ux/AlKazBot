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


def _transcribe_sync(
    ogg: bytes,
    language: str = "ru",
    initial_prompt: str | None = None,
) -> str:
    model = _load_model()
    # faster-whisper wants a filesystem path.
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(ogg)
        path = f.name
    try:
        segments, _info = model.transcribe(
            path,
            language=language,
            vad_filter=True,
            initial_prompt=initial_prompt,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        Path(path).unlink(missing_ok=True)


async def transcribe_bytes(
    ogg: bytes,
    *,
    language: str = "ru",
    initial_prompt: str | None = None,
) -> str:
    return await asyncio.to_thread(
        _transcribe_sync, ogg, language, initial_prompt
    )


# Static lead text — "sets the scene" so Whisper biases toward chat-style
# Russian instead of defaulting to cleaner dictation. The dynamic keyword
# list is appended per-call inside `_build_whisper_prompt`.
_WHISPER_PROMPT_LEAD = (
    "Разговор в Telegram-чате про Сбер-кабинеты, POA, обмен RUB на USDT."
)


def _build_whisper_prompt(keywords: list[str]) -> str | None:
    """Craft a compact initial_prompt for faster-whisper that lists
    project-specific vocabulary (bot nicknames, role words, slang).

    Whisper's initial_prompt is treated as prior context the model has
    "already seen" before transcription starts, so listing words here
    sharply improves recall on those exact tokens. Cap at 224 tokens
    per faster-whisper docs — our list is tiny, well under limit.
    """
    if not keywords:
        return None
    # Deduplicate preserving order; lowercase for consistency.
    seen: set[str] = set()
    ordered: list[str] = []
    for k in keywords:
        lo = k.strip().lower()
        if lo and lo not in seen:
            seen.add(lo)
            ordered.append(lo)
    vocab = ", ".join(ordered)
    return f"{_WHISPER_PROMPT_LEAD} Позывные и роли: {vocab}."


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

            # Pull active keywords and bake them into a Whisper prompt so
            # the model doesn't mis-hear "бот" as "бод" / "вот". Failure
            # to build the prompt (e.g. DB transient error) falls back to
            # unbiased transcription — not a blocker.
            prompt: str | None = None
            try:
                from src.core.keyword_match import get_active_keywords

                kws = await get_active_keywords()
                prompt = _build_whisper_prompt(kws)
            except Exception:
                log.exception("whisper_prompt_build_failed")

            text = await transcribe_bytes(
                bytes(row.ogg_data), initial_prompt=prompt
            )
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


MENTION_LINK_WINDOW_SEC = 5


async def find_recent_voice_by_user(
    session: AsyncSession,
    *,
    chat_id: int,
    tg_user_id: int,
    within_seconds: int = MENTION_LINK_WINDOW_SEC,
) -> VoiceMessage | None:
    """Most recent voice from this user in this chat, within a short window.
    Used when the user @-mentions the bot right after a voice note — we
    treat it as "this voice is addressed to the bot" only if the
    mention comes fast enough (default 5 s). Otherwise the voice is
    considered a side-chat between humans and the bot stays out.
    """
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
    res = await session.execute(
        select(VoiceMessage)
        .where(
            VoiceMessage.chat_id == chat_id,
            VoiceMessage.tg_user_id == tg_user_id,
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
