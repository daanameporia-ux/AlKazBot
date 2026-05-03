"""Anthropic client wrapper with prompt caching and retries.

Key design points
-----------------
* System prompt is assembled as a list of cacheable blocks (instructions,
  knowledge-base, few-shot), then appended with a *non-cached* recent-messages
  block. See spec §"Обучаемость" → "Как бот использует базу".
* Caching uses `cache_control={"type": "ephemeral"}` on blocks that change
  rarely. Hit rate should stay high because the first three blocks only
  change when the knowledge base changes.
* Retries: transient 5xx / 429 → exponential back-off (tenacity). Total
  budget ~30 seconds.

The wrapper intentionally stays small; higher-level prompt assembly lives in
`src/llm/system_prompt.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import Message
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class LLMResponse:
    """Normalized LLM response exposed to the rest of the app."""

    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    raw: Message

    @property
    def cached_tokens(self) -> int:
        return self.cache_read_input_tokens


_client: anthropic.AsyncAnthropic | None = None

# Beta header needed for 1-hour cache TTL on cache_control blocks.
# Without the header, Anthropic silently treats the ttl hint as default
# 5-minute — we still work, just at regular cache cost.
_CACHE_BETA = "extended-cache-ttl-2025-04-11"


def get_client() -> anthropic.AsyncAnthropic:
    """Process-wide singleton — Anthropic SDK client is already thread-safe."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            default_headers={"anthropic-beta": _CACHE_BETA},
        )
    return _client


_RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


@retry(
    reraise=True,
    retry=retry_if_exception_type(_RETRYABLE),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    stop=stop_after_attempt(4),
)
async def complete(
    *,
    system_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.3,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | None = None,
    call_kind: str = "unknown",
) -> LLMResponse:
    """Low-level call to Anthropic `messages.create`.

    `system_blocks` is the pre-assembled list of system text blocks — callers
    (e.g. `src.llm.system_prompt`) are responsible for adding
    `cache_control={"type":"ephemeral"}` on the right blocks.

    `call_kind` is a free-form tag that goes into `llm_call` structured log
    so that we can attribute spend to its source (`batch_analyzer`,
    `classifier`, `free_text`, `vision`, etc.). Use the same string across
    all call-sites of the same logical caller.
    """
    client = get_client()
    target_model = model or settings.anthropic_model

    kwargs: dict[str, Any] = dict(
        model=target_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_blocks,
        messages=messages,
    )
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    resp: Message = await client.messages.create(**kwargs)

    # Extract the first text block if any (tool-use handling is caller-side).
    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "") or ""
            break

    usage = resp.usage
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cw_tok = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr_tok = getattr(usage, "cache_read_input_tokens", 0) or 0

    # Single source-of-truth log for spend attribution. Aggregator can
    # group by (model, call_kind) and multiply by per-token rates.
    # Don't log batch_analyzer separately — it has its own richer log
    # downstream; centralized here only for non-batch callers (classifier,
    # free_text, vision) so they're not invisible in usage charts.
    if call_kind != "batch_analyzer":
        log.info(
            "llm_call",
            model=resp.model,
            call_kind=call_kind,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_write_tokens=cw_tok,
            cache_read_tokens=cr_tok,
            has_tools=tools is not None,
            max_tokens=max_tokens,
            stop_reason=resp.stop_reason,
        )

    return LLMResponse(
        text=text,
        model=resp.model,
        stop_reason=resp.stop_reason,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_creation_input_tokens=cw_tok,
        cache_read_input_tokens=cr_tok,
        raw=resp,
    )
