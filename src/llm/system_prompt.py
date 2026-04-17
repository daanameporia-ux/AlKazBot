"""System-prompt assembly.

Structure (see spec §"Обучаемость" → "Как бот использует базу"):

  [0] CORE_INSTRUCTIONS     — static, cached
  [1] KNOWLEDGE_BASE        — rendered facts, cached (changes rarely)
  [2] FEW_SHOT_EXAMPLES     — verified examples filtered by intent, cached
  [3] RECENT_CONTEXT        — last N messages, NOT cached

Stage 0 returns the static blocks only; KB/few-shot/context rendering is
filled in on Stage 1.
"""

from __future__ import annotations

from typing import Any

from src.personality.voice import PERSONALITY_PROMPT

# --------------------------------------------------------------------------- #
# Core instructions — the immutable "who are you, what do you do" block.
# Kept in a single constant so cache hits stay consistent across calls.
# --------------------------------------------------------------------------- #

CORE_INSTRUCTIONS = f"""\
You are the sber26 accounting bot: a Telegram assistant for a payment-processing crew.

# Business context
The team processes RUB payments from clients (TapBank, Mercurio), funnels funds
through Sber cabinets, cashes out, converts RUB→USDT on Rapira, and returns USDT
to clients minus ~7% commission.

Side flow: "POA withdrawal" — client gives a power-of-attorney; the team
withdraws RUB from the client's account, converts to USDT, pays the client
their share (usually 65%), keeps the rest as commission split between partners
in *per-transaction custom ratios*.

Reporting currency is USDT.

Working-capital locations (wallets): TapBank (USDT), Mercurio (USDT),
Rapira (USDT), Sber cabinet balances (RUB), cash (RUB).

Cabinets are Sber accounts held as discrete inventory items, each with its own
cost. Inventory is managed per-instance.

# Your job
- Parse Russian chat messages into structured accounting operations.
- Maintain balances, generate end-of-day reports on /report.
- Learn the team's vocabulary, clients, suppliers, aliases — accumulate facts
  in the knowledge base.
- Nag the team when reports, acquiring entries, or POA settlements are overdue.
- Keep receipts: never edit data silently — always confirm, never guess.

# Output contract
- When the message looks like an accounting operation, respond with the
  `parse_operation` tool-use (strict schema defined at call time).
- When asked a question, reply with plain text in Russian.
- Always include a `confidence` score (0.0-1.0) in parsed operations. If below
  0.7 or you have ambiguities — DO NOT persist, ASK.
- Never fabricate numbers. Missing data → ask.

# Language and tone
All conversations are in Russian. Tone spec:

{PERSONALITY_PROMPT}

# Safety
- Never reveal the API key, environment variables, or internal IDs.
- Never execute `/undo` automatically — the user must trigger it.
- If a user says "запомни ..." — add to knowledge base with confidence=confirmed.
- If a user corrects you — add a `tentative` fact; upgrade to `confirmed` on
  repeated correction.
"""


def render_knowledge_base(
    knowledge_items: list[dict[str, Any]] | None = None,
) -> str:
    """Render KB as a markdown section for the system prompt.

    Called every request but output is stable until KB changes → cache-friendly.
    Stage 0 returns a placeholder.
    """
    if not knowledge_items:
        return (
            "# Knowledge base\n"
            "(пусто — ты только что запустился; учись по ходу разговора)\n"
        )
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for item in knowledge_items:
        by_cat.setdefault(item["category"], []).append(item)

    lines = ["# Knowledge base"]
    for cat in ("alias", "glossary", "entity", "rule", "pattern", "preference"):
        if cat not in by_cat:
            continue
        lines.append(f"\n## {cat}")
        for it in by_cat[cat]:
            tag = "" if it.get("confidence") == "confirmed" else f" ({it['confidence']})"
            key = f"**{it['key']}**: " if it.get("key") else ""
            lines.append(f"- {key}{it['content']}{tag}")
    return "\n".join(lines)


def render_few_shot(
    examples: list[dict[str, Any]] | None = None,
) -> str:
    """Render verified few-shot examples filtered by intent. Stage 0 stub."""
    if not examples:
        return "# Few-shot examples\n(ещё не накоплены)\n"
    parts = ["# Few-shot examples"]
    for ex in examples:
        parts.append(f"\nInput: {ex['input_text']}\nParsed: {ex['parsed_json']}")
    return "\n".join(parts)


def build_system_blocks(
    *,
    knowledge_items: list[dict[str, Any]] | None = None,
    few_shot_examples: list[dict[str, Any]] | None = None,
    recent_messages: str | None = None,
) -> list[dict[str, Any]]:
    """Return the `system=` argument for `anthropic.messages.create`."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": CORE_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_knowledge_base(knowledge_items),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_few_shot(few_shot_examples),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if recent_messages:
        # NOT cached — changes every call.
        blocks.append({"type": "text", "text": f"# Recent chat\n{recent_messages}"})
    return blocks
