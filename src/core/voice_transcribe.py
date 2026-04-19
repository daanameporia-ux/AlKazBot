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
from src.db.session import session_scope
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


# Hard deadline for a single whisper pass. A ~30-sec clip on CPU int8
# completes in 10-25 sec; anything over this is either a stuck thread or a
# pathological audio file, and we'd rather give up than freeze polling.
TRANSCRIBE_TIMEOUT_SEC = 180


async def transcribe_bytes(
    ogg: bytes,
    *,
    language: str = "ru",
    initial_prompt: str | None = None,
) -> str:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_transcribe_sync, ogg, language, initial_prompt),
            timeout=TRANSCRIBE_TIMEOUT_SEC,
        )
    except TimeoutError:
        log.warning("whisper_timeout", bytes=len(ogg))
        return ""


# Static lead text — "sets the scene" so Whisper biases toward chat-style
# Russian instead of defaulting to cleaner dictation. The dynamic keyword
# list + KB entity names is appended per-call inside `_build_whisper_prompt`.
_WHISPER_PROMPT_LEAD = (
    "Разговор в Telegram-чате команды процессинга. "
    "Темы: Сбер-кабинеты, POA (доверенности), обмен RUB на USDT, "
    "эквайринг, нотариалка, кабинет в работе, додеп, откуп."
)

# Core business vocabulary Whisper should recognise. These bias the model
# toward the right tokens for short/noisy Russian speech. Cyrillic only —
# mixing ASCII into initial_prompt encourages Whisper to romanise output.
_CORE_VOCAB: tuple[str, ...] = (
    # Partners
    "казах", "арбуз",
    # Clients / suppliers we see often
    "никонов", "миша", "сельвян",
    # Wallets / platforms
    "тапбанк", "меркурио", "рапира", "сбер", "сбербанк",
    # Business slang
    "кабинет", "кабинеты", "нотариалка", "эквайринг", "додеп", "откуп",
    "пятерик", "десятка", "нал", "наличка", "рапа", "контора",
    # Entity aliases
    "арнелле", "tpay", "merk",
    # POA / exchange terminology
    "доверенность", "снятие", "обмен", "комиссия", "курс",
    # Wakewords for the bot (cyrillic only — latin variants stay in DB)
    "алкаш", "алказ", "алказбот", "ержан", "бот", "бухгалтер", "пёс",
)


def _is_cyrillic_word(s: str) -> bool:
    """True if the string contains at least one Cyrillic letter and no
    ASCII Latin letters. Used to strip ASCII keywords from the Whisper
    hint — otherwise the model biases toward Latin output ("Алкаш" →
    "Alkaz") because ASCII tokens in the prompt encourage romanization.
    """
    has_cyr = any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in s)
    has_lat = any("a" <= ch.lower() <= "z" for ch in s)
    return has_cyr and not has_lat


async def _collect_entity_vocab() -> list[str]:
    """Pull KB entity keys (client/supplier names) so Whisper has them in
    the hint. Cached by the caller (knowledge_base changes rarely)."""
    try:
        from src.db.repositories import knowledge as kb_repo

        async with session_scope() as session:
            facts = await kb_repo.list_facts(session, min_confidence="inferred")
    except Exception:
        return []
    out: list[str] = []
    for f in facts:
        if f.category in ("entity", "alias") and f.key:
            out.append(f.key)
    return out


def _build_whisper_prompt(
    keywords: list[str], entities: list[str] | None = None
) -> str | None:
    """Craft a compact initial_prompt for faster-whisper that lists
    project-specific vocabulary (bot nicknames, partners, clients,
    slang). Whisper's initial_prompt is treated as prior context the
    model has "already seen", so listing words here sharply improves
    recall on short/noisy Russian speech.

    Cyrillic-only filter — ASCII tokens in the prompt (e.g.
    "al_kazbot") encourage Whisper to transliterate ("Алкаш" →
    "Alkaz"), defeating substring matching against our Cyrillic
    trigger_keywords. Latin variants stay in the DB as a safety net.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(word: str) -> None:
        lo = word.strip().lower()
        if not lo or lo in seen:
            return
        if not _is_cyrillic_word(lo):
            return
        seen.add(lo)
        ordered.append(lo)

    # Keywords from the DB get priority (user-managed set).
    for k in keywords or []:
        _add(k)
    # Then KB entity names (clients, suppliers).
    for e in entities or []:
        _add(e)
    # Then the static core vocab — last, so if we're near the 224-token
    # Whisper prompt cap, DB-sourced words win.
    for c in _CORE_VOCAB:
        _add(c)

    if not ordered:
        return _WHISPER_PROMPT_LEAD  # still bias toward chat-style Russian
    vocab = ", ".join(ordered)
    return f"{_WHISPER_PROMPT_LEAD} Лексика: {vocab}."


# Common Whisper Russian misfires on short/noisy speech. Map is intentionally
# small — only the patterns we've seen more than once in prod logs, with
# enough context that we don't misfire on innocent text. Applied after
# transcription, before passing to keyword_match / analyzer.
_POST_FIXES: tuple[tuple[str, str], ...] = (
    # "Вержан" / "верджан" (no initial "e") → Ержан (bot wake-word).
    (r"\bвержан\b", "ержан"),
    (r"\bверджан\b", "ержан"),
    # "Alkaz" / "Alkash" in a Cyrillic stream — drop back to Cyrillic.
    (r"\balkaz\b", "алказ"),
    (r"\balkash\b", "алкаш"),
    (r"\berzhan\b", "ержан"),
    # "нахуят" — almost always "нахуя ты"
    (r"\bнахуят\b", "нахуя ты"),
    # "Als" / "Алз" truncations of "Алкаш"
    (r"\bалз\b", "алкаш"),
)


def _postprocess_transcript(text: str) -> str:
    """Apply small regex fixes for common Whisper Russian mishears.

    Idempotent — running twice yields the same result. Applied case-
    insensitively to the lowercased copy, but preserves casing best-effort
    by only touching the lowercase comparison (keyword_match is case-
    insensitive anyway, so we lowercase the output here).
    """
    import re

    if not text:
        return text
    out = text
    for pat, rep in _POST_FIXES:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return out


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

            # Pull active keywords + KB entity names and bake them into a
            # Whisper prompt so the model recognises our jargon (bot
            # nicknames, partner/client names, business slang). Failure
            # to build the prompt (e.g. DB transient error) falls back to
            # unbiased transcription — not a blocker.
            prompt: str | None = None
            try:
                from src.core.keyword_match import get_active_keywords

                kws = await get_active_keywords()
                entities = await _collect_entity_vocab()
                prompt = _build_whisper_prompt(kws, entities)
            except Exception:
                log.exception("whisper_prompt_build_failed")

            raw_text = await transcribe_bytes(
                bytes(row.ogg_data), initial_prompt=prompt
            )
            text = _postprocess_transcript(raw_text)
            if not text:
                text = "(тишина)"

            row.transcribed_text = text
            row.transcribed_at = datetime.now(UTC)
            # Keep OGG bytes for retranscription after model upgrades — the
            # periodic wipe worker (see reminders.py) clears them after 14
            # days so Postgres doesn't balloon. Immediate-zero was causing
            # lost re-listen capability.
            # row.ogg_data stays as-is intentionally.

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
