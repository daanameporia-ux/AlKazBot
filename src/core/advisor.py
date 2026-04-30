"""Proactive advisor signals.

These are lightweight, periodic checks that look for patterns the team
tends to forget:

  * **balance_cabinet**: user keeps asking about balance/report while a
    cabinet's been "in_use" for >12h — nudge them to mark it worked_out.
  * **client_repeat**: same client mentioned N times in last 24h with no
    POA logged — suggest creating one.
  * **fx_drift**: a new exchange lands with fx_rate differing from the
    last stored snapshot by more than FX_DRIFT_PCT — flag it.

Each signal is fired at most once per "context key" per day — de-duped
via `pending_reminders` the same way the existing nags are.

Philosophy: signal or shut up. Never filler. A proactive message that
doesn't add value erodes the bot's signal-to-noise, and the team stops
reading them. We'd rather miss some than spam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import func, select

from src.bot.middlewares.logging import log_bot_reply
from src.config import settings
from src.db.models import (
    Cabinet,
    Client,
    FxRateSnapshot,
    MessageLog,
    PoAWithdrawal,
)
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)


async def _send_and_log(bot: Bot, text: str, intent_hint: str) -> None:
    """Send an advisor poke and persist it to message_log so future
    reply_to chains and recent_history can resolve references back to it.

    Live bug 2026-04-30: advisor sent «Алан 12+ ч в работе. Отработал?»,
    Арбуз reply'нул «Нет, убери его на склад назад» — но parent был
    не залогирован, бот потерял якорь и проигнорировал команду.
    """
    if not settings.main_chat_id:
        return
    try:
        sent = await bot.send_message(settings.main_chat_id, text)
        await log_bot_reply(
            chat_id=settings.main_chat_id,
            tg_message_id=sent.message_id,
            text=text,
            intent_hint=intent_hint,
        )
    except Exception:
        log.exception("advisor_send_failed", intent=intent_hint)


def _utcnow() -> datetime:
    return datetime.now(UTC)


FX_DRIFT_PCT = Decimal("5.0")  # Alert threshold for rate change.
CLIENT_MENTION_THRESHOLD = 3
CLIENT_MENTION_WINDOW_HOURS = 24


# --------------------------------------------------------------------------- #
# Helpers shared with reminders de-dup
# --------------------------------------------------------------------------- #


async def _already_fired_today(session, reminder_type: str, context_key: str) -> bool:
    from src.db.models import PendingReminder

    cutoff = _utcnow() - timedelta(hours=12)
    res = await session.execute(
        select(PendingReminder).where(
            PendingReminder.reminder_type == reminder_type,
            PendingReminder.fired.is_(True),
            PendingReminder.fired_at >= cutoff,
        )
    )
    return any(
        (row.context or {}).get("key") == context_key
        for row in res.scalars().all()
    )


async def _mark_fired(session, reminder_type: str, context_key: str) -> None:
    from src.db.models import PendingReminder

    session.add(
        PendingReminder(
            reminder_type=reminder_type,
            due_at=_utcnow(),
            fired=True,
            fired_at=_utcnow(),
            context={"key": context_key},
        )
    )


# --------------------------------------------------------------------------- #
# balance ↔ cabinet
# --------------------------------------------------------------------------- #


async def check_balance_vs_cabinet(bot: Bot) -> None:
    """If /balance was called recently AND a cabinet's been in_use >12h,
    mention the cabinet once. Users tend to ask "what's the balance?" when
    they've forgotten about in-flight inventory."""
    if not settings.main_chat_id:
        return
    from src.core.reminders import _in_quiet_window

    if _in_quiet_window():
        return
    to_send: list[str] = []
    async with session_scope() as session:
        cutoff_q = _utcnow() - timedelta(hours=2)
        # Check for a recent /balance or "баланс ..." mention.
        recent_balance_q = await session.execute(
            select(MessageLog)
            .where(
                MessageLog.chat_id == settings.main_chat_id,
                MessageLog.created_at >= cutoff_q,
                MessageLog.is_bot.is_(False),
            )
            .order_by(MessageLog.id.desc())
            .limit(30)
        )
        recent_msgs = list(recent_balance_q.scalars().all())
        asked_balance = any(
            (m.text or "").lower().startswith("/balance")
            or "баланс" in (m.text or "").lower()
            for m in recent_msgs
        )
        if not asked_balance:
            return
        # Cabinets in_use >12h
        cutoff_c = _utcnow() - timedelta(hours=12)
        res = await session.execute(
            select(Cabinet).where(
                Cabinet.status == "in_use",
                Cabinet.in_use_since.isnot(None),
                Cabinet.in_use_since < cutoff_c,
            )
        )
        for c in res.scalars().all():
            key = f"balance_cabinet_{c.id}"
            if await _already_fired_today(session, "balance_vs_cabinet", key):
                continue
            await _mark_fired(session, "balance_vs_cabinet", key)
            name = c.name or c.auto_code
            to_send.append(
                f"💡 Пока смотришь баланс — кабинет {name} уже 12+ ч в работе. "
                "Отработал?"
            )
    for text in to_send:
        await _send_and_log(bot, text, "advisor_balance_cabinet")


# --------------------------------------------------------------------------- #
# client repeat (mention without POA)
# --------------------------------------------------------------------------- #


async def check_client_repeat(bot: Bot) -> None:
    """Same client name appearing N+ times in last 24h without any POA
    operation against them — suggest creating a POA."""
    if not settings.main_chat_id:
        return
    from src.core.reminders import _in_quiet_window

    if _in_quiet_window():
        return
    to_send: list[str] = []
    async with session_scope() as session:
        # Pull clients we actually know about (avoid false positives from
        # common names outside our roster).
        res = await session.execute(select(Client.id, Client.name))
        clients = [(cid, name) for cid, name in res.all()]
        if not clients:
            return
        window = _utcnow() - timedelta(hours=CLIENT_MENTION_WINDOW_HOURS)
        # Count case-insensitive substring mentions in last 24h.
        for cid, name in clients:
            if len(name) < 3:
                continue
            count_res = await session.execute(
                select(func.count(MessageLog.id)).where(
                    MessageLog.chat_id == settings.main_chat_id,
                    MessageLog.created_at >= window,
                    MessageLog.is_bot.is_(False),
                    func.lower(MessageLog.text).contains(name.lower()),
                )
            )
            mentions = int(count_res.scalar_one() or 0)
            if mentions < CLIENT_MENTION_THRESHOLD:
                continue
            # Any POA for this client in last 24h?
            poa_res = await session.execute(
                select(func.count(PoAWithdrawal.id)).where(
                    PoAWithdrawal.client_id == cid,
                    PoAWithdrawal.created_at >= window
                    if hasattr(PoAWithdrawal, "created_at")
                    else func.true(),
                )
            )
            poa_count = int(poa_res.scalar_one() or 0)
            if poa_count:
                continue  # already logged
            key = f"client_repeat_{cid}_{_utcnow().date().isoformat()}"
            if await _already_fired_today(session, "client_repeat", key):
                continue
            await _mark_fired(session, "client_repeat", key)
            to_send.append(
                f"💡 {name} мелькает в чате {mentions} раз за сутки, "
                "а POA на него не заведён. Занесём?"
            )
    for text in to_send:
        await _send_and_log(bot, text, "advisor_client_repeat")


# --------------------------------------------------------------------------- #
# fx drift
# --------------------------------------------------------------------------- #


async def check_fx_drift(bot: Bot) -> None:
    """If most recent two distinct fx snapshots diverge by FX_DRIFT_PCT or
    more — flag it."""
    if not settings.main_chat_id:
        return
    from src.core.reminders import _in_quiet_window

    if _in_quiet_window():
        return
    text: str | None = None
    async with session_scope() as session:
        res = await session.execute(
            select(FxRateSnapshot)
            .order_by(FxRateSnapshot.rate_date.desc())
            .limit(5)
        )
        rows = list(res.scalars().all())
        if len(rows) < 2:
            return
        latest = rows[0]
        # Previous distinct rate (skip identical rate rows).
        prev = next((r for r in rows[1:] if r.rate != latest.rate), None)
        if prev is None:
            return
        diff_pct = (
            abs(Decimal(latest.rate) - Decimal(prev.rate))
            / Decimal(prev.rate)
            * Decimal("100")
        )
        if diff_pct < FX_DRIFT_PCT:
            return
        key = f"fx_drift_{latest.id}"
        if await _already_fired_today(session, "fx_drift", key):
            return
        await _mark_fired(session, "fx_drift", key)
        direction = "вверх" if latest.rate > prev.rate else "вниз"
        text = (
            f"💡 Курс сдвинулся на {diff_pct:.1f}% {direction}: "
            f"{prev.rate} → {latest.rate} ₽/USDT. Проверь Rapira."
        )
    if text:
        await _send_and_log(bot, text, "advisor_fx_drift")


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

ADVISOR_JOBS: list[tuple[str, object, int]] = [
    ("advisor_balance_cabinet", check_balance_vs_cabinet, 30),
    ("advisor_client_repeat", check_client_repeat, 60),
    ("advisor_fx_drift", check_fx_drift, 30),
]
