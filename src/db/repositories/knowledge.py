"""Knowledge-base CRUD.

Spec §"Обучаемость" dictates:
- categories: entity | rule | pattern | preference | glossary | alias
- confidence: confirmed | inferred | tentative
- confidence auto-upgrades to `confirmed` if the same fact is added again.

Dedup strategy:
- Exact match (case-insensitive content + same category/key) — merge.
- Fuzzy match (similarity ≥ FUZZY_DEDUP_THRESHOLD on content within
  category + same key) — merge. Catches "Рапира биржа" vs "Рапира — биржа".
"""

from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase

CONFIDENCE_ORDER = {"tentative": 0, "inferred": 1, "confirmed": 2}
VALID_CATEGORIES = ("entity", "rule", "pattern", "preference", "glossary", "alias")

# Two facts are "the same fact" if their lowercased content is ≥85% similar
# by Ratcliff-Obershelp. High enough that "короткая форма" vs "короткая
# форма." merge, low enough that genuinely different facts don't collide.
FUZZY_DEDUP_THRESHOLD = 0.85


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


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
    """Insert a new fact with two-stage dedup.

    Stage 1 — exact content match (case-insensitive) within category/key:
              bump confidence + usage_count and return.
    Stage 2 — fuzzy content match (ratio ≥ FUZZY_DEDUP_THRESHOLD) within
              category + same key: merge, preserving the longer content
              and higher confidence.
    Stage 3 — insert as new row.
    """
    # Stage 1: exact match
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

    # Stage 2: fuzzy match within same category + same (or both NULL) key
    fuzzy_stmt = select(KnowledgeBase).where(
        KnowledgeBase.category == category,
        KnowledgeBase.is_active.is_(True),
    )
    if key:
        fuzzy_stmt = fuzzy_stmt.where(KnowledgeBase.key == key)
    else:
        fuzzy_stmt = fuzzy_stmt.where(KnowledgeBase.key.is_(None))
    candidates = list((await session.execute(fuzzy_stmt)).scalars().all())
    for cand in candidates:
        if _similar(cand.content, content) >= FUZZY_DEDUP_THRESHOLD:
            # Keep the longer / more-detailed content.
            if len(content) > len(cand.content):
                cand.content = content
            if CONFIDENCE_ORDER[confidence] > CONFIDENCE_ORDER[cand.confidence]:
                cand.confidence = confidence
            cand.usage_count = cand.usage_count + 1
            return cand

    # Stage 3: new row
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
    only_kernel: bool = False,
) -> list[KnowledgeBase]:
    """List active KB facts.

    `only_kernel=True` filters to `always_inject=true` rows — the small
    set of canonical rules/aliases that go into the cached system prompt
    every call. The rest are loaded lazily via :func:`lookup_for_text`.
    """
    threshold = CONFIDENCE_ORDER[min_confidence]
    stmt = select(KnowledgeBase).where(KnowledgeBase.is_active.is_(True))
    if only_kernel:
        stmt = stmt.where(KnowledgeBase.always_inject.is_(True))
    if category:
        stmt = stmt.where(KnowledgeBase.category == category)
    stmt = stmt.order_by(KnowledgeBase.category, KnowledgeBase.id)
    rows = list((await session.execute(stmt)).scalars().all())
    return [f for f in rows if CONFIDENCE_ORDER[f.confidence] >= threshold][: limit or None]


async def lookup_for_text(
    session: AsyncSession,
    text: str,
    *,
    limit: int = 12,
    min_confidence: str = "inferred",
) -> list[KnowledgeBase]:
    """Lazy-load: find non-kernel KB facts whose key/content overlaps with
    the given batch text. Used to inject relevant справочные facts into
    the uncached prompt tail without paying for them in cache writes.

    Match is by key substring OR significant content-word overlap.
    Skips rows with `always_inject=true` — they're already in the
    cached kernel block and would just duplicate.
    """
    if not text or not text.strip():
        return []
    lo = text.lower()

    threshold = CONFIDENCE_ORDER[min_confidence]
    stmt = (
        select(KnowledgeBase)
        .where(
            KnowledgeBase.is_active.is_(True),
            KnowledgeBase.always_inject.is_(False),
        )
        .order_by(KnowledgeBase.id.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())

    hits: list[KnowledgeBase] = []
    for r in rows:
        if CONFIDENCE_ORDER[r.confidence] < threshold:
            continue
        # Match if key (whole word) appears in batch OR any 4+ char token
        # from key appears, OR a notable noun from content appears.
        matched = False
        if r.key:
            klow = r.key.lower()
            if klow in lo:
                matched = True
            else:
                for tok in klow.replace("-", " ").replace("_", " ").split():
                    if len(tok) >= 4 and tok in lo:
                        matched = True
                        break
        if not matched:
            # Content-side: pick capitalised words (likely names/entities).
            for tok in r.content.split():
                stripped = tok.strip(".,;:()[]{}«»\"'!?")
                if (
                    len(stripped) >= 4
                    and stripped[0].isupper()
                    and stripped.lower() in lo
                ):
                    matched = True
                    break
        if matched:
            hits.append(r)
            if len(hits) >= limit:
                break
    return hits


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
