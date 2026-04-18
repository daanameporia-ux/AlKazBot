"""In-memory batch buffer for non-mention group messages.

Every message from a whitelisted user in the main group is accumulated and
flushed to the LLM batch analyzer when any of these is true:

  * `MAX_BATCH_SIZE` messages piled up (default 8)
  * `MAX_BATCH_AGE_SEC` seconds elapsed since the oldest buffered message
    (default 180 — three minutes of silence)
  * A direct trigger arrived: an @-mention, a reply to the bot, or a
    slash-command. The trigger itself gets prepended to the flush so the
    analyzer sees the fresh context.

Batch flush runs `analyze_batch` from `src.llm.batch_analyzer`, which asks
Claude to decompose the whole batch into a list of operation candidates.
Each candidate then goes through its own preview-and-confirm flow.

The buffer is intentionally in-memory + per-chat. If the container restarts
mid-batch we rely on `message_log` in Postgres to catch up — a separate
"resync" worker will scan unparsed messages on boot (future work).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)

MAX_BATCH_SIZE = 8
MAX_BATCH_AGE_SEC = 180

FlushHandler = Callable[["Batch"], Awaitable[Any]]


@dataclass(slots=True)
class BufferedMessage:
    tg_message_id: int
    tg_user_id: int
    display_name: str | None
    text: str
    received_at: float


@dataclass(slots=True)
class Batch:
    chat_id: int
    messages: list[BufferedMessage] = field(default_factory=list)
    trigger: BufferedMessage | None = None
    trigger_kind: str | None = None  # "mention" | "reply" | "command" | "size" | "age"


class BatchBuffer:
    """Per-chat accumulator. Use `instance.add(...)` and `instance.flush_now(...)`."""

    def __init__(self, flush_handler: FlushHandler) -> None:
        self._flush_handler = flush_handler
        self._buffers: dict[int, list[BufferedMessage]] = {}
        self._timers: dict[int, asyncio.Task[None]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, chat_id: int) -> asyncio.Lock:
        lk = self._locks.get(chat_id)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[chat_id] = lk
        return lk

    async def add(self, chat_id: int, msg: BufferedMessage) -> None:
        """Append a passive (non-trigger) message to the chat's buffer."""
        async with self._lock(chat_id):
            buf = self._buffers.setdefault(chat_id, [])
            buf.append(msg)
            log.debug("batcher_add", chat_id=chat_id, size=len(buf))
            if len(buf) >= MAX_BATCH_SIZE:
                await self._flush_locked(chat_id, trigger=None, trigger_kind="size")
            else:
                self._reset_timer(chat_id)

    async def flush_now(
        self,
        chat_id: int,
        *,
        trigger: BufferedMessage | None,
        trigger_kind: str,
    ) -> None:
        """Drain the buffer and invoke the flush handler immediately.

        Call this from mention / reply / command handlers BEFORE answering,
        so the analyzer sees the context of what was said in chat just before.
        """
        async with self._lock(chat_id):
            await self._flush_locked(chat_id, trigger=trigger, trigger_kind=trigger_kind)

    async def _flush_locked(
        self,
        chat_id: int,
        *,
        trigger: BufferedMessage | None,
        trigger_kind: str | None,
    ) -> None:
        buf = self._buffers.get(chat_id) or []
        t = self._timers.pop(chat_id, None)
        if t is not None and not t.done():
            t.cancel()
        if not buf and trigger is None:
            return
        self._buffers[chat_id] = []
        batch = Batch(
            chat_id=chat_id,
            messages=list(buf),
            trigger=trigger,
            trigger_kind=trigger_kind,
        )
        log.info(
            "batch_flush",
            chat_id=chat_id,
            size=len(batch.messages),
            trigger_kind=trigger_kind,
        )
        # Fire-and-forget so the bot can keep receiving updates.
        asyncio.create_task(self._safe_flush(batch), name=f"batch-flush-{chat_id}")

    async def _safe_flush(self, batch: Batch) -> None:
        try:
            await self._flush_handler(batch)
        except Exception:
            log.exception("batch_flush_handler_failed", chat_id=batch.chat_id)

    def _reset_timer(self, chat_id: int) -> None:
        old = self._timers.pop(chat_id, None)
        if old is not None and not old.done():
            old.cancel()
        self._timers[chat_id] = asyncio.create_task(
            self._age_flush(chat_id),
            name=f"batch-timer-{chat_id}",
        )

    async def _age_flush(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(MAX_BATCH_AGE_SEC)
        except asyncio.CancelledError:
            return
        async with self._lock(chat_id):
            await self._flush_locked(chat_id, trigger=None, trigger_kind="age")


# --------------------------------------------------------------------------- #
# Singleton — wired into the dispatcher at startup in src.bot.main
# --------------------------------------------------------------------------- #

_singleton: BatchBuffer | None = None


def get_batch_buffer(flush_handler: FlushHandler | None = None) -> BatchBuffer:
    global _singleton
    if _singleton is None:
        if flush_handler is None:
            raise RuntimeError("BatchBuffer not initialized yet; pass flush_handler")
        _singleton = BatchBuffer(flush_handler)
    return _singleton


def is_main_group(chat_id: int) -> bool:
    """`MAIN_CHAT_ID=0` means "group not configured yet" → never auto-batch."""
    return bool(settings.main_chat_id) and chat_id == settings.main_chat_id


def now_ts() -> float:
    return time.time()
