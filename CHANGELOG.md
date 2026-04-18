# Changelog

## [Unreleased]

### Added
- Seed migration `c1a00002seed`: partners (Казах=6885525649 owner,
  Арбуз=7220305943) and the five working-capital wallets (tapbank,
  mercurio, rapira, sber_balances, cash). Idempotent via `ON CONFLICT`.
- `.env.example` and `SETUP.md` personalized with the real TG IDs — no
  placeholder numbers left to fill in.

## [0.1.0-stage0] — 2026-04-17

Этап 0 — каркас. Первая версия на Railway, бот отвечает на `/start`,
`/help`, `/chatid`. Бизнес-логика — следующими этапами.

### Added
- Структура проекта по спеке (`src/bot`, `src/core`, `src/db`, `src/llm`,
  `src/personality`).
- `pyproject.toml` + `uv.lock` — Python 3.12, управление через uv.
- `Dockerfile` на базе `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` и
  `railway.toml` для Railway (build = DOCKERFILE, startCommand
  = `alembic upgrade head && python -m src.bot.main`).
- `src/config.py` — pydantic-settings, нормализация DSN для asyncpg.
- `src/logging_setup.py` — structlog (JSON в prod, цветной в dev).
- `src/db/models.py` — все таблицы спеки (partners, users, wallets,
  wallet_snapshots, reports, prepayments, cabinets, clients,
  poa_withdrawals, partner_contributions, partner_withdrawals, exchanges,
  fx_rates_snapshot, expenses, knowledge_base, few_shot_examples,
  message_log, feedback, audit_log, pending_reminders).
- `src/db/session.py` — async engine + `session_scope()` helper.
- `alembic.ini` + `alembic/env.py` (sync engine для миграций, async в
  runtime).
- `src/llm/client.py` — Anthropic async wrapper с retry (tenacity) и
  prompt caching.
- `src/llm/system_prompt.py` — сборка system-блоков: core / KB /
  few-shot / recent (первые три с `cache_control=ephemeral`).
- `src/llm/schemas.py` — enum Intent + PartnerShare / PoAWithdrawalParse
  / ExchangeParse.
- `src/llm/classifier.py` — regex pre-router (X/Y=Z, "эквайринг N").
- `src/bot/main.py` — aiogram Dispatcher, long-polling, graceful
  shutdown на SIGTERM.
- Middlewares: `WhitelistMiddleware` (outer), `MessageLoggingMiddleware`
  (inner, дедуп по `tg_message_id`).
- Handlers: commands (/start /help /chatid + стабы на /report /balance
  /knowledge и т.д.), mentions (подтверждает что услышал), messages
  (catch-all, вызывает `quick_classify`).
- `src/personality/voice.py` — тон-оф-войс в system prompt + текст
  первого приветствия.
- `src/personality/phrases.py` — HELP_TEXT и пара шаблонов.
- Тесты: `tests/test_classifier.py`, `tests/test_config.py`.
- Документация: `README.md`, `SETUP.md`, `DECISIONS.md`, `TODO.md`.
