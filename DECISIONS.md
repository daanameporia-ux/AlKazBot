# DECISIONS

Автономные решения Claude Code по ходу разработки. Формат:
**[этап — дата] — решение — почему — как поправить.**

Если юзер не согласен с чем-то — скажи, откатим / переделаем.

---

## [Stage 0 — 2026-04-17]

### D-001. Менеджер пакетов: `uv` (не poetry / pip-tools)
- **Почему:** uv стандарт 2025, в ~10× быстрее poetry, родное управление
  версиями Python (`uv python install 3.12`), lock-файл детерминирован.
- **Где видно:** `pyproject.toml`, `uv.lock`, `Dockerfile`, `railway.toml`.
- **Поправить:** сменить на poetry — переписать Dockerfile + `uv run`
  на `poetry run` в `railway.toml`.

### D-002. Railway билдер: **Dockerfile**, не Nixpacks
- **Почему:** официальный uv-образ (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`)
  даёт быструю и детерминированную сборку, Nixpacks про uv знает плохо.
- **Поправить:** удалить `Dockerfile`, в `railway.toml` заменить
  `builder = "DOCKERFILE"` → `"NIXPACKS"`.

### D-003. Long-polling, не webhooks
- **Почему:** для MVP проще и надёжнее, webhooks требуют публичного HTTPS
  эндпоинта. Railway поддерживает и то и то.
- **Поправить:** см. aiogram webhook docs, понадобится FastAPI/aiohttp
  веб-сервер на `$PORT`.

### D-004. aiogram 3 Router-pattern, middleware-стек `Whitelist → MessageLogging`
- **Whitelist — outer middleware**: чужие апдейты даже не доходят до
  логирования, экономим БД.
- **MessageLogging — inner middleware**: персистит каждое сообщение
  whitelisted-юзера (нужно для обучаемости + контекста).

### D-005. База данных: NUMERIC(18,6) для USDT, NUMERIC(18,2) для RUB
- **Почему:** так в спеке. 6 знаков для USDT хватит с запасом (обычно 2-3).

### D-006. Хранить `uv.lock` в репе
- **Почему:** Railway должен собирать точно те же версии что и локально,
  иначе невоспроизводимые баги.

### D-007. Миграции — Alembic, sync engine в env.py
- Alembic не любит async-движки; транслируем `+asyncpg` DSN → sync для
  миграций. На app-уровне остаётся async.

### D-008. Bot entrypoint — модуль `python -m src.bot.main`
- **Почему:** `python -m` корректно настраивает `sys.path`, не ломается
  при reorganization.

### D-009. Режим слушания по умолчанию — гибрид (`HYBRID_LISTEN_MODE=true`)
- Юзер подтвердил (AskUserQuestion). Regex-роутер ловит `X/Y=Z` и
  "эквайринг N" без `@` → без токенов, автоматическое поведение.
  Всё остальное требует `@бот`.
- Переключить через env: `HYBRID_LISTEN_MODE=false`.

### D-010. Sentry и S3-бэкапы — на Этап 4 (юзер подтвердил)
- В коде есть точки подключения (`SENTRY_DSN`), но не активируем пока.

### D-011. Модели Partner / User — раздельные таблицы (как в спеке)
- Возможно позже слить, но спека чёткая: `Partner` — сущность бизнеса
  (может не иметь tg), `User` — telegram-аккаунт, ссылается на Partner
  если это партнёр.

### D-012. Партнёров и Wallets **сеем отдельной миграцией** (c1a00002seed)
- Юзер подтвердил: Казах = 6885525649 (owner), Арбуз = 7220305943.
- Wallets из спеки: `tapbank`, `mercurio`, `rapira`, `sber_balances`, `cash`.
- Миграция идемпотентна (`ON CONFLICT DO UPDATE`), повторный апгрейд
  безопасен.
- Если ID нужно поменять — можно править в самой миграции
  и делать `alembic downgrade b4fbd8da6908 && alembic upgrade head`, или
  просто `UPDATE partners SET tg_user_id=... WHERE name=...`.

### D-013. `INT` для первичных ключей (не UUID / BIGINT)
- Спека явно говорит `SERIAL PRIMARY KEY`. Масштаб ~2-5 юзеров, 10к-100к
  операций — int4 хватит на десятилетия.

### D-014. Prompt caching: три блока — core / KB / few-shot
- Spec § "Обучаемость → Как бот использует базу" явно диктует такую
  структуру. `recent_messages` — не кэшируется (меняется каждый запрос).

### D-015. Structured-output через `tool_use` (не JSON mode)
- Anthropic рекомендует `tool_use` для схем (строгая валидация на их
  стороне). JSON mode даёт более слабые гарантии.
- Реализация — на Этапе 1 (парсер).

### D-016. На Railway — Postgres через public TCP-proxy, не internal network
- Railway private networking (`<service>.railway.internal`) — IPv6-only.
  У аккаунта юзера `ipv6EgressEnabled=false` и включение требует
  account-level feature-flag — лишний клик.
- Развязали: `tcpProxyCreate` → `metro.proxy.rlwy.net:16645` → пишем
  его в `DATABASE_URL` как публичный endpoint. Сетевой overhead мизерный
  (оба контейнера в одной US-east-ish локации Railway).
- Когда IPv6 egress будет включён в аккаунте — можно переписать DSN
  обратно на `postgres.railway.internal:5432`.

### D-017. Container entrypoint = `scripts/entrypoint.py`, не shell one-liner
- Railway runtime-логи обрывались после `alembic` init без traceback —
  Python stdout либо буферился, либо процесс умирал silently.
- Заменили shell-chain на `python -u scripts/entrypoint.py`. Он
  последовательно пишет `[entrypoint] ...` маркеры через
  `print(..., flush=True)`: env snapshot, DNS, TCP probe, alembic,
  импорт main. Один взгляд на Railway logs — и видно где оборвалось.
- Плюс безболезненно меняется в одном файле (в отличие от inline
  startCommand).

### D-018. Reply-detection учитывает Bot API 7.0 `external_reply`
- Telegram клиент (iOS / macOS Desktop в последних версиях) часть
  reply-событий отсылает боту как `external_reply` + `quote`, не как
  классический `reply_to_message`. Если проверять только старое поле —
  бот игнорирует reply и юзер думает что его не слышат.
- В `mentions._addressed_to_me` проверяем OR по трём путям:
  `@mention` | `reply_to_message.from_user.id == me.id` |
  `external_reply.origin.sender_user.id == me.id`.

### D-019. Railway account-token → GraphQL, не CLI
- `railway` CLI не принимает account-token через `RAILWAY_API_TOKEN`
  для read-write операций (тестили — `Unauthorized`).
- Работает прямой GraphQL API: `https://backboard.railway.app/graphql/v2`
  с `Authorization: Bearer <token>` (Cloudflare 1010 блокирует
  urllib-UA, использую curl как транспорт).
- Тонкая обёртка — `.scratch/rw.py` (не коммитится, нужен только
  автоматизатору).

### D-020. Whitelist — единственный trust gate (2026-04-20)

- **Что:** `WhitelistMiddleware` раньше пропускал любого отправителя
  в `MAIN_CHAT_ID`, даже если его нет в `ALLOWED_TG_USER_IDS`.
  Проверка на handler-level — hit-or-miss.
- **Почему:** если левый аккаунт добавлен в главный чат, он мог
  триггерить команды. Handler-checks разбросаны (большинство
  команд без них). Ужесточаем middleware — одна точка аудита.
- **Trade-off:** если не-whitelisted партнёр появляется в чате, для
  бота он невидим, пока его не добавят. Норм.
- **Откатить:** вернуть `or main_group` в `src/bot/middlewares/auth.py`.

### D-021. Callback confirm/cancel — только creator или owner (2026-04-20)

- **Что:** `callbacks.py` peek'ает pending_op до pop, отказывает если
  `q.from_user.id` не creator и не owner.
- **Почему:** `callback_data=confirm:<uid>` ходит открыто. Без проверки
  любой, увидевший (или угадавший) uid, может форсить чужую запись.
- **Trade-off:** небольшое трение если Арбуз хочет подтвердить карточку,
  созданную Казахом — owner (Казах) всё равно может.

### D-022. Бот без цензуры по просьбе owner-а (2026-04-20)

- **Что:** `PERSONALITY_PROMPT` разрешает свободный мат; в
  `CORE_INSTRUCTIONS` явный запрет на корпоративные отказы в
  рамках роли учёта.
- **Почему:** Казах написал в живом чате (2026-04-19): «Матерись как
  хочешь. Ты свободный бот.» Наш governor мешал голосу, который
  команда реально хочет.
- **Границы:** никаких персональных атак по национальности / внешности;
  честность про капабилити, наоборот, усилена.

### D-023. PDF без автопарсинга без явной просьбы (2026-04-20)

- **Что:** `SBER_HINT` + `ALIEN_PDF_HINT` + `has_explicit_ingest_request`
  требуют ТРИ условия перед записью из PDF: явный trigger-токен,
  «счёт наш», confidence ≥ 0.8.
- **Почему:** прод 2026-04-19: юзер прислал чужую выписку
  (Сельвян) — бот создал 14 preview-карточек. Вежливо неправильно.

### D-024. OGG retention 72h → 14d (2026-04-20)

- **Что:** `reminders._wipe_stale_voice_ogg` теперь ждёт 14 дней
  вместо 72 часов.
- **Почему:** после апгрейда Whisper-модели хочется перетранскрибировать
  старые записи. Бёрзт всё слишком рано. 14 дней — умеренное
  хранилище.

### D-025. Advisor — signal or shut up (2026-04-20)

- **Что:** `src/core/advisor.py` с 3 нюджами; де-дуп через
  `pending_reminders`; quiet hours 19:00-06:00 UTC;
  только высокоуверенные сигналы.
- **Почему:** owner хочет бизнес-напарника, не болтуна. Каждое
  proactive-сообщение должно заслужить место; спам ломает доверие.
- **Как расширять:** добавить `(name, async_fn, interval_min)` в
  `ADVISOR_JOBS`, внутри функции проверять `_in_quiet_window()`.
