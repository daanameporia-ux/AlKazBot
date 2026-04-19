"""Quiet hours check — reminders should mute overnight (19:00–06:00 UTC).

The team lives in Moscow time (MSK = UTC+3), so quiet window is 22:00 —
09:00 local.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.reminders import _in_quiet_window


def _at(hour: int) -> datetime:
    return datetime(2026, 4, 19, hour, 0, tzinfo=UTC)


def test_midday_is_not_quiet() -> None:
    assert _in_quiet_window(_at(12)) is False
    assert _in_quiet_window(_at(9)) is False


def test_evening_after_19_utc_is_quiet() -> None:
    assert _in_quiet_window(_at(19)) is True
    assert _in_quiet_window(_at(22)) is True


def test_night_is_quiet() -> None:
    assert _in_quiet_window(_at(0)) is True
    assert _in_quiet_window(_at(3)) is True
    assert _in_quiet_window(_at(5)) is True


def test_06_utc_is_no_longer_quiet() -> None:
    assert _in_quiet_window(_at(6)) is False
