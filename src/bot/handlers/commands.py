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
from src.db.repositories import cabinets as cabinet_repo
from src.db.repositories import clients as client_repo
from src.db.repositories import feedback as feedback_repo
from src.db.repositories import knowledge as kb_repo
from src.db.repositories import poa as poa_repo
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
# /stock — cabinets inventory
# --------------------------------------------------------------------------- #


@router.message(Command("stock"))
async def cmd_stock(message: Message) -> None:
    async with session_scope() as session:
        cabinets = await cabinet_repo.list_stock(session)
    if not cabinets:
        await message.answer("Склад пустой.")
        return
    by_status: dict[str, list] = {}
    total_usdt = 0
    for c in cabinets:
        by_status.setdefault(c.status, []).append(c)
        total_usdt += float(c.cost_usdt)
    lines = ["<b>Склад кабинетов:</b>"]
    status_title = {
        "in_stock": "в стоке",
        "in_use": "в работе",
        "blocked": "заблокированы",
    }
    for status in ("in_stock", "in_use", "blocked"):
        if status not in by_status:
            continue
        lines.append(f"\n<i>{status_title.get(status, status)}</i>:")
        for c in by_status[status]:
            name = c.name or c.auto_code
            lines.append(f"  • {name:<18} {c.cost_usdt:.2f}$")
    lines.append(f"\n<b>Итого</b>: {total_usdt:.2f}$")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# /clients and /client <name>
# --------------------------------------------------------------------------- #


@router.message(Command("clients"))
async def cmd_clients(message: Message) -> None:
    async with session_scope() as session:
        clients = await client_repo.list_all(session)
    if not clients:
        await message.answer("Клиентов пока нет.")
        return
    lines = ["<b>Клиенты доверенностей:</b>"]
    for c in clients:
        lines.append(f"  • {c.name}")
    await message.answer("\n".join(lines))


@router.message(Command("client"))
async def cmd_client(message: Message, command: CommandObject) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.reply("Формат: <code>/client Никонов</code>")
        return
    async with session_scope() as session:
        c = await client_repo.get_by_name(session, name)
        if c is None:
            await message.reply(f"Клиента «{name}» не нашёл.")
            return
        from sqlalchemy import select

        from src.db.models import PoAWithdrawal

        res = await session.execute(
            select(PoAWithdrawal)
            .where(PoAWithdrawal.client_id == c.id)
            .order_by(PoAWithdrawal.id.desc())
            .limit(20)
        )
        poas = list(res.scalars().all())
    lines = [f"<b>{c.name}</b>"]
    if not poas:
        lines.append("Операций ещё не было.")
    else:
        total_debt = 0
        for p in poas:
            debt = f" долг {p.client_debt_usdt:.2f}$" if p.client_debt_usdt and not p.client_paid else ""
            paid = " ✔" if p.client_paid else ""
            lines.append(
                f"  • {p.withdrawal_date} — {p.amount_rub:.0f}₽"
                f"{debt}{paid}"
            )
            if p.client_debt_usdt and not p.client_paid:
                total_debt += float(p.client_debt_usdt)
        if total_debt:
            lines.append(f"\n<b>Открытый долг</b>: {total_debt:.2f} USDT")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# /debts — all outstanding POA client debts
# --------------------------------------------------------------------------- #


@router.message(Command("debts"))
async def cmd_debts(message: Message) -> None:
    async with session_scope() as session:
        open_poas = await poa_repo.list_unpaid_client_debts(session)
        lines = ["<b>Долги клиентам:</b>"]
        total = 0.0
        if not open_poas:
            await message.answer("Долгов нет. Красавцы.")
            return
        for p in open_poas:
            # Lazy client name lookup
            from sqlalchemy import select

            from src.db.models import Client

            res = await session.execute(
                select(Client.name).where(Client.id == p.client_id)
            )
            name = res.scalar_one()
            debt = float(p.client_debt_usdt or 0)
            total += debt
            lines.append(f"  • {name:<20} {debt:.2f}$  (с {p.withdrawal_date})")
        lines.append(f"\n<b>Итого</b>: {total:.2f} USDT")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# /history [N] — last N audited operations
# --------------------------------------------------------------------------- #


@router.message(Command("history"))
async def cmd_history(message: Message, command: CommandObject) -> None:
    import contextlib

    limit = 10
    if command.args:
        with contextlib.suppress(ValueError):
            limit = max(1, min(50, int(command.args.strip())))
    async with session_scope() as session:
        from sqlalchemy import select

        from src.db.models import AuditLog

        res = await session.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        )
        rows = list(res.scalars().all())
    if not rows:
        await message.answer("Истории пока нет.")
        return
    lines = [f"<b>Последние {len(rows)} операций:</b>"]
    for a in rows:
        lines.append(
            f"  <code>#{a.id}</code>  {a.created_at.strftime('%m-%d %H:%M')}  "
            f"{a.action} {a.table_name}  rec#{a.record_id}"
        )
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# /undo <audit_id> — roll back a specific mutation (owner or creator)
# --------------------------------------------------------------------------- #


@router.message(Command("undo"))
async def cmd_undo(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    from src.config import settings as _s

    try:
        audit_id = int((command.args or "").strip())
    except (ValueError, TypeError):
        await message.reply("Формат: <code>/undo 42</code> (id из /history)")
        return
    is_owner = message.from_user.id == _s.owner_tg_user_id
    async with session_scope() as session:
        from sqlalchemy import delete, select

        from src.db.models import AuditLog

        res = await session.execute(select(AuditLog).where(AuditLog.id == audit_id))
        entry = res.scalar_one_or_none()
        if entry is None:
            await message.reply(f"Нет записи #{audit_id} в аудите.")
            return
        # Ownership check: caller is owner OR was the creator of the operation
        creator_user_id = entry.user_id
        me = await user_repo.get_user_by_tg_id(session, message.from_user.id)
        me_user_id = me.id if me else None
        if not is_owner and me_user_id != creator_user_id:
            await message.reply("Откатить чужую операцию может только owner.")
            return
        if entry.action != "create":
            await message.reply(
                f"Откат '{entry.action}' ещё не реализован. Пока умею только create."
            )
            return
        # Best-effort delete by table + record_id (hard delete; audit row
        # stays for trace).
        table = entry.table_name
        rid = entry.record_id
        from src.db.models import (
            Cabinet,
            Exchange,
            Expense,
            PartnerContribution,
            PartnerWithdrawal,
            PoAWithdrawal,
            Prepayment,
        )
        table_map = {
            "exchanges": Exchange,
            "expenses": Expense,
            "partner_contributions": PartnerContribution,
            "partner_withdrawals": PartnerWithdrawal,
            "poa_withdrawals": PoAWithdrawal,
            "cabinets": Cabinet,
            "prepayments": Prepayment,
        }
        model = table_map.get(table)
        if model is None or rid is None:
            await message.reply(f"Не знаю как откатить таблицу `{table}`.")
            return
        await session.execute(delete(model).where(model.id == rid))
    await message.reply(f"Откатил аудит #{audit_id} ({table} #{rid}).")


# --------------------------------------------------------------------------- #
# /silent and /report stubs — /report will be replaced in the next commit.
# --------------------------------------------------------------------------- #


@router.message(Command("silent"))
async def cmd_silent(message: Message) -> None:
    await message.reply("/silent — скоро будет в Этапе 3.")


# --------------------------------------------------------------------------- #
# /avatar — set the group chat photo (reply to a photo)
# --------------------------------------------------------------------------- #


@router.message(Command("avatar"))
async def cmd_avatar(message: Message) -> None:
    """Set the group's chat photo from the photo you reply this command to.

    Usage: send a photo, then reply to it with `/avatar`. Bot must be an
    admin with `can_change_info` — you already granted that.
    """
    import io

    from aiogram.types import BufferedInputFile

    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Эта команда только для группового чата.")
        return
    rpy = message.reply_to_message
    if rpy is None or not rpy.photo:
        await message.reply(
            "Ответь этой командой на фото, которое хочешь поставить аватаркой."
        )
        return

    largest = rpy.photo[-1]
    try:
        buf = io.BytesIO()
        await message.bot.download(largest, destination=buf)
    except Exception:
        await message.reply("Не смог скачать фото. Попробуй ещё раз.")
        return

    data = buf.getvalue()
    try:
        await message.bot.set_chat_photo(
            chat_id=message.chat.id,
            photo=BufferedInputFile(data, filename="chat_photo.jpg"),
        )
    except Exception as e:
        await message.reply(
            f"Не получилось сменить аватарку: {e}. Проверь что я админ "
            "с правом 'Change group info'."
        )
        return
    await message.reply("✅ Аватарка обновлена.")


# --------------------------------------------------------------------------- #
# /report — full end-of-day report
# --------------------------------------------------------------------------- #


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    from src.core.reports import acquiring_days_ago, generate

    if message.from_user is None:
        return
    async with session_scope() as session:
        me = await user_repo.get_user_by_tg_id(session, message.from_user.id)
        result = await generate(session, created_by_user_id=me.id if me else None)
        acq_ago = await acquiring_days_ago(session)

    footer = ""
    if acq_ago is None:
        footer = "\n\n<i>Эквайринга в базе не было. Если сегодня платили — кинь в чат 'эквайринг 5к' и подтверди.</i>"
    elif acq_ago >= 2:
        footer = (
            f"\n\n<i>Эквайринг был {acq_ago} дн. назад. Не забыли ли сегодня?</i>"
        )
    await message.answer(result.text + footer)


__all__ = ["router"]
