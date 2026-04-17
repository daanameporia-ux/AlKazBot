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


def get_client() -> anthropic.AsyncAnthropic:
    """Process-wide singleton — Anthropic SDK client is already thread-safe."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
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
) -> LLMResponse:
    """Low-level call to Anthropic `messages.create`.

    `system_blocks` is the pre-assembled list of system text blocks — callers
    (e.g. `src.llm.system_prompt`) are responsible for adding
    `cache_control={"type":"ephemeral"}` on the right blocks.
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
    return LLMResponse(
        text=text,
        model=resp.model,
        stop_reason=resp.stop_reason,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        raw=resp,
    )
