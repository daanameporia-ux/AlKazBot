"""Fast local keyword matcher.

Pulls active `trigger_keywords` rows from the DB once, caches them in
memory with a short TTL, and does case-insensitive substring matching
against each incoming text. Substring matcher intentionally; so "бот"
catches "ботяра", "Арбузбот", etc.

This module does NOT call any LLM / external API. It's the cheap gate
in front of the batch analyzer — only messages that pass the gate are
allowed to burn Anthropic tokens.

Cache reload: every `CACHE_TTL_SEC` or when `invalidate()` is called
(the /keywords command does this after add/remove).
"""

from __future__ import annotations

import asyncio
import time

from src.db.repositories import keywords as keyword_repo
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)

CACHE_TTL_SEC = 60

_cache: list[str] = []
_cache_loaded_at: float = 0.0
_cache_lock = asyncio.Lock()


async def _load_cache() -> list[str]:
    async with session_scope() as session:
        rows = await keyword_repo.list_active(session)
    return [r.keyword for r in rows]


async def _ensure_cache() -> list[str]:
    global _cache, _cache_loaded_at
    now = time.time()
    if _cache_loaded_at and (now - _cache_loaded_at) < CACHE_TTL_SEC:
        return _cache
    async with _cache_lock:
        # Re-check after acquiring lock (double-check pattern).
        now = time.time()
        if _cache_loaded_at and (now - _cache_loaded_at) < CACHE_TTL_SEC:
            return _cache
        _cache = await _load_cache()
        _cache_loaded_at = now
        log.info("keyword_cache_refreshed", count=len(_cache))
    return _cache


async def invalidate() -> None:
    """Called by the /keywords command after add/remove so the next
    match picks up the change immediately instead of waiting for TTL."""
    global _cache_loaded_at
    async with _cache_lock:
        _cache_loaded_at = 0.0


async def find_hits(text: str) -> list[str]:
    """Return the list of keywords found in `text`. Empty list = no match."""
    if not text:
        return []
    keywords = await _ensure_cache()
    if not keywords:
        return []
    lowered = text.lower()
    return [k for k in keywords if k in lowered]


async def has_trigger(text: str) -> bool:
    """Convenience wrapper — True if at least one keyword hit."""
    return len(await find_hits(text)) > 0


async def get_active_keywords() -> list[str]:
    """Public accessor for the cached active-keyword list.

    Used by voice transcription to build a Whisper `initial_prompt`
    so the model is biased toward recognising our trigger words
    correctly (otherwise it mishears short Russian words like
    "бот" → "бод" / "вот").
    """
    return list(await _ensure_cache())
