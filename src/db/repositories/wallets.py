"""Wallet lookup helpers. Wallets are seeded constants — no CRUD here."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Wallet


async def get_by_code(session: AsyncSession, code: str) -> Wallet | None:
    res = await session.execute(select(Wallet).where(Wallet.code == code))
    return res.scalar_one_or_none()


async def list_wallets(
    session: AsyncSession, *, active_only: bool = True
) -> list[Wallet]:
    stmt = select(Wallet).order_by(Wallet.id)
    if active_only:
        stmt = stmt.where(Wallet.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())
