"""Persists every incoming message to `message_log` for context / learning.

Also attaches a bound logger to the handler context.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy import select

from src.db.models import MessageLog
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


class MessageLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        msg: Message | None = (
            event if isinstance(event, Message) else getattr(event, "message", None)
        )
        if msg is not None and msg.from_user is not None:
            try:
                await self._persist(msg)
            except Exception:
                log.exception("message_log_persist_failed", tg_message_id=msg.message_id)

        return await handler(event, data)

    @staticmethod
    async def _persist(msg: Message) -> None:
        async with session_scope() as session:
            # Idempotency — spec §"Оптимизации/Надёжность": dedupe by tg_message_id.
            existing = await session.execute(
                select(MessageLog.id).where(
                    MessageLog.tg_message_id == msg.message_id,
                    MessageLog.chat_id == msg.chat.id,
                )
            )
            if existing.first() is not None:
                return
            entry = MessageLog(
                tg_message_id=msg.message_id,
                tg_user_id=msg.from_user.id if msg.from_user else None,
                chat_id=msg.chat.id,
                text=msg.text or msg.caption,
                has_media=bool(
                    msg.photo or msg.document or msg.video or msg.voice or msg.audio
                ),
                is_bot=bool(msg.from_user.is_bot) if msg.from_user else False,
                is_mention=False,  # set later in handler once we know
                reply_to_tg_message_id=(
                    msg.reply_to_message.message_id
                    if msg.reply_to_message is not None
                    else None
                ),
            )
            session.add(entry)


async def log_bot_reply(
    *,
    chat_id: int,
    tg_message_id: int | None,
    text: str,
    intent_hint: str | None = None,
) -> None:
    """Persist the bot's own outgoing messages so the analyzer sees them in
    recent history (otherwise the LLM thinks it never answered).

    Called from batch_processor / callbacks after bot.send_message /
    edit_text, with the resulting message_id.
    """
    async with session_scope() as session:
        existing = await session.execute(
            select(MessageLog.id).where(
                MessageLog.tg_message_id == tg_message_id,
                MessageLog.chat_id == chat_id,
            )
        )
        if existing.first() is not None:
            return
        entry = MessageLog(
            tg_message_id=tg_message_id,
            tg_user_id=None,
            chat_id=chat_id,
            text=text,
            has_media=False,
            is_bot=True,
            is_mention=False,
            intent_detected=intent_hint,
        )
        session.add(entry)
