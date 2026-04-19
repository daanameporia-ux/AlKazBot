"""PDF ingest gating — only explicit user requests parse into operations."""

from __future__ import annotations

import pytest
from src.core.pdf_ingest import (
    ALIEN_PDF_HINT,
    EXPLICIT_INGEST_TOKENS,
    SBER_HINT,
    has_explicit_ingest_request,
    is_sber_statement,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("запиши эти расходы в учёт", True),
        ("Внеси, пожалуйста, операции", True),
        ("оформи как expense", True),
        ("занеси в учёт", True),
        ("разбери выписку", False),
        ("посмотри что там", False),
        ("", False),
        (None, False),
    ],
)
def test_has_explicit_ingest_request(text, expected) -> None:
    assert has_explicit_ingest_request(text or "") is expected


def test_sber_statement_detection() -> None:
    assert is_sber_statement("Выписка по платёжному счёту, www.sberbank.ru")
    assert is_sber_statement("СберБанк, 2026 год")
    assert not is_sber_statement("Yandex Bank Statement")
    assert not is_sber_statement("")


def test_hints_are_strict_about_default_no_parse() -> None:
    assert "operations=[]" in SBER_HINT
    assert "operations=[]" in ALIEN_PDF_HINT
    assert "не парсим" in ALIEN_PDF_HINT.lower()


def test_explicit_tokens_are_short() -> None:
    # Guard against accidentally bloating the trigger vocabulary.
    assert len(EXPLICIT_INGEST_TOKENS) <= 20
    for t in EXPLICIT_INGEST_TOKENS:
        assert t == t.lower()
