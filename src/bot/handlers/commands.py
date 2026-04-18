"""Slash commands.

Stage 0 shipped /start, /help, /chatid. Stage 1 adds:
  /knowledge                — list KB
  /knowledge add <text>     — append a fact (confirmed)
  /knowledge forget <id>    — soft-delete
  /knowledge edit <id> <text>
  /knowledge search <q>
  /feedback                 — show accumulated wishes
  /partners                 — current partner shares
  /balance  /fx  /stock  /report  /history — stubs wired into Stage 1 repos

The unimplemented ones reply with an explicit "coming in Stage 1/2" note
rather than a 404-style silence so the team knows work is tracked.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from src.db.repositories import knowledge as kb_repo
from src.db.repositories import users as user_repo
from src.db.session import session_scope
from src.logging_setup import get_logger
from src.personality.phrases import HELP_TEXT
from src.personality.voice import GREETING_FIRST_RUN

log = get_logger(__name__)
router = Router(name="commands")


# --------------------------------------------------------------------------- #
# Core onboarding / help
# --------------------------------------------------------------------------- #


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Upsert the user on first /start and say hi."""
    if message.from_user is None:
        return
    async with session_scope() as session:
        await user_repo.upsert_user(
            session,
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
            display_name=message.from_user.full_name,
        )
    await message.answer(GREETING_FIRST_RUN)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    await message.answer(
        f"chat_id = `{message.chat.id}`\n"
        f"your user_id = `{message.from_user.id if message.from_user else '?'}`",
        parse_mode="Markdown",
    )


# --------------------------------------------------------------------------- #
# /knowledge — explicit KB management
# --------------------------------------------------------------------------- #


KB_CATEGORY_ORDER = ("alias", "glossary", "entity", "rule", "pattern", "preference")


def _format_kb_list(facts) -> str:
    if not facts:
        return "База пустая. Учи меня: `@бот запомни: <факт>` или `/knowledge add <факт>`."
    by_cat: dict[str, list] = {}
    for f in facts:
        by_cat.setdefault(f.category, []).append(f)
    parts = ["*Что я знаю:*"]
    for cat in KB_CATEGORY_ORDER:
        if cat not in by_cat:
            continue
        parts.append(f"\n_{cat}_")
        for f in by_cat[cat]:
            tag = "" if f.confidence == "confirmed" else f" _({f.confidence})_"
            key = f"*{f.key}*: " if f.key else ""
            parts.append(f"  `#{f.id}` {key}{f.content}{tag}")
    return "\n".join(parts)


@router.message(Command("knowledge"))
async def cmd_knowledge(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        async with session_scope() as session:
            facts = await kb_repo.list_facts(session, min_confidence="tentative")
        await message.answer(_format_kb_list(facts), parse_mode="Markdown")
        return

    # Sub-commands: add / forget / edit / search
    subcmd, _, rest = args.partition(" ")
    subcmd = subcmd.lower()

    if subcmd == "add":
        text = rest.strip()
        if not text:
            await message.reply("Чё добавить? `/knowledge add <факт>`", parse_mode="Markdown")
            return
        async with session_scope() as session:
            me = (
                await user_repo.get_user_by_tg_id(session, message.from_user.id)
                if message.from_user
                else None
            )
            fact = await kb_repo.add_fact(
                session,
                category="rule",  # default; LLM-driven classification comes later
                content=text,
                confidence="confirmed",
                created_by_user_id=me.id if me else None,
            )
        await message.reply(
            f"Записал `#{fact.id}` (`{fact.category}`): {fact.content}",
            parse_mode="Markdown",
        )
        return

    if subcmd == "forget":
        try:
            fact_id = int(rest.strip())
        except ValueError:
            await message.reply("ID надо числом. `/knowledge forget 42`")
            return
        async with session_scope() as session:
            ok = await kb_repo.deactivate(session, fact_id)
        await message.reply(
            f"Забыл `#{fact_id}`." if ok else f"Нет такого `#{fact_id}` (или уже забыт).",
            parse_mode="Markdown",
        )
        return

    if subcmd == "edit":
        head, _, new_text = rest.partition(" ")
        try:
            fact_id = int(head.strip())
        except ValueError:
            await message.reply("`/knowledge edit <id> <новый текст>`", parse_mode="Markdown")
            return
        new_text = new_text.strip()
        if not new_text:
            await message.reply("Пустой текст, нечего сохранять.")
            return
        async with session_scope() as session:
            ok = await kb_repo.edit_content(session, fact_id, new_text)
        await message.reply(
            f"Поправил `#{fact_id}`." if ok else f"Нет такого `#{fact_id}`.",
            parse_mode="Markdown",
        )
        return

    if subcmd == "search":
        q = rest.strip()
        if not q:
            await message.reply("Что искать? `/knowledge search <запрос>`", parse_mode="Markdown")
            return
        async with session_scope() as session:
            facts = await kb_repo.search(session, q)
        await message.answer(_format_kb_list(facts), parse_mode="Markdown")
        return

    await message.reply(
        "Не понял. Варианты: `/knowledge` (list), `/knowledge add|forget|edit|search ...`",
        parse_mode="Markdown",
    )


# --------------------------------------------------------------------------- #
# Stage 2 stubs — still not implemented, explicit "coming later".
# --------------------------------------------------------------------------- #

STAGE2_COMMANDS = ("stock", "clients", "client", "debts", "history", "undo", "silent")


@router.message(Command(*STAGE2_COMMANDS))
async def cmd_stage2_stub(message: Message) -> None:
    cmd = (message.text or "").split()[0] if message.text else "?"
    await message.reply(
        f"{cmd} — эта штука в Этапе 2 (склад / клиенты / доверки). "
        f"Пока не умею. /help — что уже умею."
    )


__all__ = ["router"]
