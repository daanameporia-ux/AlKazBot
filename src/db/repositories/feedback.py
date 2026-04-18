"""Feedback / wishes queue — the bot listens for "хотелось бы...", "давайте..."
and stores them here for the team to review.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Feedback


async def add(
    session: AsyncSession,
    *,
    message: str,
    created_by_user_id: int | None = None,
    context: str | None = None,
) -> Feedback:
    fb = Feedback(
        message=message,
        created_by=created_by_user_id,
        context=context,
    )
    session.add(fb)
    await session.flush()
    return fb


async def list_open(session: AsyncSession, *, limit: int = 50) -> list[Feedback]:
    res = await session.execute(
        select(Feedback)
        .where(Feedback.status.in_(("new", "noted", "in_progress")))
        .order_by(Feedback.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def set_status(session: AsyncSession, fb_id: int, status: str) -> bool:
    res = await session.execute(
        update(Feedback).where(Feedback.id == fb_id).values(status=status)
    )
    return (res.rowcount or 0) > 0
