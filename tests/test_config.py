"""Smoke test: config loads and DSN normalization works."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.config import Settings


def test_database_url_normalization() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "x" * 20,
        "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 20,
        "DATABASE_URL": "postgres://u:p@h:5432/db",
    }
    with patch.dict(os.environ, env, clear=False):
        s = Settings()  # type: ignore[call-arg]
        assert s.database_url.startswith("postgresql+asyncpg://")


def test_allowed_user_ids_split() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "x" * 20,
        "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 20,
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
        "ALLOWED_TG_USER_IDS": "111,222,  333",
    }
    with patch.dict(os.environ, env, clear=False):
        s = Settings()  # type: ignore[call-arg]
        assert s.allowed_tg_user_ids == [111, 222, 333]
