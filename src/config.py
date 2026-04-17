"""App config — single source of truth for env-driven settings.

Usage:
    from src.config import settings
    print(settings.telegram_bot_token)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Core ----
    app_env: Literal["dev", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ---- Telegram ----
    telegram_bot_token: str = Field(..., min_length=10)
    main_chat_id: int = 0
    # NoDecode → pydantic-settings will NOT try JSON-parse the env value;
    # our `mode="before"` validator splits the raw CSV itself.
    allowed_tg_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    owner_tg_user_id: int = 0

    # ---- Anthropic ----
    anthropic_api_key: str = Field(..., min_length=10)
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_fallback_model: str = "claude-haiku-4-5"

    # ---- Database ----
    database_url: str = Field(..., min_length=10)

    # ---- Observability ----
    sentry_dsn: str | None = None

    # ---- Feature flags ----
    enable_pranks: bool = False
    hybrid_listen_mode: bool = True

    # ---- Validators ----
    @field_validator("allowed_tg_user_ids", mode="before")
    @classmethod
    def _split_user_ids(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _normalize_async_dsn(cls, v: str) -> str:
        # Railway provides `postgres://...` — SQLAlchemy 2 wants `postgresql+asyncpg://...`.
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # ---- Derived ----
    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class _LazySettings:
    """Proxy so `from src.config import settings` doesn't force env validation
    until someone actually accesses a field. Needed so that unit tests and
    tooling (ruff, mypy) can import the package without a real .env.
    """

    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __repr__(self) -> str:  # pragma: no cover
        try:
            return repr(get_settings())
        except Exception as e:
            return f"<LazySettings: not yet initialized ({e.__class__.__name__})>"


# Typed as Settings for IDE/mypy; actually a lazy proxy at runtime.
settings: Settings = _LazySettings()  # type: ignore[assignment]
