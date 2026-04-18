"""Wallet snapshot writes — tied to a Report."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Wallet, WalletSnapshot


async def create(
    session: AsyncSession,
    *,
    wallet: Wallet,
    amount_native: Decimal,
    amount_usdt: Decimal,
    fx_rate: Decimal | None,
    report_id: int | None = None,
) -> WalletSnapshot:
    snap = WalletSnapshot(
        report_id=report_id,
        wallet_id=wallet.id,
        amount_native=amount_native,
        amount_usdt=amount_usdt,
        fx_rate=fx_rate,
    )
    session.add(snap)
    await session.flush()
    return snap
