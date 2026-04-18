# Changelog

## [Unreleased]

### Added
- Seed migration `c1a00002seed`: partners (Казах=6885525649 owner,
  Арбуз=7220305943) and the five working-capital wallets (tapbank,
  mercurio, rapira, sber_balances, cash). Idempotent via `ON CONFLICT`.
- `.env.example` and `SETUP.md` personalized with the real TG IDs — no
  placeholder numbers left to fill in.

## [0.2.0-stage1] — 2026-04-18

### Added
- **Hybrid-plus listening mode.** Every message from a whitelisted user
  in MAIN_CHAT_ID accumulates in an in-memory `BatchBuffer` (`src/bot/
  batcher.py`) and is analysed as a pack when any of these fires:
  8 messages piled up, 3 min of silence, or an explicit trigger
  (@-mention / reply to bot / slash-command).
- **Batch LLM analyzer** (`src/llm/batch_analyzer.py`). One Claude call
  per batch via tool-use; returns a list of structured
  `BatchOperation`s with intent / confidence / source_message_ids /
  fields / ambiguities, plus optional `chat_reply` for free-text
  answers when the batch was a question.
- **Confirm-before-persist UX.** Every candidate operation becomes a
  `PendingOp` (src/core/pending_ops.py, 30-min TTL) and is shown in
  chat as an HTML preview card (`src/core/preview.py`) with ✅ / ❌
  inline buttons. User taps ✅ → `src/core/applier.py` writes to the
  right table + `audit_log`.
- **10 intent appliers**: exchange, expense, partner_deposit,
  partner_withdrawal, poa_withdrawal, cabinet_purchase,
  cabinet_worked_out, cabinet_blocked, prepayment_given, client_payout.
- **Repositories** for every write path: `exchanges`, `expenses`,
  `partner_ops`, `snapshots`, `clients`, `poa`, `cabinets`,
  `prepayments`, `audit`.
- **`/report`** — full end-of-day report with the classical layout and
  the spec's net-profit formula. Persists a `reports` row and
  `cabinets_worked` since the last one.
- **Read commands**: `/stock` (grouped by status), `/clients`,
  `/client <name>` (per-client history + outstanding debt),
  `/debts` (all unpaid POA shares), `/history [N]` (audit_log tail),
  `/undo <audit_id>` (owner-or-creator rollback of creates).
- **APScheduler reminders** (`src/core/reminders.py`) — 5 nag types:
  overdue report (>26h + new ops), acquiring missing (>2d), cabinet
  in_use too long (>12h), POA without exchange (>6h), client debt
  stale (>24h). De-duped via `pending_reminders` rows.
- **Diagnostic container entrypoint** (`scripts/entrypoint.py`) with
  env snapshot + DNS/TCP probe + alembic + bot start markers — useful
  post-mortem material on Railway.
- `tests/test_batcher.py`, `tests/test_applier.py`, `tests/test_preview.py`
  — 16 new tests on top of Stage 0's 9. 25 green.

### Fixed
- `/help` crashed with `Bad Request: Unsupported start tag "код"` —
  HELP_TEXT reorganised as valid HTML, angle-bracketed placeholders
  HTML-escaped.
- `@Al_Kazbot запомни ...` only captured the first line — regex now
  DOTALL + `.search()` so multi-line facts store in full.
- Bot wasn't reacting to Telegram Bot-API-7.0 replies — the new
  `external_reply` / `quote` fields are now treated equivalently to
  the classic `reply_to_message`.
- Container was SIGTERMed ~4s after start because `uv run` reinstalled
  the project on every start; dropped `uv run` from runtime, put
  `.venv/bin` on PATH in the Dockerfile.
- Railway private network is IPv6-only and the account's egress flag
  was off — DATABASE_URL now targets the public TCP proxy
  (`metro.proxy.rlwy.net:16645`).

### Changed
- `src/bot/handlers/mentions.py` no longer calls `process_message`
  directly in the main group — routes through the BatchBuffer so
  context accumulated from other teammates travels with the @-trigger.
- `src/bot/handlers/messages.py` flipped from no-op to passive intake
  (appends whitelisted-user messages to the BatchBuffer).

### Scope
End-user-facing set now covers Stage 1 and 2 scope of the spec
(POA / cabinets / prepayments / exchange / expense / partner ops /
report / reminders). Stage 3 (few-shot verification loop,
admin-prank flag) and more comprehensive parser tests are still
open.

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
