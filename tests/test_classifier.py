"""Quick smoke tests for the regex pre-router."""

from __future__ import annotations

import pytest
from src.llm.classifier import quick_classify
from src.llm.schemas import Intent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("517000/6433=80.367", Intent.EXCHANGE),
        ("517000 / 6433 = 80,37", Intent.EXCHANGE),
        ("эквайринг 5к", Intent.EXPENSE),
        ("эквайринг 5000", Intent.EXPENSE),
        ("/report", None),  # handled by command router
        ("доброе утро", None),
        ("", None),
    ],
)
def test_quick_classify(text: str, expected: Intent | None) -> None:
    assert quick_classify(text) is expected
