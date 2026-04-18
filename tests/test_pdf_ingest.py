"""PDF ingest helpers — Sber-statement detection."""

from __future__ import annotations

from src.core.pdf_ingest import SBER_HINT, is_sber_statement


def test_detects_sber_header() -> None:
    sample = """
    www.sberbank.ru
    ул. Вавилова, д. 19, Москва, 117312
    Выписка по платёжному счёту
    За период 08.04.2026 — 09.04.2026
    """
    assert is_sber_statement(sample) is True


def test_not_sber_random_text() -> None:
    assert is_sber_statement("Hello world, just a regular document") is False


def test_hint_mentions_key_semantics() -> None:
    assert "sber_balances" in SBER_HINT
    assert "Выдача наличных" in SBER_HINT
