"""Fuzzy dedup for knowledge_base add_fact.

The seed + LLM-driven teach paths easily produce near-duplicates like
"Рапира биржа" and "Рапира — биржа". We match with SequenceMatcher
≥0.85 to merge them instead of creating clones.
"""

from __future__ import annotations

from src.db.repositories.knowledge import _similar


def test_exact_match_similarity_is_one() -> None:
    assert _similar("Рапира — биржа", "Рапира — биржа") == 1.0


def test_close_variant_above_threshold() -> None:
    # The two real prod duplicates (ids #5 and #7).
    a = "Рапира — биржа обмена наличного рубля на USDT"
    b = "Рапира — биржа обмена наличного рубля на USDT."
    assert _similar(a, b) >= 0.85


def test_different_facts_below_threshold() -> None:
    assert _similar(
        "эквайринг обычно ежедневно ~5000₽",
        "POA: 35% делится между партнёрами, пропорции каждый раз разные",
    ) < 0.5


def test_case_and_whitespace_insensitive() -> None:
    # Whitespace / case variations still merge.
    assert _similar("  hello world  ", "Hello World") >= 0.85
