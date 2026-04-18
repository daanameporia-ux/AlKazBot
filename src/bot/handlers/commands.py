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

from src.db.repositories import balances as balances_repo
from src.db.repositories import feedback as feedback_repo
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
# /balance — wallet snapshot (read-only)
# --------------------------------------------------------------------------- #


def _fmt_money(amount, currency: str) -> str:
    if amount is None:
        return "—"
    return f"{amount:,.2f} {currency}".replace(",", " ")


@router.message(Command("balance"))
async def cmd_balance(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().lower()
    async with session_scope() as session:
        items = await balances_repo.latest_wallet_balances(session)

    if arg:
        items = [w for w in items if w.wallet_code == arg or arg in w.wallet_name.lower()]
        if not items:
            await message.reply(f"Кошелька `{arg}` не нашёл.", parse_mode="Markdown")
            return

    if not any(w.amount_usdt is not None for w in items):
        await message.answer(
            "Снапшотов балансов ещё не было — запусти `/report`, там спрошу цифры.",
            parse_mode="Markdown",
        )
        return

    lines = ["*Балансы (последний снапшот):*"]
    total = 0
    for w in items:
        native = _fmt_money(w.amount_native, w.currency) if w.amount_native is not None else "—"
        usdt = _fmt_money(w.amount_usdt, "USDT") if w.amount_usdt is not None else "—"
        lines.append(f"  `{w.wallet_code:<14}` {w.wallet_name:<22} {native}  →  {usdt}")
        if w.amount_usdt is not None:
            total += float(w.amount_usdt)
    lines.append(f"\n*Итого*: `{total:,.2f}` USDT".replace(",", " "))
    await message.answer("\n".join(lines), parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# /fx — current RUB/USDT rate
# --------------------------------------------------------------------------- #


@router.message(Command("fx"))
async def cmd_fx(message: Message) -> None:
    async with session_scope() as session:
        snap = await balances_repo.current_fx_rate(session)
    if snap is None:
        await message.answer(
            "Курса ещё нет. Кинь в чат строку вида `517000/6433=80.37` — я запишу.",
            parse_mode="Markdown",
        )
        return
    await message.answer(
        f"*Курс*: `{snap.rate:.4f}` ₽/USDT\n"
        f"_обновлён_: `{snap.rate_date.strftime('%Y-%m-%d %H:%M UTC')}`",
        parse_mode="Markdown",
    )


# --------------------------------------------------------------------------- #
# /partners — per-partner running totals
# --------------------------------------------------------------------------- #


@router.message(Command("partners"))
async def cmd_partners(message: Message) -> None:
    async with session_scope() as session:
        shares = await balances_repo.partner_shares(session)
    if not shares:
        await message.answer("Партнёров в базе нет (неожиданно — seed должен был их создать).")
        return
    lines = ["*Партнёры — итого:*"]
    for s in shares:
        lines.append(
            f"\n*{s.partner_name}*\n"
            f"  depo:        `{s.deposits_usdt:>10,.2f}` USDT\n".replace(",", " ")
            + f"  +snятия:    `{s.contributions_usdt:>10,.2f}` USDT\n".replace(",", " ")
            + f"  −вывод:     `{s.withdrawals_usdt:>10,.2f}` USDT\n".replace(",", " ")
            + f"  *net*:       *`{s.net_usdt:>10,.2f}`* USDT".replace(",", " ")
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# /feedback — accumulated wishes
# --------------------------------------------------------------------------- #


@router.message(Command("feedback"))
async def cmd_feedback(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    # /feedback add <text> — explicit add (in addition to passive listening)
    if args.startswith("add "):
        text = args[4:].strip()
        if len(text) < 3:
            await message.reply("Слишком коротко. Пиши развёрнуто.")
            return
        async with session_scope() as session:
            me = (
                await user_repo.get_user_by_tg_id(session, message.from_user.id)
                if message.from_user
                else None
            )
            fb = await feedback_repo.add(
                session,
                message=text,
                created_by_user_id=me.id if me else None,
            )
        await message.reply(f"Записал `#{fb.id}`. Разберёмся потом.", parse_mode="Markdown")
        return

    async with session_scope() as session:
        items = await feedback_repo.list_open(session)
    if not items:
        await message.answer("Пожеланий пока нет. Добавь: `/feedback add <текст>`.", parse_mode="Markdown")
        return
    lines = ["*Пожелания команды:*"]
    for fb in items:
        lines.append(f"`#{fb.id}` [{fb.status}] {fb.message[:180]}")
    await message.answer("\n".join(lines), parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# Stage 2 stubs — still not implemented, explicit "coming later".
# --------------------------------------------------------------------------- #

STAGE2_COMMANDS = ("stock", "clients", "client", "debts", "history", "undo", "silent", "report")


@router.message(Command(*STAGE2_COMMANDS))
async def cmd_stage2_stub(message: Message) -> None:
    cmd = (message.text or "").split()[0] if message.text else "?"
    await message.reply(
        f"{cmd} — пока не готово. Появится в ближайших коммитах Этапа 1-2. "
        f"/help — что уже умею."
    )


__all__ = ["router"]
