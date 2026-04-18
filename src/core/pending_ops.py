"""Registry of operations awaiting user confirmation.

Persisted in the `pending_ops` table so Railway redeploys don't lose
active preview cards. Users pressing ✅ after a container restart still
works — the uid travels in callback_data and we look it up in the DB.

Entries older than `ENTRY_TTL_SEC` are auto-expired on access and can be
cleaned up by a periodic task (started from main.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from src.db.models import PendingOperation
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)

ENTRY_TTL_SEC = 30 * 60


@dataclass(slots=True)
class PendingOp:
    """Read-only view exposed to callers. Mirrors PendingOperation columns."""

    uid: str
    chat_id: int
    preview_message_id: int | None
    intent: str
    fields: dict[str, Any]
    summary: str
    source_message_ids: list[int]
    created_by_tg_id: int
    created_at: datetime
    status: str


def _to_view(row: PendingOperation) -> PendingOp:
    return PendingOp(
        uid=row.uid,
        chat_id=row.chat_id,
        preview_message_id=row.preview_message_id,
        intent=row.intent,
        fields=dict(row.fields or {}),
        summary=row.summary,
        source_message_ids=list(row.source_message_ids or []),
        created_by_tg_id=row.created_by_tg_id,
        created_at=row.created_at,
        status=row.status,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_expired(row: PendingOperation) -> bool:
    # created_at is tz-aware (Postgres TIMESTAMPTZ).
    return (_utcnow() - row.created_at) > timedelta(seconds=ENTRY_TTL_SEC)


async def register(
    *,
    chat_id: int,
    intent: str,
    fields: dict[str, Any],
    summary: str,
    source_message_ids: list[int],
    created_by_tg_id: int,
) -> PendingOp:
    uid = uuid.uuid4().hex[:10]
    async with session_scope() as session:
        row = PendingOperation(
            uid=uid,
            chat_id=chat_id,
            intent=intent,
            fields=fields,
            summary=summary,
            source_message_ids=source_message_ids,
            created_by_tg_id=created_by_tg_id,
            status="pending",
        )
        session.add(row)
        await session.flush()
        return _to_view(row)


async def attach_preview(uid: str, preview_message_id: int) -> None:
    async with session_scope() as session:
        await session.execute(
            update(PendingOperation)
            .where(PendingOperation.uid == uid)
            .values(preview_message_id=preview_message_id)
        )


async def pop_for_confirm(uid: str) -> PendingOp | None:
    """Atomically flip pending→confirmed and return the view — or None if
    the row doesn't exist / was already handled / has expired.
    """
    async with session_scope() as session:
        res = await session.execute(
            select(PendingOperation).where(PendingOperation.uid == uid)
        )
        row = res.scalar_one_or_none()
        if row is None:
            return None
        if row.status != "pending":
            return None
        if _is_expired(row):
            row.status = "expired"
            return None
        row.status = "confirmed"
        row.confirmed_at = _utcnow()
        return _to_view(row)


async def pop_for_cancel(uid: str) -> PendingOp | None:
    async with session_scope() as session:
        res = await session.execute(
            select(PendingOperation).where(PendingOperation.uid == uid)
        )
        row = res.scalar_one_or_none()
        if row is None:
            return None
        if row.status != "pending":
            return None
        row.status = "cancelled"
        return _to_view(row)


async def peek(uid: str) -> PendingOp | None:
    async with session_scope() as session:
        res = await session.execute(
            select(PendingOperation).where(PendingOperation.uid == uid)
        )
        row = res.scalar_one_or_none()
        if row is None:
            return None
        return _to_view(row)


async def list_active(chat_id: int | None = None) -> list[PendingOp]:
    """Return all pending (non-expired) cards. Useful for resync on boot."""
    cutoff = _utcnow() - timedelta(seconds=ENTRY_TTL_SEC)
    async with session_scope() as session:
        stmt = select(PendingOperation).where(
            PendingOperation.status == "pending",
            PendingOperation.created_at > cutoff,
        )
        if chat_id is not None:
            stmt = stmt.where(PendingOperation.chat_id == chat_id)
        stmt = stmt.order_by(PendingOperation.created_at.desc())
        rows = list((await session.execute(stmt)).scalars().all())
    return [_to_view(r) for r in rows]


async def expire_stale(bot=None) -> int:
    """Mark status=expired on everything older than TTL. Returns count.

    If a `bot` is provided, we also edit the stale preview messages in
    Telegram to strike the buttons so users don't keep tapping into the
    void. Silently skips messages we can't edit anymore (user deleted them,
    chat gone, etc.).
    """
    cutoff = _utcnow() - timedelta(seconds=ENTRY_TTL_SEC)
    async with session_scope() as session:
        res = await session.execute(
            select(PendingOperation).where(
                PendingOperation.status == "pending",
                PendingOperation.created_at <= cutoff,
            )
        )
        stale = list(res.scalars().all())
        for row in stale:
            row.status = "expired"
    if bot is not None:
        import contextlib

        for row in stale:
            if row.preview_message_id is None:
                continue
            with contextlib.suppress(Exception):
                await bot.edit_message_reply_markup(
                    chat_id=row.chat_id,
                    message_id=row.preview_message_id,
                    reply_markup=None,
                )
                await bot.send_message(
                    row.chat_id,
                    f"⏰ Карточка «{row.summary[:80]}» истекла — не записал. "
                    "Пришли снова если нужно.",
                    reply_to_message_id=row.preview_message_id,
                )
    return len(stale)
