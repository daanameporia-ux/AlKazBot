# Changelog

## [Unreleased]

## [0.4.0-apple-polish] — 2026-04-20

Giant audit pass + advisor mode + personality uncensoring, driven by a
day of live-prod pain points (Казах-side).

### Security

- **Callback forgery fix** — only the op's creator or the owner can
  confirm / cancel a pending_op card. Previously anyone seeing the
  callback_data could fire someone else's operation.
- **Whitelist tightened** — `MAIN_CHAT_ID` no longer grants access to
  non-whitelisted users. Main-group is now a routing signal only.
  Works for both `Message` and `CallbackQuery` events.
- **Owner-gated commands** — `/silent`, `/resync`, `/avatar`, and
  `/keywords add|remove` now require `OWNER_TG_USER_ID`. Listing
  keywords / inspecting silent state stays open to the team.
- **Log secret redaction** — structlog processor scrubs Anthropic
  keys, TG bot tokens, Bearer tokens, and DSN creds from every log
  event before output.
- **PDF DoS guards** — 15 MB size cap, 60 s extract timeout. 
  Non-Sber PDFs never auto-parse into operations.
- **Voice DoS guard** — 180 s hard timeout on faster-whisper so a
  pathological OGG can't freeze polling.
- **Rate limit** is now per-`(user, chat)` instead of per-user; a
  single user can't fan out across chats to multiply their budget.

### Bot honesty / LLM prompts

- **`CORE_INSTRUCTIONS`** rewritten. Strong "no lies about own
  capabilities" rule: bot must not deny Vision on stickers (prod
  sample: bot said "only emoji" while Vision descriptions were
  rendered right there in its system prompt).
- **Sticker hallucination fix** — prompt now requires the bot to
  read `recent_history` for its own sends and quote the actual
  description instead of fabricating ("Сбер там" when the sticker
  was Blizzard).
- **"Я забыл из памяти" lie removed** — instructions explain what the
  bot can actually delete (pending_ops via ❌, operations via
  `/undo`) and what it can't.
- **`PERSONALITY_PROMPT`** unclamped. Removed "мат — не сыпать"
  governor per Казах's explicit request. Added explicit anti-
  corporate framing and the 7 tone rules.
- **Operation detection from free speech** — analyzer now looks for
  operations in voice transcripts / casual phrasing without
  requiring literal "запиши". Confidence ≥ 0.75 or the operation
  goes out with `ambiguities` for the user to resolve.

### PDF policy hardening

- `SBER_HINT` now enforces THREE conditions before any auto-parse:
  (a) explicit user request (concrete token list from
  `has_explicit_ingest_request`), (b) document is our team's
  account, (c) confidence ≥ 0.8. Any miss → `operations=[]` with a
  "это по нашему счёту?" follow-up.
- `ALIEN_PDF_HINT` added — non-Sber PDFs get a stricter "don't parse,
  summarize only" instruction block. Fixes the live-prod case where
  an alien client's bank statement (Сельвян Андрей) was turned into
  14 expense preview cards.

### Voice pipeline

- Whisper `initial_prompt` expanded with partners, clients,
  suppliers, and business vocab (додеп, откуп, пятерик, нотариалка,
  контора, Rapira, TapBank, ...). Filters Latin tokens to avoid
  Whisper transliterating Russian speech into ASCII.
- KB entity names (alias + entity with a `key`) now feed into the
  Whisper prompt — learns new names as the team teaches them.
- `_postprocess_transcript()` fixes common Whisper mishears
  ("Вержан"→"ержан", "нахуят"→"нахуя ты", "alkaz"→"алказ", ...).
- OGG retention extended 72h → 14d so we can re-transcribe after a
  model upgrade / debug a garbled transcript.

### Finance math guards

- **Exchange math** — applier now validates `amount_rub / fx_rate ≈
  amount_usdt` within 0.5%, rejects zero/negative, and catches the
  classic `amount_usdt <-> fx_rate` swap before it hits the DB.
- **POA partner validation** — two-pass: (1) shape + sum check, (2)
  each partner must exist in `partners` via `resolve_partner`.
  Prevents ghost shares silently dropping when attach_exchange runs.
- **Cabinet auto_code race** — select-then-insert retry loop on
  unique-violation. Single-bot traffic rarely collides, but the
  prior `COUNT(*)+1` could duplicate codes under concurrent creates.

### Knowledge base

- **Fuzzy dedup** in `add_fact` — merges near-duplicates (≥0.85
  Ratcliff-Obershelp) within same category/key. Fixes prod case
  where "Рапира биржа" and "Рапира — биржа." became two rows.
- **Migration `f3a10012kbclean`** — deactivates the two junk
  "для будущего" rule rows + all duplicate `рапа` aliases except
  the earliest, purges >7d expired/cancelled pending_ops.

### Stickers

- **Pack theme** — new `seen_stickers.pack_theme` column for
  thematic tagging of whole packs. `kontorapidarasov` seeded as
  `сбер-мем` (120 stickers) so "сбер"-themed picks land in the
  right pack even if no individual sticker description matches.
- **`sticker_theme_hint`** — new field on `BatchAnalysis` +
  pick_smart. Four-level fallback cascade: theme is the pin,
  description narrows, emoji tie-breaks, drop one at a time.
- **Described catalog** — system prompt now shows theme next to
  pack name so Claude sees what themes exist.

### Advisor mode (proactive)

New `src/core/advisor.py` with three nudges:

- **balance_vs_cabinet** — if /balance asked recently AND a cabinet
  has been in_use 12+ h → one nudge.
- **client_repeat** — known client mentioned 3+ times in 24h with no
  POA logged → suggest creating one.
- **fx_drift** — two latest fx snapshots differ by ≥ 5% → flag it.

De-duped via the same `pending_reminders` machinery.

### Quiet hours

All chat-sending reminders (+ advisor jobs) now muted during
Moscow-night (22:00–09:00 local = 19:00–06:00 UTC). Pending-op expiry
and voice-OGG wipe run regardless (no chat message side-effect).

### UX polish

- Rewrote `HELP_TEXT` — shorter, scannable, accurate command list.
- `BOT_COMMANDS` list (bot's `/` menu) populated with all 20 live
  commands. Was effectively 11 before; 9 were invisible.
- Preview cards use KB-preference rounding: USDT → `$1`, RUB ≥ 10k
  → `100₽`. Exchange preview shows the full formula inline:
  `280 000 ₽ @ 80.46 ₽/USDT = 3 480$`.

### Ops

- **Drain timeout** 15s → 45s so Claude-API retries don't lose
  preview-card sends on SIGTERM.
- **Periodic purge** — new job hard-deletes `pending_ops` with
  status in (expired, cancelled) older than 7 days (previously
  just-marked; table grew indefinitely).

### Tests

Added 7 new test files (92 passing total):

- `test_applier_exchange_math.py` — math guards.
- `test_knowledge_dedup.py` — fuzzy similarity threshold.
- `test_voice_postprocess.py` — mishear fixes + prompt builder.
- `test_pdf_gate.py` — SBER vs ALIEN hints + explicit tokens.
- `test_log_redaction.py` — secret scrubbing.
- `test_reminders_quiet_window.py` — night-mode gate.

## [0.3.0-audit] — 2026-04-19

Full audit pass + voice features + behaviour tuning.

### Added
- **Voice note support.** Bot captures `F.voice` into a new
  `voice_messages` table (OGG bytea + metadata). Two new ways to get
  the bot to act on a voice:
    1. Record voice, then reply to it with `@Al_Kazbot` — bot
       transcribes that exact voice and feeds the text into the
       analyzer before answering.
    2. Record voice, then send a bare `@Al_Kazbot` right after — bot
       picks the most-recent untranscribed voice from the same user in
       the same chat (within 10 min) and does the same.
- **Inline transcription runtime.** `faster-whisper` (`small`, int8,
  ru) runs inside the Railway container. Model is pre-downloaded at
  Docker build time so the first in-chat call doesn't stall.
- **Periodic voice backfill.** APScheduler job every 5 min scans the
  `voice_messages` queue and transcribes up to 5 rows per tick. A
  second job every 6 h force-wipes OGG bytes older than 72 h that
  never got transcribed (Postgres bloat ceiling).
- **`/voices` command** — shows the pending transcription count.
- **`scripts/transcribe_voices.py`** — still works as a manual
  fallback when running outside Railway (dev laptop).

### Fixed (audit pass)
- **POA partner-share validation.** `apply()` now enforces that the
  sum of partner shares + client_share_pct equals 100 % (±0.5 %
  tolerance). Missing / empty / zero-pct shares reject the op
  with a clear Russian error. Prevents silent underpayment.
- **/undo cascade.** Rolling back a `poa_withdrawals` row now also
  deletes the `partner_contributions` fanned out by
  `attach_exchange`. `wallet_snapshots` added to the supported
  table map. The rollback audit row now records what cascaded.
- **Media routing shadow.** `mentions` and `messages` catch-alls now
  filter on `F.text | F.caption` so voice / photo / document /
  sticker messages reach their own routers.
- **Router registration order** — media routers register before the
  text catch-alls.

### Added (audit + enrichment)
- **Perf indexes** (migration `e2a00006idx`):
  `partner_contributions(source, source_ref_id)`,
  `poa_withdrawals(client_paid, withdrawal_date)`,
  `message_log(chat_id, created_at DESC)`,
  `voice_messages(created_at) WHERE transcribed_text IS NULL`,
  `audit_log(table_name, record_id)`.
- **KB enrichment (v2)** — 12 more seeded facts: Никонов / Миша
  entities, залог / нотариалка / отработать / додеп glossary,
  rules about share-sum enforcement and prepayment fulfilment,
  patterns for "откуп" and "сняли с X", preferences on report
  formatting.

### Behaviour tuning from prod logs
- `CORE_INSTRUCTIONS` grew a "CRITICAL formats" section spelling out
  the X/Y=Z = RUB/USDT/fx_rate convention after the LLM swapped the
  last two values on a live exchange record.
- Seeded 12 critical KB facts (aliases for рапа / пятерик / нал /
  Tpay / Merk / додеп, X/Y=Z pattern, POA share rule, acquiring
  daily rule, arithmetic precheck preference).

### Tests
- 5 new POA-validation tests (sums, zero pct, empty partner, exact
  100 %).
- Total test count: **44 green**.

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
