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
    """Case-insensitive match against Partner.name, plus knowledge-base alias
    lookup (so 'kazakh' → 'Казах' works after the user teaches the bot).
    """
    from sqlalchemy import func as sa_func

    from src.db.models import KnowledgeBase

    key = name_or_alias.strip()
    if not key:
        return None
    # 1. Direct name match (case-insensitive).
    res = await session.execute(
        select(Partner).where(
            sa_func.lower(Partner.name) == key.lower(),
            Partner.is_active.is_(True),
        )
    )
    p = res.scalar_one_or_none()
    if p is not None:
        return p

    # 2. Alias via KB: look for an `alias` entry where key matches, try to
    # match its content against any partner.
    res = await session.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.category == "alias",
            KnowledgeBase.is_active.is_(True),
            sa_func.lower(KnowledgeBase.key) == key.lower(),
        )
    )
    alias_rows = list(res.scalars().all())
    partners = (
        (await session.execute(select(Partner).where(Partner.is_active.is_(True))))
        .scalars()
        .all()
    )
    for row in alias_rows:
        content_lower = (row.content or "").lower()
        for partner in partners:
            if partner.name.lower() in content_lower:
                return partner
    return None
