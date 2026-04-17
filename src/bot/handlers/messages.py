"""Catch-all message handler. Stage 0 just logs + no-op (real classification is
in Stage 1). The message-logging middleware already persists every message.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from src.llm.classifier import quick_classify
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="messages")


@router.message()
async def on_message(message: Message) -> None:
    text = message.text or message.caption or ""
    if not text:
        return
    # Regex pre-router — cheap, no LLM call. On Stage 1 the matched intent
    # will actually trigger a parser; for now we just log it.
    intent = quick_classify(text)
    if intent is not None:
        log.info(
            "message_quick_intent",
            intent=intent.value,
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
        )
