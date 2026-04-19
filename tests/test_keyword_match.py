"""Local keyword matcher — substring, case-insensitive, no LLM."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from src.core import keyword_match


@pytest.fixture(autouse=True)
def reset_cache():
    keyword_match._cache = []
    keyword_match._cache_loaded_at = 0.0
    yield
    keyword_match._cache = []
    keyword_match._cache_loaded_at = 0.0


async def _with_keywords(words: list[str], fn):
    with patch.object(keyword_match, "_load_cache", return_value=words):
        return await fn()


async def test_no_keywords_no_hit() -> None:
    async def run():
        return await keyword_match.find_hits("обычное сообщение без триггеров")

    hits = await _with_keywords([], run)
    assert hits == []


async def test_simple_substring_hit() -> None:
    async def run():
        return await keyword_match.find_hits("Спросите у бухгалтера")

    hits = await _with_keywords(["бухгалтер"], run)
    assert hits == ["бухгалтер"]


async def test_case_insensitive() -> None:
    async def run():
        return await keyword_match.find_hits("БОТ, ты тут?")

    hits = await _with_keywords(["бот"], run)
    assert hits == ["бот"]


async def test_substring_inside_word() -> None:
    # "бот" should match "ботяра" / "Арбузбот"
    async def run():
        return await keyword_match.find_hits("Ай ботяра, пришёл?")

    hits = await _with_keywords(["бот"], run)
    assert hits == ["бот"]


async def test_multiple_hits_returned() -> None:
    async def run():
        return await keyword_match.find_hits(
            "Бухгалтер, где бот? Цифровой пидорас зовёт."
        )

    hits = await _with_keywords(
        ["бот", "бухгалтер", "цифровой пидорас"], run
    )
    assert set(hits) == {"бот", "бухгалтер", "цифровой пидорас"}


async def test_has_trigger_true_false() -> None:
    async def run_yes():
        return await keyword_match.has_trigger("бот иди сюда")

    async def run_no():
        return await keyword_match.has_trigger("привет как дела")

    assert await _with_keywords(["бот"], run_yes) is True
    assert await _with_keywords(["бот"], run_no) is False


async def test_empty_text_no_hits() -> None:
    async def run():
        return await keyword_match.find_hits("")

    hits = await _with_keywords(["бот"], run)
    assert hits == []
