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


async def _is_mention_of_me(message: Message) -> bool:
    me = await message.bot.me()
    text = message.text or message.caption or ""
    if not text:
        return False
    return f"@{me.username}".lower() in text.lower()


def _strip_mention(text: str, bot_username: str) -> str:
    return re.sub(rf"(?i)@{re.escape(bot_username)}", "", text).strip()


@router.message()
async def on_mention(message: Message) -> None:
    if not await _is_mention_of_me(message):
        return

    me = await message.bot.me()
    raw = message.text or message.caption or ""
    body = _strip_mention(raw, me.username or "")

    log.info(
        "mention_received",
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
