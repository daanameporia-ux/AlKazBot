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
from sqlalchemy import select

from src.bot.batcher import Batch
from src.db.models import MessageLog
from src.db.repositories import few_shot as few_shot_repo
from src.db.repositories import stickers as sticker_repo
from src.db.session import session_scope
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
    sticker_emoji: str | None = None
    sticker_description_hint: str | None = None
    sticker_pack_hint: str | None = None
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
                                "client_payout: {client_name, amount_usdt}\n"
                                "knowledge_teach: {category: 'alias'|'glossary'|'entity'|'rule'|'pattern'|'preference', key?, content}\n"
                                "  - alias: key=короткая форма, content=канон (\"Арнелле\" → acquiring)\n"
                                "  - entity: key=имя, content=описание (клиент, поставщик)\n"
                                "  - rule: content=правило бизнеса\n"
                                "  - glossary: key=термин, content=значение\n"
                                "  - pattern: content=типовая формулировка\n"
                                "  - preference: content=как юзер хочет чтобы бот работал\n"
                                "  Можно возвращать НЕСКОЛЬКО knowledge_teach в одном batch если юзер накинул несколько фактов."
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
            "sticker_emoji": {
                "type": "string",
                "description": (
                    "Optional emoji label to narrow sticker pick. Matches "
                    "exactly (with variation-selector/ZWJ stripping). Pick "
                    "from the spectrum listed in the 'Стикеры' system "
                    "block."
                ),
            },
            "sticker_description_hint": {
                "type": "string",
                "description": (
                    "Optional free-text substring matched (case-insensitive, "
                    "ILIKE '%...%') against the Vision-generated "
                    "`description` column of `seen_stickers`. The field is "
                    "Russian, so send a Russian noun/verb (e.g. 'офис', "
                    "'деньги', 'устал', 'кот', 'мешок'). Combine with "
                    "`sticker_emoji` for narrower picks or use alone when "
                    "no obvious emoji fits the mood. Read the '## Каталог "
                    "по сюжету' section of the 'Стикеры' block to see what "
                    "descriptions are available."
                ),
            },
            "sticker_pack_hint": {
                "type": "string",
                "description": (
                    "Optional substring of a pack name to restrict the "
                    "pick to a specific pack (e.g. 'kontorapidarasov'). "
                    "Use when you specifically want the feel of one pack."
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
one payout), or may be pure chit-chat, or a direct question to the bot,
or a teaching command ("запомни ...").

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

## PDF / банковские выписки — НЕ автопарсить в операции

Если в батче пришёл `trigger_kind=document` (юзер прислал PDF), по
умолчанию **НЕ создавай операции** из содержимого. Верни
`operations=[]` и в `chat_reply` дай короткую сводку: что за
документ, ключевые цифры (итого пришло / ушло / остаток, диапазон
дат), необычное. Весь текст документа остаётся в recent_history —
юзер может задать follow-up вопросы, отвечай по нему.

Парсить в операции разрешается ТОЛЬКО если юзер явно попросил:
словами «запиши», «внеси», «оформи операции», «занеси в учёт»,
«создай wallet_snapshot» и т.п. (может быть в том же сообщении с
PDF, или в следующем триггере). Без явного запроса — молчи про
операции, отвечай текстом. См. также SBER_HINT, встраиваемый в PDF
сбера — там детали по разметке строк.

## Teaching (`knowledge_teach`) specifics

When a user writes something like "запомни: X", "запомни что X", or just
a plain statement-of-fact about the business ("Миша обычно 22-28к за
кабинет", "Tpay это TapBank", "эквайринг 5к у нас ежедневно") —
decompose it into one or MORE `knowledge_teach` entries.

Pick the right `category`:
- **alias**: two names for the same thing. `key` = short/colloquial,
  `content` = canonical. Example: key="Арнелле", content="оплата за
  эквайринг (acquiring)".
- **entity**: a person / supplier / client / specific object.
  `key` = name, `content` = description ("приходит раз в 2 недели, суммы
  50-150к").
- **rule**: business rule. No key needed, just `content`.
- **glossary**: term → definition.
- **pattern**: typical phrasing. No key, `content` = the pattern.
- **preference**: how the user wants the bot to behave.

If the user cites several facts in one message, split them into
separate `knowledge_teach` operations. Each one gets its own preview
card so the user can ✅ / ❌ individually.

If you're unsure between categories or couldn't pull a crisp `key` /
`content`, set `confidence < 0.7` and list the exact clarifying
questions in `ambiguities`. The bot will ask first.

## General

Only return operations you are reasonably sure about. It's better to ask
than to invent. Low confidence (< 0.7) or non-empty `ambiguities` is
EXPECTED and welcome — the bot will ask the user before writing anything.
"""


def _format_batch(batch: Batch) -> str:
    parts = []
    if batch.trigger is not None:
        t = batch.trigger
        text = t.text
        voice_note = ""
        if text.startswith("[voice]"):
            text = text.removeprefix("[voice]").strip()
            voice_note = " (транскрипция голосового)"
        parts.append(
            f"[trigger message ({batch.trigger_kind}){voice_note}, "
            f"id={t.tg_message_id}] {t.display_name or t.tg_user_id}: {text}"
        )
    for m in batch.messages:
        text = m.text
        voice_note = ""
        if text.startswith("[voice]"):
            text = text.removeprefix("[voice]").strip()
            voice_note = " (голосовым)"
        parts.append(
            f"[id={m.tg_message_id}] {m.display_name or m.tg_user_id}{voice_note}: {text}"
        )
    return "\n".join(parts) if parts else "(empty batch)"


RECENT_HISTORY_WINDOW = 30
FEW_SHOT_PER_INTENT = 2
FEW_SHOT_INTENTS = (
    Intent.POA_WITHDRAWAL,
    Intent.EXCHANGE,
    Intent.EXPENSE,
    Intent.PARTNER_WITHDRAWAL,
    Intent.PARTNER_DEPOSIT,
    Intent.CABINET_PURCHASE,
    Intent.CABINET_WORKED_OUT,
    Intent.WALLET_SNAPSHOT,
)


async def _collect_few_shot() -> list[dict[str, Any]]:
    async with session_scope() as session:
        out: list[dict[str, Any]] = []
        for intent in FEW_SHOT_INTENTS:
            rows = await few_shot_repo.list_for_intent(
                session, intent.value, limit=FEW_SHOT_PER_INTENT
            )
            for r in rows:
                out.append(
                    {
                        "intent": r.intent,
                        "input_text": r.input_text,
                        "parsed_json": r.parsed_json,
                    }
                )
    return out


async def _collect_sticker_context() -> tuple[
    list[tuple[str, list[str]]],
    list[tuple[str, list[tuple[str, str, str]]]],
    list[dict[str, Any]],
]:
    """Pull (pack_emoji_summary, described_catalog, usage_examples) for
    the Stickers system block. Each element may be empty; caller handles
    empty-safe rendering.
    """
    async with session_scope() as session:
        packs = await sticker_repo.pack_emoji_summary(session, pack_limit=10)
        catalog = await sticker_repo.described_catalog(
            session, per_pack=20, pack_limit=10
        )
        rows = await sticker_repo.recent_usage_examples(
            session, limit=10, humans_only=True
        )
    examples = [
        {
            "who": str(r.tg_user_id) if r.tg_user_id else "?",
            "emoji": r.emoji or "?",
            "pack": r.sticker_set,
            "preceding_text": r.preceding_text,
        }
        for r in rows
    ]
    return packs, catalog, examples


async def _recent_history(chat_id: int, exclude_ids: set[int]) -> str:
    """Pull last N messages from message_log (including bot replies) so the
    analyzer has conversation context. Messages that are already part of
    the current batch are excluded to avoid double-quoting.

    Voice transcripts (intent_detected='voice_transcript') are formatted
    with an explicit 'voice from user' marker — Claude otherwise saw
    `[voice] ...` and thought it was a stub without content.
    """
    async with session_scope() as session:
        res = await session.execute(
            select(MessageLog)
            .where(MessageLog.chat_id == chat_id)
            .order_by(MessageLog.id.desc())
            .limit(RECENT_HISTORY_WINDOW)
        )
        rows = list(res.scalars().all())
    rows.reverse()  # chronological ascending
    lines: list[str] = []
    for r in rows:
        if r.tg_message_id and r.tg_message_id in exclude_ids:
            continue
        if not r.text:
            continue
        who = "бот" if r.is_bot else (str(r.tg_user_id) if r.tg_user_id else "?")
        text = r.text[:500]
        if r.intent_detected == "voice_transcript" and text.startswith("[voice]"):
            # Strip the [voice] prefix and make the origin explicit.
            stripped = text.removeprefix("[voice]").strip()
            lines.append(
                f"  [id={r.tg_message_id}] {who} (голосовым): {stripped}"
            )
        else:
            lines.append(f"  [id={r.tg_message_id}] {who}: {text}")
    if not lines:
        return ""
    return "# Контекст чата (последние сообщения)\n" + "\n".join(lines)


async def analyze_batch(
    batch: Batch,
    *,
    knowledge_items: list[dict] | None = None,
) -> BatchAnalysis:
    rendered = _format_batch(batch)

    # Pull conversation history — everything except the messages that are
    # already inside `batch` (the analyzer would otherwise see them twice).
    batch_ids: set[int] = set()
    if batch.trigger:
        batch_ids.add(batch.trigger.tg_message_id)
    batch_ids.update(m.tg_message_id for m in batch.messages)
    recent_history = await _recent_history(batch.chat_id, batch_ids)

    # Pull a mix of verified examples across the most likely intents.
    few_shot_items = await _collect_few_shot()

    # Sticker library + usage examples so Claude knows which emojis are
    # actually resolvable, sees Vision descriptions for picking by meaning,
    # and learns from recent human usage.
    (
        sticker_packs,
        sticker_catalog,
        sticker_examples,
    ) = await _collect_sticker_context()

    # `recent_history` is the non-cached last system block — it changes every
    # request, so we keep the cached sections ahead of it.
    system_blocks = build_system_blocks(
        knowledge_items=knowledge_items,
        few_shot_examples=few_shot_items,
        sticker_pack_emojis=sticker_packs,
        sticker_described_catalog=sticker_catalog,
        sticker_usage_examples=sticker_examples,
        recent_messages=recent_history or None,
    )

    user_prompt = f"{BATCH_INSTRUCTION}\n\nMessages to analyze now:\n{rendered}"

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
