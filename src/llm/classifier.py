"""Intent classifier. Stage 1 will flesh this out with an LLM tool call.

For Stage 0 we ship a regex pre-router so the bot handles obvious patterns
(exchange `X/Y=Z`, slash commands) without spending tokens.
"""

from __future__ import annotations

import re

from src.llm.schemas import Intent

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
        return None  # handled by command routers
    if EXCHANGE_RE.search(t):
        return Intent.EXCHANGE
    if ACQUIRING_RE.search(t):
        return Intent.EXPENSE
    return None
