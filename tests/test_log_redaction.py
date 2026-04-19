"""Secret redaction in the logging pipeline."""

from __future__ import annotations

from src.logging_setup import _redact_str, _redact_value, redact_secrets


def test_redacts_anthropic_key() -> None:
    out = _redact_str(
        "failed with key sk-ant-api03-DEADBEEFdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefAA"
    )
    assert "sk-ant-" not in out
    assert "[REDACTED]" in out


def test_redacts_tg_bot_token() -> None:
    out = _redact_str("using 8634741067:AAEPf61-5UhJqfFvSki7kdx8XmMJ_zmbgmo now")
    assert "8634741067:AAEPf61-5UhJqfFvSki7kdx8XmMJ_zmbgmo" not in out
    assert "[REDACTED]" in out


def test_redacts_bearer_token() -> None:
    out = _redact_str("Authorization: Bearer abc123XYZ_token-end")
    assert "Bearer abc123XYZ_token-end" not in out


def test_redacts_dsn() -> None:
    out = _redact_str(
        "postgresql+asyncpg://postgres:super_secret@metro.proxy.rlwy.net:16645/db"
    )
    assert "super_secret" not in out
    assert "[REDACTED]" in out


def test_leaves_clean_text_alone() -> None:
    clean = "batch flush ok, n_ops=3, chat_id=-1234"
    assert _redact_str(clean) == clean


def test_redact_value_recursive() -> None:
    out = _redact_value(
        {
            "token": "Bearer abc123.xyz",
            "list": ["sk-ant-api03-abcdefghijklmnop1234"],
        }
    )
    assert "[REDACTED]" in out["token"]
    assert "[REDACTED]" in out["list"][0]


def test_structlog_processor_returns_dict() -> None:
    event = {"msg": "ok", "token": "Bearer xyz.abc"}
    out = redact_secrets(None, "info", event)
    assert isinstance(out, dict)
    assert "[REDACTED]" in out["token"]
