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

### D-012. Партнёров и Wallets **не сеем** на Этапе 0
- В спеке имена "Казах" и "Арбуз", но мне нужно подтверждение юзера
  (кто из них он + TG ID второго). Seed-миграция = Этап 1.

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
