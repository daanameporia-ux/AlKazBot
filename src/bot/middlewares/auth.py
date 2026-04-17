"""Whitelist middleware — drops updates from users not in ALLOWED_TG_USER_IDS.

Group chats: allowed if the chat_id matches MAIN_CHAT_ID OR the sender is
whitelisted. Private chats: allowed only if the sender is whitelisted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        msg: Message | None = getattr(event, "message", None) or (
            event if isinstance(event, Message) else None
        )
        if msg is None or msg.from_user is None:
            return await handler(event, data)

        user_id = msg.from_user.id
        chat_id = msg.chat.id
        allowed_user = user_id in settings.allowed_tg_user_ids
        main_group = settings.main_chat_id and chat_id == settings.main_chat_id

        if allowed_user or main_group:
            data["is_whitelisted_user"] = allowed_user
            data["is_main_group"] = bool(main_group)
            return await handler(event, data)

        log.info(
            "rejected_unauthorized",
            user_id=user_id,
            chat_id=chat_id,
            username=msg.from_user.username,
        )
        # Silent drop — don't reveal the bot's presence to strangers.
        return None
