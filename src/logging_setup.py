"""structlog configuration — human-readable in dev, JSON in prod.

Includes a redacting processor that strips common secret fragments from
every log event so an accidental f-string leak doesn't surface in Railway
logs (shared with the team / potentially archived).
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from src.config import settings

# Patterns that indicate a secret got interpolated somewhere we didn't
# expect. Redaction is aggressive by design — false positives (e.g. a log
# that contained the word "bearer") are fine, silent leaks are not.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),  # Anthropic keys
    re.compile(r"\b\d{9,12}:[A-Za-z0-9_\-]{30,}\b"),  # Telegram bot tokens
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]+"),  # generic bearers
    re.compile(r"postgresql(?:\+asyncpg)?://[^@\s]+@"),  # DSN with creds
    re.compile(r"//postgres:[^@\s]+@"),  # bare DSN credentials
)

_REDACTED = "[REDACTED]"


def _redact_str(s: str) -> str:
    out = s
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return _redact_str(v)
    if isinstance(v, dict):
        return {k: _redact_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        seq = [_redact_value(x) for x in v]
        return type(v)(seq) if isinstance(v, tuple) else seq
    return v


def redact_secrets(logger, method_name, event_dict):
    """structlog processor: scrub secret-shaped substrings from every field."""
    for k, val in list(event_dict.items()):
        try:
            event_dict[k] = _redact_value(val)
        except Exception:
            # Never let redaction break logging.
            event_dict[k] = "[unserializable]"
    return event_dict


def configure_logging() -> None:
    level = getattr(logging, settings.log_level, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,  # last before renderer so it sees the final fields
    ]

    if settings.is_prod:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
