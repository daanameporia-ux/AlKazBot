"""Knowledge-base CRUD.

Spec §"Обучаемость" dictates:
- categories: entity | rule | pattern | preference | glossary | alias
- confidence: confirmed | inferred | tentative
- confidence auto-upgrades to `confirmed` if the same fact is added again.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase

CONFIDENCE_ORDER = {"tentative": 0, "inferred": 1, "confirmed": 2}
VALID_CATEGORIES = ("entity", "rule", "pattern", "preference", "glossary", "alias")


async def add_fact(
    session: AsyncSession,
    *,
    category: str,
    content: str,
    key: str | None = None,
    confidence: str = "confirmed",
    created_by_user_id: int | None = None,
    notes: str | None = None,
) -> KnowledgeBase:
    """Insert a new fact. If an identical fact (same category + key + content)
    already exists and is `is_active`, upgrade its confidence instead of
    creating a duplicate.
    """
    # Dedup heuristic — case-insensitive content match within (category, key?).
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.category == category,
        KnowledgeBase.is_active.is_(True),
        func.lower(KnowledgeBase.content) == content.lower(),
    )
    if key:
        stmt = stmt.where(KnowledgeBase.key == key)
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is not None:
        if CONFIDENCE_ORDER[confidence] > CONFIDENCE_ORDER[existing.confidence]:
            existing.confidence = confidence
        existing.usage_count = existing.usage_count + 1
        return existing

    fact = KnowledgeBase(
        category=category,
        key=key,
        content=content,
        confidence=confidence,
        created_by=created_by_user_id,
        notes=notes,
    )
    session.add(fact)
    await session.flush()
    return fact


async def deactivate(session: AsyncSession, fact_id: int) -> bool:
    res = await session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == fact_id, KnowledgeBase.is_active.is_(True))
        .values(is_active=False)
    )
    return (res.rowcount or 0) > 0


async def edit_content(session: AsyncSession, fact_id: int, new_content: str) -> bool:
    res = await session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == fact_id, KnowledgeBase.is_active.is_(True))
        .values(content=new_content)
    )
    return (res.rowcount or 0) > 0


async def list_facts(
    session: AsyncSession,
    *,
    min_confidence: str = "tentative",
    category: str | None = None,
    limit: int | None = None,
) -> list[KnowledgeBase]:
    threshold = CONFIDENCE_ORDER[min_confidence]
    stmt = select(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
    if category:
        stmt = stmt.where(KnowledgeBase.category == category)
    stmt = stmt.order_by(KnowledgeBase.category, KnowledgeBase.id)
    rows = list((await session.execute(stmt)).scalars().all())
    return [f for f in rows if CONFIDENCE_ORDER[f.confidence] >= threshold][: limit or None]


async def search(
    session: AsyncSession, query: str, *, limit: int = 20
) -> list[KnowledgeBase]:
    q = f"%{query.lower()}%"
    stmt = (
        select(KnowledgeBase)
        .where(
            KnowledgeBase.is_active.is_(True),
            or_(
                func.lower(KnowledgeBase.content).like(q),
                func.lower(KnowledgeBase.key).like(q),
            ),
        )
        .order_by(KnowledgeBase.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def mark_used(session: AsyncSession, fact_ids: list[int]) -> None:
    if not fact_ids:
        return
    await session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id.in_(fact_ids))
        .values(last_used=func.now(), usage_count=KnowledgeBase.usage_count + 1)
    )
