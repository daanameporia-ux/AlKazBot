# sber26-bot

Telegram-бот для управленческого учёта процессинг-бизнеса. Работает в
одном групповом чате команды + в личных диалогах с whitelisted юзерами.
Парсит свободную речь через Claude API, ведёт баланс кошельков / склад
кабинетов / POA-снятия / партнёрские доли, генерит вечерние отчёты.

Если ты юзер и хочешь запустить бота — открой **[SETUP.md](SETUP.md)**.
Если ты разработчик — читай дальше.

## Stack

- Python **3.12**, [uv](https://docs.astral.sh/uv/) для управления зависимостями
- [aiogram 3](https://docs.aiogram.dev/) для Telegram
- **Postgres 16** + SQLAlchemy 2.x async + asyncpg + Alembic
- [anthropic](https://pypi.org/project/anthropic/) SDK — Claude (`claude-sonnet-4-6`), с prompt caching
- pydantic 2 для валидации LLM-ответов
- structlog + Sentry
- pytest / pytest-asyncio

Хостинг — [Railway](https://railway.app) (Docker build из корневого `Dockerfile`).

## Layout

```
src/
  bot/                aiogram: main + handlers + middlewares
  core/               бизнес-логика (operations, reports, reminders)
    operations/       по файлу на тип операции
  db/                 SQLAlchemy models + session
  llm/                Anthropic wrapper, system_prompt, classifier, parser
  personality/        тон бота, фразы
  config.py           pydantic-settings, всё из .env
alembic/              миграции
tests/                pytest
Dockerfile            uv-based build для Railway
railway.toml          конфиг деплоя
SETUP.md              инструкция юзеру
DECISIONS.md          автономные решения Claude Code
TODO.md               следующие этапы
CHANGELOG.md          что меняли
sber26-bot-SPEC.md    исходная спека (чтение обязательно перед работой)
```

## Local dev

```bash
# 1. install deps
uv sync

# 2. copy env
cp .env.example .env
# заполни TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL, ALLOWED_TG_USER_IDS

# 3. поднимаем postgres (docker)
docker run -d --name sber26-pg -p 5432:5432 \
    -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=sber26 postgres:16

# 4. миграции
uv run alembic upgrade head

# 5. запуск
uv run python -m src.bot.main
```

## Tests / lint

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format src tests
```

## Deploy

Push в `main` → Railway собирает `Dockerfile` → при успешной сборке
выполняет `alembic upgrade head && python -m src.bot.main`
(см. `railway.toml`).

Environment variables — в Railway UI во вкладке **Variables**
(полный список в `.env.example`).

## Текущий этап

**Этап 0 (каркас).** Бот отвечает на `/start`, `/help`, `/chatid`,
логирует все сообщения в `message_log`. Парсинг операций и LLM-обращения
подключаются на Этапе 1.

Следующее — см. `TODO.md`.
