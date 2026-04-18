"""Partner money-flow writes: contributions (depo / poa_share / manual) and
withdrawals.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Partner, PartnerContribution, PartnerWithdrawal


async def record_contribution(
    session: AsyncSession,
    *,
    partner_id: int,
    amount_usdt: Decimal,
    source: str,  # 'initial_depo' | 'poa_share' | 'manual'
    source_ref_id: int | None = None,
    contribution_date: date | None = None,
    notes: str | None = None,
) -> PartnerContribution:
    c = PartnerContribution(
        partner_id=partner_id,
        source=source,
        source_ref_id=source_ref_id,
        amount_usdt=amount_usdt,
        contribution_date=contribution_date or date.today(),
        notes=notes,
    )
    session.add(c)
    await session.flush()
    return c


async def record_withdrawal(
    session: AsyncSession,
    *,
    partner_id: int,
    amount_usdt: Decimal,
    from_wallet_id: int | None = None,
    withdrawal_date: date | None = None,
    notes: str | None = None,
) -> PartnerWithdrawal:
    w = PartnerWithdrawal(
        partner_id=partner_id,
        amount_usdt=amount_usdt,
        from_wallet_id=from_wallet_id,
        withdrawal_date=withdrawal_date or date.today(),
        notes=notes,
    )
    session.add(w)
    await session.flush()
    return w


async def resolve_partner(
    session: AsyncSession, name_or_alias: str
) -> Partner | None:
    """Case-insensitive, handles 'Казах'/'казах'/'kazakh' etc."""
    candidates = {
        name_or_alias.strip(),
        name_or_alias.strip().lower(),
        name_or_alias.strip().title(),
    }
    for c in list(candidates):
        res = await session.execute(
            select(Partner).where(
                Partner.name == c, Partner.is_active.is_(True)
            )
        )
        p = res.scalar_one_or_none()
        if p is not None:
            return p
    return None
