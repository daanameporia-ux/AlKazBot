"""LLM batch analyzer.

Takes a batch of chat messages (plus optional trigger message), asks Claude
to decompose them into a list of operation candidates, and returns the
structured result as a pydantic model.

The prompt is deliberately explicit about the business context — it leans on
`CORE_INSTRUCTIONS` from `system_prompt.py` via cache, then adds the
batch-specific instruction block.

Output schema (tool input):

    {
        "operations": [
            {
                "intent": "poa_withdrawal" | "exchange" | "expense" | ...,
                "confidence": 0.0..1.0,
                "source_message_ids": [123, 124],  # tg_message_id references
                "summary": "snятие 150k с Никонова",
                "fields": { ...intent-specific... },
                "ambiguities": ["кто из партнёров первая доля"]
            },
            ...
        ],
        "chat_only": true/false,  # true — ничего не парсить, это просто болтовня
        "notes": "optional free-text summary"
    }
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.bot.batcher import Batch
from src.llm.client import complete
from src.llm.schemas import Intent
from src.llm.system_prompt import build_system_blocks
from src.logging_setup import get_logger

log = get_logger(__name__)


class BatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_ids: list[int] = Field(default_factory=list)
    summary: str
    fields: dict[str, Any] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)


class BatchAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[BatchOperation] = Field(default_factory=list)
    chat_only: bool = False
    chat_reply: str | None = None
    notes: str | None = None


ANALYZE_TOOL = {
    "name": "analyze_batch",
    "description": (
        "Decompose a batch of Russian chat messages from the accounting team "
        "into a list of separate operation candidates. If the batch is pure "
        "chit-chat with no operations, set `chat_only=true` and return "
        "an empty `operations` list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": [i.value for i in Intent],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "source_message_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "tg_message_id values from the batch that "
                                "this operation is based on. Include the "
                                "ids you used so the bot can cite them."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short Russian sentence like 'Снятие 150k с Никонова'.",
                        },
                        "fields": {
                            "type": "object",
                            "description": (
                                "Intent-specific fields. Examples:\n"
                                "exchange: {amount_rub, amount_usdt, fx_rate}\n"
                                "expense: {category, amount_rub?, amount_usdt?, description}\n"
                                "partner_withdrawal: {partner, amount_usdt, from_wallet?}\n"
                                "partner_deposit: {partner, amount_usdt}\n"
                                "poa_withdrawal: {client_name, amount_rub, partner_shares:[{partner,pct}], client_share_pct}\n"
                                "cabinet_purchase: {name?, cost_rub, prepayment_ref?}\n"
                                "cabinet_worked_out: {name_or_code}\n"
                                "cabinet_blocked: {name_or_code}\n"
                                "prepayment_given: {supplier, amount_rub, expected_cabinets?}\n"
                                "prepayment_fulfilled: {supplier, cabinets:[{name,cost_rub}]}\n"
                                "wallet_snapshot: {tapbank?, mercurio?, rapira?, sber_balances?, cash?}\n"
                                "client_payout: {client_name, amount_usdt}"
                            ),
                        },
                        "ambiguities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Things you were unsure about. The bot will "
                                "ask the user before persisting if this is "
                                "non-empty OR if confidence < 0.7."
                            ),
                        },
                    },
                    "required": ["intent", "confidence", "summary"],
                },
            },
            "chat_only": {
                "type": "boolean",
                "description": "true if the batch has no operations to record.",
            },
            "chat_reply": {
                "type": "string",
                "description": (
                    "Free-text Russian reply to send back to the chat. "
                    "Required when chat_only=true AND the batch includes a "
                    "trigger message (a direct @-mention, a reply to the "
                    "bot, or a question). Must follow the personality/tone "
                    "spec. Leave empty when there's no trigger (passive "
                    "analysis should stay silent unless there are operations)."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Optional free-text commentary for the user.",
            },
        },
        "required": ["operations", "chat_only"],
    },
}


BATCH_INSTRUCTION = """\
# Batch analysis task

You receive a list of chat messages from the sber26 accounting team's group.
Each entry shows the Telegram message_id, author handle, and text. A batch
may contain multiple separate operations (e.g. one snятие + one exchange +
one payout), or may be pure chit-chat, or a direct question to the bot.

Possible sections in the input:
- `[trigger message ...]` — the message that forced the flush right now
  (an @-mention, reply to the bot, or slash-command). Treat it as the
  "current request" the user wants answered.
- Regular `[id=...]` entries — passive context that accumulated before.

For each operation-like statement, return a `BatchOperation` entry with:
- `intent` from the Intent enum
- `confidence` (low it if something is ambiguous)
- `source_message_ids` — the Telegram message ids that contributed to it
- `summary` — one-line Russian description for the preview card
- `fields` — the structured fields for that intent (see tool schema)
- `ambiguities` — what you'd ask the user before persisting

If there are no operations AND there IS a trigger message:
  - set `chat_only=true`
  - write the actual Russian reply into `chat_reply` (following the
    personality spec — по делу, не слащаво, допустимая лёгкая подъёбка)

If there are no operations AND there's no trigger (purely passive
analysis of buffered chit-chat): set `chat_only=true`, leave
`chat_reply` empty. The bot will stay silent.

Only return operations you are reasonably sure about. It's better to ask
than to invent. Low confidence (< 0.7) or non-empty `ambiguities` is
EXPECTED and welcome — the bot will ask the user before writing anything.
"""


def _format_batch(batch: Batch) -> str:
    parts = []
    if batch.trigger is not None:
        t = batch.trigger
        parts.append(
            f"[trigger message ({batch.trigger_kind}), id={t.tg_message_id}] "
            f"{t.display_name or t.tg_user_id}: {t.text}"
        )
    for m in batch.messages:
        parts.append(
            f"[id={m.tg_message_id}] {m.display_name or m.tg_user_id}: {m.text}"
        )
    return "\n".join(parts) if parts else "(empty batch)"


async def analyze_batch(
    batch: Batch,
    *,
    knowledge_items: list[dict] | None = None,
) -> BatchAnalysis:
    system_blocks = build_system_blocks(knowledge_items=knowledge_items)
    rendered = _format_batch(batch)
    user_prompt = f"{BATCH_INSTRUCTION}\n\nMessages:\n{rendered}"

    resp = await complete(
        system_blocks=system_blocks,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "analyze_batch"},
        max_tokens=2500,
        temperature=0.2,
    )

    payload: dict | None = None
    for block in resp.raw.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "analyze_batch":
            payload = block.input  # type: ignore[assignment]
            break

    if payload is None:
        log.warning("batch_analyzer_no_tool_use", size=len(batch.messages))
        return BatchAnalysis(operations=[], chat_only=True)

    try:
        result = BatchAnalysis.model_validate(payload)
    except Exception as e:
        log.warning("batch_analyzer_validation_failed", error=str(e))
        return BatchAnalysis(operations=[], chat_only=True)

    log.info(
        "batch_analyzer_result",
        size=len(batch.messages),
        n_ops=len(result.operations),
        chat_only=result.chat_only,
        cached_tokens=resp.cache_read_input_tokens,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
    )
    return result
