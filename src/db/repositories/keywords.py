"""Trigger keyword CRUD + active-list loader."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TriggerKeyword

MIN_KEYWORD_LEN = 3


async def add(
    session: AsyncSession,
    *,
    keyword: str,
    created_by_user_id: int | None = None,
    notes: str | None = None,
) -> TriggerKeyword:
    kw = keyword.strip().lower()
    if len(kw) < MIN_KEYWORD_LEN:
        raise ValueError(
            f"Слишком короткое ключевое слово ({len(kw)} симв.). Минимум {MIN_KEYWORD_LEN}."
        )
    # Upsert — if exists, reactivate.
    res = await session.execute(
        select(TriggerKeyword).where(TriggerKeyword.keyword == kw)
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
            if notes:
                existing.notes = notes
        return existing
    row = TriggerKeyword(
        keyword=kw, notes=notes, created_by=created_by_user_id, is_active=True
    )
    session.add(row)
    await session.flush()
    return row


async def deactivate(session: AsyncSession, keyword_id: int) -> bool:
    res = await session.execute(
        update(TriggerKeyword)
        .where(TriggerKeyword.id == keyword_id, TriggerKeyword.is_active.is_(True))
        .values(is_active=False)
    )
    return (res.rowcount or 0) > 0


async def list_active(session: AsyncSession) -> list[TriggerKeyword]:
    res = await session.execute(
        select(TriggerKeyword)
        .where(TriggerKeyword.is_active.is_(True))
        .order_by(TriggerKeyword.keyword)
    )
    return list(res.scalars().all())


async def list_all(session: AsyncSession) -> list[TriggerKeyword]:
    res = await session.execute(
        select(TriggerKeyword).order_by(TriggerKeyword.keyword)
    )
    return list(res.scalars().all())
