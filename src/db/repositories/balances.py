"""Read-only queries that summarize the current state of the business.

These back the quick /balance, /partners, /fx, /debts commands. None of them
mutate state — they're just aggregators over the existing tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    FxRateSnapshot,
    Partner,
    PartnerContribution,
    PartnerWithdrawal,
    Wallet,
    WalletSnapshot,
)


@dataclass(slots=True)
class WalletBalance:
    wallet_id: int
    wallet_code: str
    wallet_name: str
    currency: str
    amount_native: Decimal | None
    amount_usdt: Decimal | None
    last_updated: str | None


@dataclass(slots=True)
class PartnerShare:
    partner_id: int
    partner_name: str
    deposits_usdt: Decimal
    withdrawals_usdt: Decimal
    contributions_usdt: Decimal  # from POA etc.
    net_usdt: Decimal


async def latest_wallet_balances(session: AsyncSession) -> list[WalletBalance]:
    """Latest snapshot per wallet, walking back from the most recent."""
    # Subquery: latest snapshot_time per wallet_id.
    latest = (
        select(
            WalletSnapshot.wallet_id,
            func.max(WalletSnapshot.snapshot_time).label("ts"),
        )
        .group_by(WalletSnapshot.wallet_id)
        .subquery()
    )

    stmt = (
        select(Wallet, WalletSnapshot)
        .outerjoin(latest, latest.c.wallet_id == Wallet.id)
        .outerjoin(
            WalletSnapshot,
            (WalletSnapshot.wallet_id == Wallet.id)
            & (WalletSnapshot.snapshot_time == latest.c.ts),
        )
        .where(Wallet.is_active.is_(True))
        .order_by(Wallet.id)
    )
    rows = (await session.execute(stmt)).all()
    out: list[WalletBalance] = []
    for wallet, snap in rows:
        out.append(
            WalletBalance(
                wallet_id=wallet.id,
                wallet_code=wallet.code,
                wallet_name=wallet.name,
                currency=wallet.currency,
                amount_native=snap.amount_native if snap else None,
                amount_usdt=snap.amount_usdt if snap else None,
                last_updated=snap.snapshot_time.isoformat() if snap else None,
            )
        )
    return out


async def partner_shares(session: AsyncSession) -> list[PartnerShare]:
    """Per-partner running totals: initial deposits + POA contributions − withdrawals."""
    partners = (
        (await session.execute(select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.id)))
        .scalars()
        .all()
    )
    out: list[PartnerShare] = []
    for p in partners:
        deposits = (
            await session.execute(
                select(func.coalesce(func.sum(PartnerContribution.amount_usdt), 0))
                .where(
                    PartnerContribution.partner_id == p.id,
                    PartnerContribution.source == "initial_depo",
                )
            )
        ).scalar_one()
        poa = (
            await session.execute(
                select(func.coalesce(func.sum(PartnerContribution.amount_usdt), 0))
                .where(
                    PartnerContribution.partner_id == p.id,
                    PartnerContribution.source == "poa_share",
                )
            )
        ).scalar_one()
        manual = (
            await session.execute(
                select(func.coalesce(func.sum(PartnerContribution.amount_usdt), 0))
                .where(
                    PartnerContribution.partner_id == p.id,
                    PartnerContribution.source == "manual",
                )
            )
        ).scalar_one()
        withdrawals = (
            await session.execute(
                select(func.coalesce(func.sum(PartnerWithdrawal.amount_usdt), 0))
                .where(PartnerWithdrawal.partner_id == p.id)
            )
        ).scalar_one()
        net = (Decimal(deposits) + Decimal(poa) + Decimal(manual)) - Decimal(withdrawals)
        out.append(
            PartnerShare(
                partner_id=p.id,
                partner_name=p.name,
                deposits_usdt=Decimal(deposits) + Decimal(manual),
                contributions_usdt=Decimal(poa),
                withdrawals_usdt=Decimal(withdrawals),
                net_usdt=net,
            )
        )
    return out


async def current_fx_rate(session: AsyncSession) -> FxRateSnapshot | None:
    """Newest FX snapshot marked `is_current=True` for RUB→USDT."""
    res = await session.execute(
        select(FxRateSnapshot)
        .where(
            FxRateSnapshot.from_ccy == "RUB",
            FxRateSnapshot.to_ccy == "USDT",
            FxRateSnapshot.is_current.is_(True),
        )
        .order_by(FxRateSnapshot.rate_date.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()
