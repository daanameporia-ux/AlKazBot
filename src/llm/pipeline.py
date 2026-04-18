"""End-to-end message pipeline: classify → parse → confirm / persist.

Stage 1 wires classify + `QUESTION`/`CHAT` text answers only. Parsers for
structured ops land next. The function is intentionally small so the same
entry point can be reused from @-mention handler, from the catch-all message
handler (when in hybrid listen mode), and from tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.db.repositories import knowledge as kb_repo
from src.db.session import session_scope
from src.llm.classifier import llm_classify, quick_classify
from src.llm.client import complete
from src.llm.schemas import ClassifiedIntent, Intent
from src.llm.system_prompt import build_system_blocks
from src.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class PipelineResult:
    intent: Intent
    confidence: float
    reply_text: str | None
    needs_confirmation: bool = False


async def _load_kb_items() -> list[dict]:
    async with session_scope() as session:
        facts = await kb_repo.list_facts(session, min_confidence="inferred")
    return [
        {
            "id": f.id,
            "category": f.category,
            "key": f.key,
            "content": f.content,
            "confidence": f.confidence,
        }
        for f in facts
    ]


async def answer_free_text(message_text: str, *, knowledge_items: list[dict]) -> str:
    """LLM free-form reply for `QUESTION` / `CHAT` / `UNCLEAR` intents."""
    system_blocks = build_system_blocks(knowledge_items=knowledge_items)
    resp = await complete(
        system_blocks=system_blocks,
        messages=[{"role": "user", "content": message_text}],
        max_tokens=600,
        temperature=0.6,
    )
    return resp.text.strip() or "…"


async def process_message(text: str) -> PipelineResult:
    """Classify and produce a reply.

    Parser / confirm flows for accounting operations are appended here on
    subsequent Stage 1 sub-tasks. For now we only answer free-text intents.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return PipelineResult(intent=Intent.UNCLEAR, confidence=0.0, reply_text=None)

    quick = quick_classify(cleaned)
    if quick is not None:
        # Parser for these intents arrives in the next commit. For now we
        # acknowledge so the user sees the bot is alive.
        return PipelineResult(
            intent=quick,
            confidence=1.0,
            reply_text=(
                f"Похоже на `{quick.value}` — парсер этой операции приедет "
                f"следующим коммитом Этапа 1."
            ),
        )

    kb_items = await _load_kb_items()

    classified: ClassifiedIntent = await llm_classify(cleaned, knowledge_items=kb_items)

    if classified.intent in (Intent.QUESTION, Intent.CHAT, Intent.UNCLEAR):
        reply = await answer_free_text(cleaned, knowledge_items=kb_items)
        return PipelineResult(
            intent=classified.intent,
            confidence=classified.confidence,
            reply_text=reply,
        )

    # Accounting-operation intents — parsers land next commit. Stub reply.
    return PipelineResult(
        intent=classified.intent,
        confidence=classified.confidence,
        reply_text=(
            f"Понял как `{classified.intent.value}` "
            f"(confidence={classified.confidence:.2f}). "
            "Парсер этой операции приедет следующим коммитом Этапа 1."
        ),
    )
