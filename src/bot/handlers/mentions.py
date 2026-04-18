"""Handlers for messages that @-mention the bot.

Stage 1: route @-mentions through the LLM pipeline. The pipeline classifies
intent and produces a text reply for free-form questions. Structured-operation
intents are acknowledged as stubs until parsers ship in the next sub-commit.

Special case: "запомни: <факт>" / "запомни что <факт>" — bypass the classifier
and store immediately with confidence=confirmed. This matches Spec §"Обучаемость
→ Способ 1: явная команда".
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import BufferedMessage, get_batch_buffer, is_main_group, now_ts
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="mentions")




async def _addressed_to_me(message: Message) -> bool:
    """True when the user is talking to the bot — either by @-mention in text,
    by classic reply (`reply_to_message`) to one of the bot's messages, or by
    Bot-API-7.0+ external reply / quote pointing at the bot.

    A reply thread — in any shape Telegram encodes it — IS explicit address.
    """
    me = await message.bot.me()
    text = message.text or message.caption or ""
    text_lower = text.lower()
    mention = bool(text and f"@{me.username}".lower() in text_lower)

    rpy = message.reply_to_message
    reply_to_bot = bool(rpy and rpy.from_user and rpy.from_user.id == me.id)

    # Bot API 7.0 — external reply / quote (used when Telegram encodes
    # replies to out-of-history messages or "reply with quote" UX).
    ext = getattr(message, "external_reply", None)
    ext_sender = None
    if ext is not None:
        origin = getattr(ext, "origin", None)
        ext_sender = getattr(origin, "sender_user", None) if origin else None
    ext_reply_to_bot = bool(ext_sender and ext_sender.id == me.id)

    return mention or reply_to_bot or ext_reply_to_bot


def _strip_mention(text: str, bot_username: str) -> str:
    return re.sub(rf"(?i)@{re.escape(bot_username)}", "", text).strip()


# Filter on text/caption only — otherwise this catch-all handler
# short-circuits routing before voice/photo/document/sticker routers get a
# chance to fire.
@router.message(F.text | F.caption)
async def on_mention(message: Message) -> None:
    if not await _addressed_to_me(message):
        return

    me = await message.bot.me()
    raw = message.text or message.caption or ""
    body = _strip_mention(raw, me.username or "")

    classic_reply = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == me.id
    )
    ext = getattr(message, "external_reply", None)
    ext_reply = bool(
        ext
        and getattr(ext, "origin", None)
        and getattr(ext.origin, "sender_user", None)
        and ext.origin.sender_user.id == me.id
    )
    via = "reply" if classic_reply else ("external_reply" if ext_reply else "at_mention")

    log.info(
        "mention_received",
        via=via,
        text_preview=body[:120],
        user_id=message.from_user.id if message.from_user else None,
        chat_id=message.chat.id,
    )

    if not body:
        await message.reply("Чё?")
        return

    # Teaching commands now go through the batch analyzer like everything
    # else — Claude picks the right knowledge_base category (alias / entity
    # / rule / ...), pulls out a key when applicable, and emits a preview
    # card so the user can ✅ / ❌ just like for operations.
    #
    # No more naive regex shortcut dumping everything into 'rule'.

    # Flush the batch buffer with the trigger message so the analyzer sees
    # the @/reply + any buffered passive context together, and either
    # produces a list of operation cards or a chat_reply. Only the main
    # group uses the buffer; private chats / other groups still get a one-
    # shot reply path (future: extend batcher to per-chat).
    if is_main_group(message.chat.id) and message.from_user:
        trigger = BufferedMessage(
            tg_message_id=message.message_id,
            tg_user_id=message.from_user.id,
            display_name=message.from_user.full_name,
            text=body,
            received_at=now_ts(),
        )
        trigger_kind = "reply" if classic_reply or ext_reply else "mention"
        buf = get_batch_buffer()
        await buf.flush_now(message.chat.id, trigger=trigger, trigger_kind=trigger_kind)
        return

    # Non-main-group fallback: answer via the old pipeline.
    from src.llm.pipeline import process_message
    from src.personality.phrases import BOT_ERROR_FALLBACK

    try:
        result = await process_message(body)
    except Exception:
        log.exception("mention_pipeline_failed")
        await message.reply(BOT_ERROR_FALLBACK)
        return
    if result.reply_text:
        await message.reply(result.reply_text)
