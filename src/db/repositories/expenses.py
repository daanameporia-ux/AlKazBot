"""Expense writes (acquiring / commissions / misc)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Expense


async def create(
    session: AsyncSession,
    *,
    category: str,
    amount_usdt: Decimal,
    amount_rub: Decimal | None = None,
    fx_rate: Decimal | None = None,
    description: str | None = None,
    expense_date: date | None = None,
    created_by_user_id: int | None = None,
) -> Expense:
    ex = Expense(
        category=category,
        amount_rub=amount_rub,
        amount_usdt=amount_usdt,
        fx_rate=fx_rate,
        description=description,
        expense_date=expense_date or date.today(),
        created_by=created_by_user_id,
    )
    session.add(ex)
    await session.flush()
    return ex


async def last_of_category(
    session: AsyncSession, category: str
) -> Expense | None:
    res = await session.execute(
        select(Expense)
        .where(Expense.category == category)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()
