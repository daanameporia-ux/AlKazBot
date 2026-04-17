# TODO

Что ещё не сделано. Группировка по этапам из `sber26-bot-SPEC.md § "Этапы работы"`.

---

## Этап 0 — каркас (в работе / почти готов)

- [x] Структура проекта + pyproject.toml + railway.toml + Dockerfile
- [x] SQLAlchemy модели (все таблицы из спеки)
- [x] Alembic init + env.py
- [ ] Первая миграция `0001_initial.py` (autogenerate после `uv sync`)
- [x] aiogram 3 skeleton: /start, /help, /chatid + whitelist middleware + message logging
- [x] Anthropic wrapper с prompt caching
- [x] System prompt (static часть) + personality
- [x] SETUP.md, DECISIONS.md, README.md
- [ ] Первый пуш в GitHub
- [ ] Подключить Railway и задеплоить (ответственный — юзер, с моей подсказкой)

---

## Этап 1 — обучаемость и минимальные операции

- [ ] `src/llm/knowledge_base.py` — CRUD (add / list / forget / edit / search по ключу)
- [ ] `src/llm/few_shot.py` — `render_for_intent(intent, limit=5)` + авто-запись при верификации
- [ ] `src/llm/parser.py` — structured output через tool-use, pydantic-валидация
- [ ] `src/llm/classifier.py` — LLM-путь для всего что regex не поймал
- [ ] Seed партнёров (Казах, Арбуз) + TG ID → Alembic data migration
- [ ] Seed wallets (tapbank, mercurio, rapira, sber_balances, cash)
- [ ] Команды `/knowledge`, `/knowledge add|forget|edit`
- [ ] Команда `/report` — вопросы + wallet snapshot + формула
- [ ] Команды `/balance`, `/fx`, `/partners`, `/feedback`
- [ ] Парсер для `exchange`, `expense`, `partner_deposit`, `partner_withdrawal`, `wallet_snapshot`
- [ ] Reminder: просроченный отчёт (>26 ч)
- [ ] Тесты парсеров (см. spec §"Тесты")

---

## Этап 2 — сложные операции

- [ ] POA withdrawals (включая валидацию сумм долей = 100%)
- [ ] Связка POA → exchange → автогенерация `partner_contributions`
- [ ] Клиенты (CRUD, история, долги)
- [ ] Кабинеты: purchase / worked_out / blocked / recovered
- [ ] Авто-генерация `auto_code` для безымянных кабинетов
- [ ] Prepayments с множественной отгрузкой, сверка сумм
- [ ] Команды `/stock`, `/clients`, `/client`, `/debts`, `/history`, `/undo`

---

## Этап 3 — обучение и полировка

- [ ] `verified=true` flow: inline-кнопка "Всё верно" + запись в few-shot
- [ ] Confidence scoring в парсерах (< 0.7 → уточняющий вопрос)
- [ ] Feedback loop (запись в `feedback`, команда `/feedback`)
- [ ] Персоналити: проверить реальный тон на диалогах с командой
- [ ] Админские приколы (`ENABLE_PRANKS=true`) — смена аватарки / пин
- [ ] `/silent on|off`

---

## Этап 4 — тесты и мониторинг

- [ ] Полное покрытие парсеров и отчёта pytest-ами
- [ ] Sentry DSN → интеграция (код уже готов, надо дать ключ)
- [ ] S3 / Backblaze — ежедневный дамп Postgres
- [ ] Rate limit 20 msg/min на юзера
- [ ] Проверка memory leaks (long-polling процесс работает месяцами)

---

## Отложено (не делаем в MVP)

- Vision-парсинг скриншотов
- Web UI
- Интеграция с 1С / банковскими API
- Мультитенантность
- Экспорт в Excel/PDF
- Google Sheets sync
- Многоязычность
- Сложная иерархия ролей (сверх whitelist + owner)
