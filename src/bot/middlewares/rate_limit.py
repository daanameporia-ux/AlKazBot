"""Rate limit: 20 messages/min per (user, chat).

Spec § "Оптимизации/Надёжность". Two guarantees:
  1. A single user can't spam the chat (DoS).
  2. A single user active in multiple chats can't multiply their cap
     by fanning out — the key is (user, chat), so each chat gets its
     own budget.

Sliding window in a per-(uid,chat) deque. Checked in an outer middleware
BEFORE whitelist — even whitelisted users are capped.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.logging_setup import get_logger

log = get_logger(__name__)

LIMIT = 20
WINDOW_SEC = 60


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._hits: dict[tuple[int, int], deque[float]] = {}
        self._notified: dict[tuple[int, int], float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        msg: Message | None = (
            event if isinstance(event, Message) else getattr(event, "message", None)
        )
        if msg is None or msg.from_user is None or msg.from_user.is_bot:
            return await handler(event, data)

        key = (msg.from_user.id, msg.chat.id)
        now = time.time()
        q = self._hits.setdefault(key, deque())
        while q and (now - q[0]) > WINDOW_SEC:
            q.popleft()
        if len(q) >= LIMIT:
            # Warn once per minute per (user, chat) so we don't spam.
            last = self._notified.get(key, 0)
            if now - last > WINDOW_SEC:
                self._notified[key] = now
                log.info(
                    "rate_limited",
                    user_id=msg.from_user.id,
                    chat_id=msg.chat.id,
                    window_count=len(q),
                )
                import contextlib

                with contextlib.suppress(Exception):
                    await msg.reply(
                        "Слишком много сообщений подряд — притормози на минуту, "
                        "я не справляюсь."
                    )
            return None
        q.append(now)
        return await handler(event, data)
