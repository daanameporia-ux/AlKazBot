"""RUB→USDT exchange writes + linkage with fx_rates_snapshot."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Exchange
from src.db.repositories import fx as fx_repo


async def create(
    session: AsyncSession,
    *,
    amount_rub: Decimal,
    amount_usdt: Decimal,
    fx_rate: Decimal,
    raw_input: str | None = None,
    created_by_user_id: int | None = None,
    exchange_date: datetime | None = None,
) -> Exchange:
    ex = Exchange(
        amount_rub=amount_rub,
        amount_usdt=amount_usdt,
        fx_rate=fx_rate,
        raw_input=raw_input,
        created_by=created_by_user_id,
    )
    if exchange_date is not None:
        ex.exchange_date = exchange_date
    session.add(ex)
    await session.flush()

    # Every exchange updates the "current rate" snapshot for the pair.
    await fx_repo.set_current_rate(
        session,
        from_ccy="RUB",
        to_ccy="USDT",
        rate=fx_rate,
        rate_date=ex.exchange_date,
        source_exchange_id=ex.id,
    )
    return ex
