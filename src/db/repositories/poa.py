"""POA withdrawal writes + linkage to exchanges + contribution fan-out.

When a POA withdrawal is recorded:
  1. `poa_withdrawals` row is inserted (amount_usdt NULL until exchange lands)
  2. A `pending_reminders` entry is registered so we can ping if the exchange
     isn't attached within 6h.

When a subsequent exchange lands:
  3. amount_usdt + fx_rate get filled in.
  4. Commission share of each partner gets inserted into partner_contributions
     with source='poa_share' and source_ref_id=poa.id.
  5. client_debt_usdt = amount_rub * client_share_pct / 100 / fx_rate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PendingReminder, PoAWithdrawal
from src.db.repositories import partner_ops as partner_repo


async def create_pending(
    session: AsyncSession,
    *,
    client_id: int,
    amount_rub: Decimal,
    partner_shares: list[dict[str, Any]],
    client_share_pct: Decimal,
    withdrawal_date: date | None = None,
    notes: str | None = None,
    created_by_user_id: int | None = None,
) -> PoAWithdrawal:
    poa = PoAWithdrawal(
        client_id=client_id,
        amount_rub=amount_rub,
        partner_shares=partner_shares,
        client_share_pct=client_share_pct,
        withdrawal_date=withdrawal_date or date.today(),
        created_by=created_by_user_id,
        notes=notes,
    )
    session.add(poa)
    await session.flush()
    return poa


async def attach_exchange(
    session: AsyncSession,
    *,
    poa_id: int,
    fx_rate: Decimal,
) -> PoAWithdrawal | None:
    """Fill in amount_usdt / client_debt_usdt after an exchange lands.
    Also fan out partner_contributions with source='poa_share'.
    """
    res = await session.execute(
        select(PoAWithdrawal).where(PoAWithdrawal.id == poa_id)
    )
    poa = res.scalar_one_or_none()
    if poa is None or poa.amount_usdt is not None:
        return poa  # already linked or missing

    amount_usdt = Decimal(poa.amount_rub) / fx_rate
    client_share_usdt = amount_usdt * Decimal(poa.client_share_pct) / Decimal(100)
    commission_usdt = amount_usdt - client_share_usdt

    poa.amount_usdt = amount_usdt
    poa.fx_rate = fx_rate
    poa.client_debt_usdt = client_share_usdt

    # Fan out partner_contributions for each share
    for share in poa.partner_shares or []:
        pname = share.get("partner")
        pct = Decimal(str(share.get("pct", 0)))
        if not pname or pct <= 0:
            continue
        partner = await partner_repo.resolve_partner(session, pname)
        if partner is None:
            continue
        share_amount = commission_usdt * pct / Decimal(100)
        await partner_repo.record_contribution(
            session,
            partner_id=partner.id,
            amount_usdt=share_amount,
            source="poa_share",
            source_ref_id=poa.id,
            contribution_date=poa.withdrawal_date,
            notes=f"Доля с снятия с {pname} (POA #{poa.id})",
        )
    return poa


async def list_pending_exchange(session: AsyncSession) -> list[PoAWithdrawal]:
    """POA rows that still don't have an attached exchange."""
    res = await session.execute(
        select(PoAWithdrawal)
        .where(PoAWithdrawal.amount_usdt.is_(None))
        .order_by(PoAWithdrawal.id.desc())
    )
    return list(res.scalars().all())


async def list_unpaid_client_debts(session: AsyncSession) -> list[PoAWithdrawal]:
    res = await session.execute(
        select(PoAWithdrawal)
        .where(
            PoAWithdrawal.client_paid.is_(False),
            PoAWithdrawal.client_debt_usdt.isnot(None),
        )
        .order_by(PoAWithdrawal.withdrawal_date)
    )
    return list(res.scalars().all())


async def mark_client_paid(
    session: AsyncSession, poa_id: int, paid_date: date | None = None
) -> bool:
    res = await session.execute(
        update(PoAWithdrawal)
        .where(PoAWithdrawal.id == poa_id)
        .values(client_paid=True, client_paid_date=paid_date or date.today())
    )
    return (res.rowcount or 0) > 0


async def schedule_exchange_reminder(
    session: AsyncSession, *, poa_id: int, due_at
) -> PendingReminder:
    r = PendingReminder(
        reminder_type="poa_needs_exchange",
        due_at=due_at,
        context={"poa_id": poa_id},
    )
    session.add(r)
    await session.flush()
    return r
