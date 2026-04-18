"""Few-shot examples — verified (intent, input_text, parsed_json) triples.

When a user presses ✅ on a preview card we save the pairing here. On
future analyses the LLM gets a handful of verified examples for the
relevant intent injected into its system prompt (see `src/llm/system_prompt.py`
-> render_few_shot).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import FewShotExample


async def add_verified(
    session: AsyncSession,
    *,
    intent: str,
    input_text: str,
    parsed_json: dict[str, Any],
) -> FewShotExample:
    e = FewShotExample(
        intent=intent,
        input_text=input_text[:4000],  # cap to keep table sane
        parsed_json=parsed_json,
        verified=True,
    )
    session.add(e)
    await session.flush()
    return e


async def list_for_intent(
    session: AsyncSession, intent: str, *, limit: int = 5
) -> list[FewShotExample]:
    res = await session.execute(
        select(FewShotExample)
        .where(
            FewShotExample.intent == intent,
            FewShotExample.verified.is_(True),
        )
        .order_by(FewShotExample.used_count.asc(), FewShotExample.id.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def bump_usage(session: AsyncSession, ids: list[int]) -> None:
    if not ids:
        return
    await session.execute(
        update(FewShotExample)
        .where(FewShotExample.id.in_(ids))
        .values(used_count=FewShotExample.used_count + 1)
    )


async def count(session: AsyncSession) -> int:
    res = await session.execute(select(func.count(FewShotExample.id)))
    return int(res.scalar_one() or 0)
