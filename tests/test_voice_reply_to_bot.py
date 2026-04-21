"""Voice as reply-to-bot triggers the analyzer even without keyword."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.bot.handlers.voice import _is_reply_to_bot


async def test_classic_reply_to_bot_detected() -> None:
    msg = MagicMock()
    msg.bot = MagicMock()
    me = MagicMock()
    me.id = 8634741067
    msg.bot.me = AsyncMock(return_value=me)
    msg.reply_to_message = MagicMock()
    msg.reply_to_message.from_user = MagicMock()
    msg.reply_to_message.from_user.id = 8634741067
    msg.external_reply = None
    assert await _is_reply_to_bot(msg) is True


async def test_reply_to_other_user_not_detected() -> None:
    msg = MagicMock()
    msg.bot = MagicMock()
    me = MagicMock()
    me.id = 8634741067
    msg.bot.me = AsyncMock(return_value=me)
    msg.reply_to_message = MagicMock()
    msg.reply_to_message.from_user = MagicMock()
    msg.reply_to_message.from_user.id = 7220305943  # Арбуз, not bot
    msg.external_reply = None
    assert await _is_reply_to_bot(msg) is False


async def test_no_reply_not_detected() -> None:
    msg = MagicMock()
    msg.bot = MagicMock()
    me = MagicMock()
    me.id = 8634741067
    msg.bot.me = AsyncMock(return_value=me)
    msg.reply_to_message = None
    msg.external_reply = None
    assert await _is_reply_to_bot(msg) is False


async def test_external_reply_to_bot_detected() -> None:
    """Bot-API-7 external_reply path — some Telegram clients send this
    instead of classic reply_to_message."""
    msg = MagicMock()
    msg.bot = MagicMock()
    me = MagicMock()
    me.id = 8634741067
    msg.bot.me = AsyncMock(return_value=me)
    msg.reply_to_message = None
    ext = MagicMock()
    ext.origin = MagicMock()
    ext.origin.sender_user = MagicMock()
    ext.origin.sender_user.id = 8634741067
    msg.external_reply = ext
    assert await _is_reply_to_bot(msg) is True
