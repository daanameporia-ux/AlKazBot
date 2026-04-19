"""Cabinets inventory: purchase / in_use / worked_out / blocked / recovered."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Cabinet


def _utcnow() -> datetime:
    return datetime.now(UTC)

CAB_STATUSES = (
    "in_stock", "in_use", "worked_out", "blocked", "recovered", "lost"
)


_MAX_AUTO_CODE_ATTEMPTS = 10


async def _next_auto_code(session: AsyncSession) -> str:
    """'Cab-042' rolling counter based on max(id)+1.

    Not a sequence (we'd need a migration); instead, we retry on collision.
    Under any realistic concurrency (single-bot, 1-2 ops/sec) this is plenty.
    """
    res = await session.execute(select(func.max(Cabinet.id)))
    max_id = res.scalar_one() or 0
    return f"Cab-{max_id + 1:03d}"


async def create(
    session: AsyncSession,
    *,
    name: str | None,
    cost_rub: Decimal,
    cost_usdt: Decimal,
    fx_rate: Decimal,
    prepayment_id: int | None = None,
    received_date: date | None = None,
    notes: str | None = None,
) -> Cabinet:
    from sqlalchemy.exc import IntegrityError

    # Retry loop: if two concurrent creates pick the same auto_code, one
    # hits the UNIQUE violation — we bump and try again. In practice this
    # fires almost never; the retry is just belt-and-suspenders.
    attempt = 0
    while True:
        attempt += 1
        code = await _next_auto_code(session)
        # If something else already claimed this auto_code (races), bump.
        existing = await session.execute(
            select(Cabinet.id).where(Cabinet.auto_code == code)
        )
        if existing.first() is not None:
            if attempt >= _MAX_AUTO_CODE_ATTEMPTS:
                raise RuntimeError(
                    f"auto_code exhaustion after {_MAX_AUTO_CODE_ATTEMPTS} retries"
                )
            # Force a bump by inserting a "probe" style increment —
            # simplest: recompute from max again next loop.
            continue
        cab = Cabinet(
            name=name,
            auto_code=code,
            cost_rub=cost_rub,
            cost_usdt=cost_usdt,
            fx_rate=fx_rate,
            received_date=received_date or date.today(),
            prepayment_id=prepayment_id,
            status="in_stock",
            notes=notes,
        )
        session.add(cab)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            if attempt >= _MAX_AUTO_CODE_ATTEMPTS:
                raise
            continue
        return cab


async def find_by_name_or_code(
    session: AsyncSession, key: str
) -> Cabinet | None:
    key = key.strip()
    res = await session.execute(
        select(Cabinet)
        .where(
            or_(
                func.lower(Cabinet.name) == key.lower(),
                Cabinet.auto_code == key,
            )
        )
        .order_by(Cabinet.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def set_status(
    session: AsyncSession, cabinet_id: int, status: str, **extra
) -> bool:
    if status not in CAB_STATUSES:
        return False
    values: dict = {"status": status}
    if status == "in_use":
        values["in_use_since"] = extra.get("in_use_since") or _utcnow()
    if status == "worked_out":
        values["worked_out_date"] = extra.get("worked_out_date") or date.today()
    res = await session.execute(
        update(Cabinet).where(Cabinet.id == cabinet_id).values(**values)
    )
    return (res.rowcount or 0) > 0


async def list_stock(session: AsyncSession) -> list[Cabinet]:
    res = await session.execute(
        select(Cabinet)
        .where(Cabinet.status.in_(("in_stock", "in_use", "blocked")))
        .order_by(Cabinet.status, Cabinet.id)
    )
    return list(res.scalars().all())


async def list_in_use_longer_than(
    session: AsyncSession, hours: int
) -> list[Cabinet]:
    threshold = _utcnow() - timedelta(hours=hours)
    res = await session.execute(
        select(Cabinet)
        .where(
            Cabinet.status == "in_use",
            Cabinet.in_use_since.isnot(None),
            Cabinet.in_use_since < threshold,
        )
        .order_by(Cabinet.in_use_since)
    )
    return list(res.scalars().all())
