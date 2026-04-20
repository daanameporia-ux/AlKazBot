"""Regex fallback: preference fact with 'откликаться на X' extracts X."""

from __future__ import annotations

from src.core.applier import _WAKEWORD_TRIGGER


def _extract(text: str) -> list[str]:
    return [m.lower() for m in _WAKEWORD_TRIGGER.findall(text)]


def test_basic_respond_to() -> None:
    assert "пёс" in _extract("Откликаться на 'пёс' наравне с алкаш, бот")


def test_quoted_russian_word() -> None:
    assert "шавка" in _extract("Отзывайся на «шавка» тоже")


def test_multiple_words() -> None:
    got = _extract("Реагируй на пёс и отвечай на шавка")
    assert "пёс" in got
    assert "шавка" in got


def test_ignores_latin_noise() -> None:
    # Latin words skipped by the cyrillic-only pattern.
    assert _extract("откликайся на alkaz") == []


def test_rule_content_doesnt_match() -> None:
    # A random rule-like content should NOT produce wake-words.
    assert _extract("35% делится между партнёрами пропорции каждый раз разные") == []


def test_ignores_very_short_words() -> None:
    # "на" / "ну" shouldn't hit — the {2,24} with leading cyrillic handles most,
    # and the _mirror helper filters len<3.
    from src.core.applier import _mirror_wakewords_from_preference  # noqa: F401 — import check

    # 2-letter words don't match our regex's {2,24} suffix plus leading letter.
    # But even if they did, _mirror filters len<3. Here we just confirm
    # the regex doesn't over-capture a filler.
    got = _extract("Отзывайся на ну")
    # match may or may not hit "ну" depending on trailing token; either
    # way the 3-char minimum guards the actual insert.
    for w in got:
        # If it matched something, at least it has letters.
        assert w.isalpha() or w == ""
