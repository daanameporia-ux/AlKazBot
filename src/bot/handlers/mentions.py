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

from aiogram import Router
from aiogram.types import Message

from src.db.repositories import knowledge as kb_repo
from src.db.repositories import users as user_repo
from src.db.session import session_scope
from src.llm.pipeline import process_message
from src.logging_setup import get_logger
from src.personality.phrases import BOT_ERROR_FALLBACK

log = get_logger(__name__)
router = Router(name="mentions")


REMEMBER_RE = re.compile(
    r"(?ix)\bзапомни[\s,:]*(что)?\s*[:\-]?\s*(?P<body>.+)",
)


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


@router.message()
async def on_mention(message: Message) -> None:
    # Verbose diagnostic dump — Telegram has three reply-ish fields since
    # Bot API 7.0 (reply_to_message, external_reply, quote). We want to see
    # exactly which one (if any) is populated.
    ext = getattr(message, "external_reply", None)
    quote = getattr(message, "quote", None)
    print(
        f"[mentions] entered: chat={message.chat.id} "
        f"user={message.from_user.id if message.from_user else '?'} "
        f"has_reply={message.reply_to_message is not None} "
        f"reply_from_id={(message.reply_to_message.from_user.id if message.reply_to_message and message.reply_to_message.from_user else None)} "
        f"reply_from_is_bot={(message.reply_to_message.from_user.is_bot if message.reply_to_message and message.reply_to_message.from_user else None)} "
        f"has_ext_reply={ext is not None} "
        f"ext_origin_sender_is_bot={(getattr(ext.origin, 'sender_user', None).is_bot if ext and getattr(ext, 'origin', None) and getattr(ext.origin, 'sender_user', None) else None)} "
        f"has_quote={quote is not None} "
        f"thread_id={message.message_thread_id} "
        f"text={(message.text or '')[:60]!r}",
        flush=True,
    )
    if not await _addressed_to_me(message):
        print("[mentions] not addressed -> skip", flush=True)
        return

    me = await message.bot.me()
    raw = message.text or message.caption or ""
    body = _strip_mention(raw, me.username or "")
    is_reply = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == me.id
    )

    log.info(
        "mention_received",
        via="reply" if is_reply else "at_mention",
        text_preview=body[:120],
        user_id=message.from_user.id if message.from_user else None,
        chat_id=message.chat.id,
    )

    if not body:
        await message.reply("Чё?")
        return

    # Fast-path: explicit teach command.
    teach = REMEMBER_RE.match(body)
    if teach:
        fact = teach.group("body").strip().rstrip(".")
        if len(fact) < 3:
            await message.reply("Это не факт, это зевок. Перефразируй.")
            return
        async with session_scope() as session:
            me_user = (
                await user_repo.get_user_by_tg_id(session, message.from_user.id)
                if message.from_user
                else None
            )
            stored = await kb_repo.add_fact(
                session,
                category="rule",
                content=fact,
                confidence="confirmed",
                created_by_user_id=me_user.id if me_user else None,
            )
        await message.reply(
            f"Записал `#{stored.id}`: {stored.content}",
            parse_mode="Markdown",
        )
        return

    # General path — LLM pipeline.
    try:
        result = await process_message(body)
    except Exception:
        log.exception("mention_pipeline_failed")
        await message.reply(BOT_ERROR_FALLBACK)
        return

    if result.reply_text:
        await message.reply(result.reply_text)
