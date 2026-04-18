"""POA clients — lookups and creates."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Client


async def get_or_create(session: AsyncSession, name: str) -> Client:
    """Case-insensitive lookup, creates if missing."""
    name = name.strip()
    res = await session.execute(
        select(Client).where(func.lower(Client.name) == name.lower())
    )
    c = res.scalar_one_or_none()
    if c is not None:
        return c
    c = Client(name=name)
    session.add(c)
    await session.flush()
    return c


async def list_all(session: AsyncSession) -> list[Client]:
    res = await session.execute(select(Client).order_by(Client.name))
    return list(res.scalars().all())


async def get_by_name(session: AsyncSession, name: str) -> Client | None:
    res = await session.execute(
        select(Client).where(func.lower(Client.name) == name.lower())
    )
    return res.scalar_one_or_none()
