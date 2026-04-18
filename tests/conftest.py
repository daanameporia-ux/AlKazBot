"""Common pytest fixtures + env bootstrap.

Tests import modules that, transitively, touch `src.config.settings` at
module load time (e.g. `src.db.session.engine = create_async_engine(...)`).
Pydantic-settings would fail import-time without real credentials, so we
plant dummy values before any test modules import.

NOTE: Claude Code's own launcher exports ANTHROPIC_API_KEY='' (empty) —
`os.environ.setdefault` does NOT overwrite empty strings (they count as
already-set). So we explicitly overwrite anything that's falsy.
"""

from __future__ import annotations

import os

_DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "test-token-dummy-0000000000",
    "ANTHROPIC_API_KEY": "sk-ant-test-dummy-0000000000",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/test",
    "APP_ENV": "dev",
}
for k, v in _DEFAULTS.items():
    if not os.environ.get(k):
        os.environ[k] = v
