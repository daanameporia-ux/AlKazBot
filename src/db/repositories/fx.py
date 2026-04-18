"""Current RUB→USDT rate lookup and history writes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import FxRateSnapshot


async def get_current_rate(
    session: AsyncSession, *, from_ccy: str = "RUB", to_ccy: str = "USDT"
) -> FxRateSnapshot | None:
    """Most recent snapshot flagged `is_current=True` (per currency pair)."""
    res = await session.execute(
        select(FxRateSnapshot)
        .where(
            FxRateSnapshot.from_ccy == from_ccy,
            FxRateSnapshot.to_ccy == to_ccy,
            FxRateSnapshot.is_current.is_(True),
        )
        .order_by(FxRateSnapshot.rate_date.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def set_current_rate(
    session: AsyncSession,
    *,
    from_ccy: str,
    to_ccy: str,
    rate: Decimal,
    rate_date: datetime,
    source_exchange_id: int | None = None,
) -> FxRateSnapshot:
    """Flip the `is_current` flag and insert the new rate as current."""
    await session.execute(
        update(FxRateSnapshot)
        .where(
            FxRateSnapshot.from_ccy == from_ccy,
            FxRateSnapshot.to_ccy == to_ccy,
            FxRateSnapshot.is_current.is_(True),
        )
        .values(is_current=False)
    )
    snap = FxRateSnapshot(
        from_ccy=from_ccy,
        to_ccy=to_ccy,
        rate=rate,
        rate_date=rate_date,
        source_exchange_id=source_exchange_id,
        is_current=True,
    )
    session.add(snap)
    await session.flush()
    return snap
