"""Simple in-memory silence flag.

When silence is on, `batch_processor` skips sending any chat replies /
preview cards. APScheduler reminders and /commands are unaffected — only
proactive LLM-driven replies shut up.

Intentionally in-memory: the whole point is "just for a bit, stop chirping".
Survives normal message flow, gets reset on container restart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_silent_until: datetime | None = None


def is_silent() -> bool:
    if _silent_until is None:
        return False
    return datetime.now(UTC) < _silent_until


def set_silent(hours: float) -> datetime:
    global _silent_until
    _silent_until = datetime.now(UTC) + timedelta(hours=hours)
    return _silent_until


def clear_silent() -> None:
    global _silent_until
    _silent_until = None


def silence_remaining_minutes() -> int | None:
    if _silent_until is None:
        return None
    delta = _silent_until - datetime.now(UTC)
    if delta.total_seconds() < 0:
        return None
    return int(delta.total_seconds() // 60)
