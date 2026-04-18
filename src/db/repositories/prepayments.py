"""Supplier prepayments and their fulfilment."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Prepayment


async def create_pending(
    session: AsyncSession,
    *,
    supplier: str | None,
    amount_rub: Decimal,
    amount_usdt: Decimal,
    fx_rate: Decimal,
    expected_cabinets: int | None = None,
    given_date: date | None = None,
    notes: str | None = None,
) -> Prepayment:
    p = Prepayment(
        supplier=supplier,
        amount_rub=amount_rub,
        amount_usdt=amount_usdt,
        fx_rate=fx_rate,
        expected_cabinets=expected_cabinets,
        given_date=given_date or date.today(),
        status="pending",
        notes=notes,
    )
    session.add(p)
    await session.flush()
    return p


async def find_open_by_supplier(
    session: AsyncSession, supplier: str
) -> Prepayment | None:
    res = await session.execute(
        select(Prepayment)
        .where(
            func.lower(Prepayment.supplier) == supplier.lower(),
            Prepayment.status.in_(("pending", "partial")),
        )
        .order_by(Prepayment.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def set_status(
    session: AsyncSession, prepayment_id: int, status: str
) -> bool:
    res = await session.execute(
        update(Prepayment).where(Prepayment.id == prepayment_id).values(status=status)
    )
    return (res.rowcount or 0) > 0


async def list_open(session: AsyncSession) -> list[Prepayment]:
    res = await session.execute(
        select(Prepayment)
        .where(Prepayment.status.in_(("pending", "partial")))
        .order_by(Prepayment.given_date.desc())
    )
    return list(res.scalars().all())
