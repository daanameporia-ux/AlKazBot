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


# --------------------------------------------------------------------------- #
# Sticker-usage log — what the team / bot actually sends, with context.
# Lives in the same module so callers don't need a second import.
# --------------------------------------------------------------------------- #

from src.db.models import StickerUsage  # noqa: E402


async def log_usage(
    session: AsyncSession,
    *,
    sticker_file_unique_id: str | None,
    sticker_set: str | None,
    emoji: str | None,
    tg_user_id: int | None,
    chat_id: int | None,
    tg_message_id: int | None,
    preceding_text: str | None,
    sent_by_bot: bool = False,
) -> StickerUsage:
    row = StickerUsage(
        sticker_file_unique_id=sticker_file_unique_id,
        sticker_set=sticker_set,
        emoji=emoji,
        tg_user_id=tg_user_id,
        chat_id=chat_id,
        tg_message_id=tg_message_id,
        preceding_text=(preceding_text or "")[:2000] or None,
        sent_by_bot=sent_by_bot,
    )
    session.add(row)
    await session.flush()
    return row


async def recent_usage_examples(
    session: AsyncSession, *, limit: int = 12, humans_only: bool = True
) -> list[StickerUsage]:
    """Pull last N sticker-usage rows (defaults to human-sent only) so the
    system prompt can show the bot what contexts the team reacts to.
    """
    q = select(StickerUsage).order_by(StickerUsage.id.desc()).limit(limit)
    if humans_only:
        q = q.where(StickerUsage.sent_by_bot.is_(False))
    res = await session.execute(q)
    return list(res.scalars().all())


async def pack_emoji_summary(
    session: AsyncSession, *, pack_limit: int = 10
) -> list[tuple[str, list[str]]]:
    """Per-pack emoji spectrum — returns `[(pack_name, [emoji1, emoji2, ...]), ...]`
    sorted by pack size descending, capped at `pack_limit` packs.

    Used to render a compact "available stickers" block into the system
    prompt so Claude knows what emoji slots actually resolve to real
    stickers in our library.
    """
    res = await session.execute(
        select(SeenSticker.sticker_set, SeenSticker.emoji).where(
            SeenSticker.sticker_set.isnot(None)
        )
    )
    by_pack: dict[str, set[str]] = {}
    for pack, emoji in res.all():
        if not pack:
            continue
        by_pack.setdefault(pack, set())
        if emoji:
            by_pack[pack].add(emoji)
    ranked = sorted(by_pack.items(), key=lambda kv: -len(kv[1]))
    return [(p, sorted(e)) for p, e in ranked[:pack_limit]]


async def described_catalog(
    session: AsyncSession,
    *,
    per_pack: int = 20,
    pack_limit: int = 10,
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Per-pack catalog with Vision descriptions.

    Returns `[(pack_name, [(emoji, description, file_unique_id), ...]), ...]`
    where each inner tuple is a described sticker. Only stickers with
    `description IS NOT NULL` are included. Packs sorted by described-
    count descending; inner list ordered by `usage_count DESC`
    (stickers the team actually loves first) then by id.

    Cap `per_pack` so the system-prompt block stays in cache-friendly
    territory. `pack_limit` bounds the total pack count.
    """
    res = await session.execute(
        select(
            SeenSticker.sticker_set,
            SeenSticker.emoji,
            SeenSticker.description,
            SeenSticker.file_unique_id,
            SeenSticker.usage_count,
        )
        .where(
            SeenSticker.sticker_set.isnot(None),
            SeenSticker.description.isnot(None),
        )
        .order_by(SeenSticker.usage_count.desc(), SeenSticker.id.asc())
    )
    by_pack: dict[str, list[tuple[str, str, str]]] = {}
    for pack, emoji, desc, fuid, _uc in res.all():
        if not pack:
            continue
        lst = by_pack.setdefault(pack, [])
        if len(lst) < per_pack:
            lst.append((emoji or "", desc or "", fuid or ""))
    ranked = sorted(by_pack.items(), key=lambda kv: -len(kv[1]))
    return ranked[:pack_limit]


async def pick_smart(
    session: AsyncSession,
    *,
    emoji: str | None = None,
    description_hint: str | None = None,
    pack_hint: str | None = None,
    limit: int = 25,
) -> SeenSticker | None:
    """Smarter resolver that Claude can steer via emoji + description
    hint + pack hint. Any None filter is skipped. Final pick is random
    among the narrowed candidates, biased toward lower usage_count
    (freshness).
    """
    import random

    from sqlalchemy import or_

    clauses = []
    if emoji:
        # Strip ZWJ / variation selectors — match loose.
        stripped = "".join(
            ch for ch in emoji
            if not (0xFE00 <= ord(ch) <= 0xFE0F or ord(ch) == 0x200D)
        )
        clauses.append(
            or_(SeenSticker.emoji == emoji, SeenSticker.emoji == stripped)
        )
    if description_hint:
        clauses.append(SeenSticker.description.ilike(f"%{description_hint}%"))
    if pack_hint:
        clauses.append(SeenSticker.sticker_set.ilike(f"%{pack_hint}%"))

    q = select(SeenSticker).order_by(SeenSticker.usage_count.asc()).limit(limit)
    for c in clauses:
        q = q.where(c)

    res = await session.execute(q)
    rows = list(res.scalars().all())
    if not rows:
        return None
    # Weighted pick: favour top-half (less-used) with 70% probability.
    top_half = rows[: max(1, len(rows) // 2)]
    return random.choice(top_half if random.random() < 0.7 else rows)
