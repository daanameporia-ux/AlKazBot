"""Whitelist middleware — drops updates from users not in ALLOWED_TG_USER_IDS.

Policy: only whitelisted users (`allowed_tg_user_ids`) are allowed to
talk to the bot, period. MAIN_CHAT_ID lets us distinguish "the main team
chat" from "some other group the bot is in" for routing purposes, but it
is NOT a trust gate on its own.

Handlers still receive `is_main_group` in `data` so they can pick between
passive-batching flow (main) vs one-shot reply flow (private DM of a
whitelisted partner).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)


def _extract_user_chat(event: TelegramObject) -> tuple[int | None, int | None, str | None]:
    if isinstance(event, Message):
        if event.from_user is None:
            return (None, event.chat.id, None)
        return (event.from_user.id, event.chat.id, event.from_user.username)
    if isinstance(event, CallbackQuery):
        if event.from_user is None:
            return (None, None, None)
        chat_id = event.message.chat.id if event.message else None
        return (event.from_user.id, chat_id, event.from_user.username)
    # Unknown event type — let through (policy decision for forward-compat).
    return (None, None, None)


class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id, chat_id, username = _extract_user_chat(event)
        if user_id is None:
            # Event without a user (system event, service message, etc.).
            return await handler(event, data)

        allowed_user = user_id in settings.allowed_tg_user_ids
        main_group = bool(
            settings.main_chat_id and chat_id == settings.main_chat_id
        )

        if not allowed_user:
            log.info(
                "rejected_unauthorized",
                user_id=user_id,
                chat_id=chat_id,
                username=username,
                in_main_group=main_group,
            )
            # Silent drop — don't reveal the bot's presence to strangers,
            # even if they're in the main group chat.
            return None

        data["is_whitelisted_user"] = True
        data["is_main_group"] = main_group
        return await handler(event, data)
