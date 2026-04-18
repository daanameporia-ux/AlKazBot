"""/silent — toggle and remaining time."""

from __future__ import annotations

from src.core import silent


def setup_function(_: object) -> None:
    silent.clear_silent()


def test_default_is_not_silent() -> None:
    assert silent.is_silent() is False
    assert silent.silence_remaining_minutes() is None


def test_set_and_clear() -> None:
    silent.set_silent(1.0)
    assert silent.is_silent() is True
    remaining = silent.silence_remaining_minutes()
    assert remaining is not None
    assert 50 <= remaining <= 60
    silent.clear_silent()
    assert silent.is_silent() is False


def test_set_overwrite() -> None:
    silent.set_silent(0.5)
    silent.set_silent(2.0)
    remaining = silent.silence_remaining_minutes()
    assert remaining is not None
    assert remaining > 60  # second call extended the window
