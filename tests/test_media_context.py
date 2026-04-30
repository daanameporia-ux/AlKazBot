from __future__ import annotations

from src.core.media_context import asks_for_recent_media


def test_media_request_requires_clear_media_reference() -> None:
    assert asks_for_recent_media("разбери PDF выше") is True
    assert asks_for_recent_media("глянь скрин, что там") is True
    assert asks_for_recent_media("разбери выше, что я скинул") is True
    assert asks_for_recent_media("просто болтаем") is False
    assert asks_for_recent_media("pdf лежит в папке") is False


def test_trigger_media_is_enough_after_handler_gate() -> None:
    assert asks_for_recent_media("@bot", trigger_has_media=True) is True
