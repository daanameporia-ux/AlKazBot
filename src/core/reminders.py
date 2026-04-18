"""Background reminder worker.

Scheduled checks that nag the main group chat when something the team
usually tracks has slipped. Uses APScheduler (AsyncIOScheduler) bound to
the running event loop in `src.bot.main`.

Reminders (from sber26-bot-SPEC.md §"Автоматические напоминания"):

  1. Report overdue     — >26h since last /report and some operations
                          happened since.
  2. Acquiring missing  — >2d without an expense with category='acquiring'.
  3. Cabinet too long   — cabinet.in_use_since > 12h without a status
                          transition.
  4. POA without exchange — >6h since poa_withdrawal without amount_usdt.
  5. Client debt stale  — >24h after POA without client_paid=true.

Each reminder has its own cron-style cadence in `_JOBS`. Duplicates are
avoided via rows in `pending_reminders` (reminder_type + context hash).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select

from src.config import settings
from src.db.models import (
    AuditLog,
    Cabinet,
    Client,
    Expense,
    PendingReminder,
    PoAWithdrawal,
    Report,
)
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# De-dup helpers — one fired row per (type, day, context-key)
# --------------------------------------------------------------------------- #


async def _already_fired_today(
    session, reminder_type: str, context_key: str
) -> bool:
    cutoff = datetime.utcnow() - timedelta(hours=12)
    res = await session.execute(
        select(PendingReminder)
        .where(
            PendingReminder.reminder_type == reminder_type,
            PendingReminder.fired.is_(True),
            PendingReminder.fired_at >= cutoff,
        )
    )
    return any(
        (row.context or {}).get("key") == context_key
        for row in res.scalars().all()
    )


async def _mark_fired(
    session,
    reminder_type: str,
    context_key: str,
    due_at: datetime | None = None,
) -> None:
    r = PendingReminder(
        reminder_type=reminder_type,
        due_at=due_at or datetime.utcnow(),
        fired=True,
        fired_at=datetime.utcnow(),
        context={"key": context_key},
    )
    session.add(r)


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


async def _check_report_overdue(bot: Bot) -> None:
    if not settings.main_chat_id:
        return
    async with session_scope() as session:
        res = await session.execute(
            select(Report).order_by(Report.id.desc()).limit(1)
        )
        last = res.scalar_one_or_none()
        if last is None:
            return  # bootstrapping — no prior report, don't nag yet
        age = datetime.utcnow().replace(tzinfo=last.created_at.tzinfo) - last.created_at
        if age < timedelta(hours=26):
            return
        # Were there any audit events since then?
        res = await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.created_at > last.created_at
            )
        )
        recent_count = res.scalar_one() or 0
        if recent_count == 0:
            return
        key = last.created_at.date().isoformat()
        if await _already_fired_today(session, "report_overdue", key):
            return
        await bot.send_message(
            settings.main_chat_id,
            "Бляяяя, а не охуели ли вы там отчёт забыть? Уже 26+ часов прошло,"
            " а операции за это время были. /report когда уже?",
        )
        await _mark_fired(session, "report_overdue", key)


async def _check_acquiring_missing(bot: Bot) -> None:
    if not settings.main_chat_id:
        return
    async with session_scope() as session:
        res = await session.execute(
            select(func.max(Expense.expense_date)).where(
                Expense.category == "acquiring"
            )
        )
        last = res.scalar_one()
        if last is None:
            return  # no expenses yet — don't start nagging on day 1
        days = (date.today() - last).days
        if days < 2:
            return
        key = last.isoformat()
        if await _already_fired_today(session, "acquiring_missing", key):
            return
        await bot.send_message(
            settings.main_chat_id,
            f"Эквайринг пропадал. Последний раз {days} дн. назад. "
            "Сегодня платили?",
        )
        await _mark_fired(session, "acquiring_missing", key)


async def _check_cabinet_too_long(bot: Bot) -> None:
    if not settings.main_chat_id:
        return
    async with session_scope() as session:
        threshold = datetime.utcnow() - timedelta(hours=12)
        res = await session.execute(
            select(Cabinet).where(
                Cabinet.status == "in_use",
                Cabinet.in_use_since.isnot(None),
                Cabinet.in_use_since < threshold,
            )
        )
        cabs = list(res.scalars().all())
        for c in cabs:
            key = f"cabinet_{c.id}_{(c.in_use_since or datetime.utcnow()).isoformat()}"
            if await _already_fired_today(session, "cabinet_too_long", key):
                continue
            name = c.name or c.auto_code
            await bot.send_message(
                settings.main_chat_id,
                f"Кабинет {name} уже 12+ часов в работе. Ещё крутится или забыли отметить?",
            )
            await _mark_fired(session, "cabinet_too_long", key)


async def _check_poa_without_exchange(bot: Bot) -> None:
    if not settings.main_chat_id:
        return
    async with session_scope() as session:
        threshold = datetime.utcnow() - timedelta(hours=6)
        res = await session.execute(
            select(PoAWithdrawal, Client.name)
            .join(Client, Client.id == PoAWithdrawal.client_id)
            .where(PoAWithdrawal.amount_usdt.is_(None))
        )
        rows = res.all()
        for poa, client_name in rows:
            # Approximate: use withdrawal_date midnight for threshold.
            if datetime.combine(poa.withdrawal_date, datetime.min.time()) > threshold:
                continue
            key = f"poa_{poa.id}"
            if await _already_fired_today(session, "poa_without_exchange", key):
                continue
            await bot.send_message(
                settings.main_chat_id,
                f"Снятие с {client_name} (#{poa.id}) уже 6+ часов без обмена."
                " Курс нужен — кинь строку вида '150000/9300=16.13'.",
            )
            await _mark_fired(session, "poa_without_exchange", key)


async def _check_client_debt_stale(bot: Bot) -> None:
    if not settings.main_chat_id:
        return
    async with session_scope() as session:
        threshold = date.today() - timedelta(days=1)
        res = await session.execute(
            select(PoAWithdrawal, Client.name)
            .join(Client, Client.id == PoAWithdrawal.client_id)
            .where(
                PoAWithdrawal.client_paid.is_(False),
                PoAWithdrawal.client_debt_usdt.isnot(None),
                PoAWithdrawal.withdrawal_date <= threshold,
            )
        )
        rows = res.all()
        for poa, client_name in rows:
            key = f"debt_{poa.id}"
            if await _already_fired_today(session, "client_debt_stale", key):
                continue
            await bot.send_message(
                settings.main_chat_id,
                f"{client_name} ждёт свою долю ({poa.client_debt_usdt:.2f}$)."
                " Прошло больше суток — может пора?",
            )
            await _mark_fired(session, "client_debt_stale", key)


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #


_JOBS: list[tuple[str, Any, int]] = [
    # (name, coro, interval_minutes)
    ("report_overdue", _check_report_overdue, 15),
    ("acquiring_missing", _check_acquiring_missing, 60),
    ("cabinet_too_long", _check_cabinet_too_long, 30),
    ("poa_without_exchange", _check_poa_without_exchange, 15),
    ("client_debt_stale", _check_client_debt_stale, 60),
]


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    for name, coro, minutes in _JOBS:
        scheduler.add_job(
            coro,
            IntervalTrigger(minutes=minutes),
            kwargs={"bot": bot},
            id=f"reminder_{name}",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.utcnow() + timedelta(minutes=2),
        )
    scheduler.start()
    log.info("reminders_started", jobs=[n for n, _, _ in _JOBS])
    return scheduler
