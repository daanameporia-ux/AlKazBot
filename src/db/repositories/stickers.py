"""Sticker collection + lookup.

Every sticker sent by a whitelisted user in the main group is logged, and
its entire `set_name` is expanded so the bot has the whole pack to choose
from later.

Lookup lets the bot find a sticker whose emoji matches a mood string
("ok" / "angry" / "fire" / "skull" etc. mapped to emoji). Falls back to a
random pick from recently-seen stickers if no emoji match.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import SeenSticker


async def upsert(
    session: AsyncSession,
    *,
    file_id: str,
    file_unique_id: str | None,
    sticker_set: str | None,
    emoji: str | None,
    is_animated: bool = False,
    is_video: bool = False,
) -> SeenSticker:
    res = await session.execute(
        select(SeenSticker).where(SeenSticker.file_id == file_id)
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        if emoji and not existing.emoji:
            existing.emoji = emoji
        if sticker_set and not existing.sticker_set:
            existing.sticker_set = sticker_set
        return existing
    s = SeenSticker(
        file_id=file_id,
        file_unique_id=file_unique_id,
        sticker_set=sticker_set,
        emoji=emoji,
        is_animated=is_animated,
        is_video=is_video,
    )
    session.add(s)
    await session.flush()
    return s


async def pick_by_emoji(
    session: AsyncSession, emojis: list[str], *, limit: int = 20
) -> SeenSticker | None:
    """Random pick among stickers whose `emoji` is in the given set."""
    if not emojis:
        return None
    res = await session.execute(
        select(SeenSticker)
        .where(SeenSticker.emoji.in_(emojis))
        .order_by(SeenSticker.usage_count.asc())
        .limit(limit)
    )
    rows = list(res.scalars().all())
    if not rows:
        return None
    return random.choice(rows)


async def pick_random(
    session: AsyncSession, *, limit: int = 50
) -> SeenSticker | None:
    res = await session.execute(
        select(SeenSticker).order_by(SeenSticker.first_seen.desc()).limit(limit)
    )
    rows = list(res.scalars().all())
    if not rows:
        return None
    return random.choice(rows)


async def bump_usage(session: AsyncSession, sticker_id: int) -> None:
    await session.execute(
        update(SeenSticker)
        .where(SeenSticker.id == sticker_id)
        .values(
            last_used=datetime.now(UTC),
            usage_count=SeenSticker.usage_count + 1,
        )
    )


async def count(session: AsyncSession) -> int:
    res = await session.execute(select(func.count(SeenSticker.id)))
    return int(res.scalar_one() or 0)


async def known_sets(session: AsyncSession) -> list[str]:
    res = await session.execute(
        select(SeenSticker.sticker_set)
        .where(SeenSticker.sticker_set.isnot(None))
        .distinct()
    )
    return [r for (r,) in res.all() if r]
