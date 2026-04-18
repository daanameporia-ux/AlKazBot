"""User + Partner repositories.

The upsert-on-first-message pattern keeps auth simple: any whitelisted TG user
who writes to the bot gets a row in `users`; if their tg_user_id matches a
partner in seed data, the partner relation is wired automatically.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Partner, User


async def upsert_user(
    session: AsyncSession,
    *,
    tg_user_id: int,
    tg_username: str | None,
    display_name: str | None,
) -> User:
    """Create or update a User row from Telegram profile data.

    Also links the user to a Partner row if one exists with the same tg id.
    """
    existing = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    user = existing.scalar_one_or_none()

    partner = (
        await session.execute(select(Partner).where(Partner.tg_user_id == tg_user_id))
    ).scalar_one_or_none()

    if user is None:
        user = User(
            tg_user_id=tg_user_id,
            tg_username=tg_username,
            display_name=display_name,
            role="partner" if partner else "assistant",
            partner_id=partner.id if partner else None,
        )
        session.add(user)
        await session.flush()
        return user

    # Refresh displayable fields; role is sticky unless partner just got linked.
    user.tg_username = tg_username
    user.display_name = display_name
    if partner and user.partner_id != partner.id:
        user.partner_id = partner.id
        user.role = "partner"
    return user


async def get_user_by_tg_id(session: AsyncSession, tg_user_id: int) -> User | None:
    res = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    return res.scalar_one_or_none()


async def get_partner_by_tg_id(session: AsyncSession, tg_user_id: int) -> Partner | None:
    res = await session.execute(
        select(Partner).where(Partner.tg_user_id == tg_user_id)
    )
    return res.scalar_one_or_none()


async def list_partners(session: AsyncSession, *, active_only: bool = True) -> list[Partner]:
    stmt = select(Partner).order_by(Partner.id)
    if active_only:
        stmt = stmt.where(Partner.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())
