"""Intent classifier.

Two-stage pipeline:
  1. `quick_classify` — regex pre-router. Cheap, no tokens. Handles the two
     most common unambiguous patterns: exchange formula "X/Y=Z" and the
     "эквайринг N" expense.
  2. `llm_classify` — Claude tool-use classifier for everything else.
     Returns a `ClassifiedIntent` with confidence 0.0-1.0.

If quick_classify matches — we skip the LLM entirely.
"""

from __future__ import annotations

import json
import re

from src.llm.client import complete
from src.llm.schemas import ClassifiedIntent, Intent
from src.llm.system_prompt import build_system_blocks
from src.logging_setup import get_logger

log = get_logger(__name__)


# `517000/6433=80.367` or `517000 / 6433 = 80,37`
EXCHANGE_RE = re.compile(
    r"(?P<rub>\d[\d\s]*)\s*/\s*(?P<usdt>\d[\d\s.,]*)\s*=\s*(?P<rate>\d[\d.,]*)",
)

# "эквайринг 5к", "эквайринг 5000"
ACQUIRING_RE = re.compile(r"(?i)\bэквайринг\b\s*(?P<amount>\d[\d\s.,]*к?)")


def quick_classify(text: str) -> Intent | None:
    """Return an intent if the message matches a cheap regex rule; else None.

    None means: "route to the LLM".
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("/"):
        return None  # commands handled elsewhere
    if EXCHANGE_RE.search(t):
        return Intent.EXCHANGE
    if ACQUIRING_RE.search(t):
        return Intent.EXPENSE
    return None


# --------------------------------------------------------------------------- #
# LLM classifier — Claude tool-use
# --------------------------------------------------------------------------- #

_INTENT_VALUES = [i.value for i in Intent]


CLASSIFY_TOOL = {
    "name": "classify_intent",
    "description": (
        "Classify a single user chat message into exactly one intent from the "
        "enumerated list. Also return a confidence score and a short reasoning."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": _INTENT_VALUES,
                "description": "The best-fitting intent.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "0.0 = guessing, 1.0 = certain.",
            },
            "reasoning": {
                "type": "string",
                "description": "One-sentence justification in Russian.",
            },
        },
        "required": ["intent", "confidence"],
    },
}


async def llm_classify(
    text: str,
    *,
    knowledge_items: list[dict] | None = None,
) -> ClassifiedIntent:
    """Ask Claude to classify the message into one of the Intent enum values.

    Использует Haiku — простой 23-way enum-роутинг, Sonnet здесь оверкилл
    (3x дешевле + быстрее на коротких ответах с tool-use).
    """
    from src.config import settings as _settings

    system_blocks = build_system_blocks(knowledge_items=knowledge_items)
    resp = await complete(
        system_blocks=system_blocks,
        messages=[{"role": "user", "content": text}],
        model=_settings.anthropic_fallback_model,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_intent"},
        max_tokens=400,
        temperature=0.0,
        call_kind="classifier",
    )

    # Find the tool_use block.
    payload: dict | None = None
    for block in resp.raw.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "classify_intent":
            payload = block.input  # type: ignore[assignment]
            break

    if payload is None:
        log.warning("classifier_no_tool_use", text=text[:120])
        return ClassifiedIntent(intent=Intent.UNCLEAR, confidence=0.0, reasoning="no tool_use")

    try:
        result = ClassifiedIntent.model_validate(payload)
    except Exception as e:
        log.warning("classifier_validation_failed", error=str(e), payload=json.dumps(payload))
        return ClassifiedIntent(
            intent=Intent.UNCLEAR, confidence=0.0, reasoning=f"validation: {e}"
        )

    log.info(
        "classifier_result",
        intent=result.intent.value,
        confidence=result.confidence,
        cached_tokens=resp.cache_read_input_tokens,
        input_tokens=resp.input_tokens,
    )
    return result
