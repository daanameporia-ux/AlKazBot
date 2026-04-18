# TODO

Группировка по этапам из `sber26-bot-SPEC.md § "Этапы работы"`.
Обновлено: 2026-04-18.

---

## Этап 0 — каркас ✅

- [x] Структура проекта + pyproject.toml + railway.toml + Dockerfile
- [x] SQLAlchemy модели (все таблицы из спеки)
- [x] Alembic init + env.py
- [x] Первая миграция `0001_initial.py`
- [x] aiogram 3 skeleton: /start, /help, /chatid + whitelist middleware + message logging
- [x] Anthropic wrapper с prompt caching
- [x] System prompt (static часть) + personality
- [x] SETUP.md, DECISIONS.md, README.md
- [x] Первый пуш в GitHub
- [x] Railway deploy + Postgres service + env vars

---

## Этап 1 — обучаемость и минимальные операции ✅

- [x] `src/llm/knowledge_base.py` — CRUD (add/list/forget/edit/search)
- [x] `src/llm/classifier.py` — regex pre-router + LLM classifier
- [x] `src/llm/batch_analyzer.py` — multi-intent batch анализатор через tool-use
- [x] `src/bot/batcher.py` — in-memory batch buffer (size/age/trigger flush)
- [x] Seed партнёров (Казах, Арбуз) + TG ID → Alembic data migration
- [x] Seed wallets (tapbank, mercurio, rapira, sber_balances, cash)
- [x] Команды `/knowledge`, `/knowledge add|forget|edit|search`
- [x] Команда `/report` — формула прибыли, классический формат, persist
- [x] Команды `/balance`, `/fx`, `/partners`, `/feedback`
- [x] Парсер для `exchange`, `expense`, `partner_deposit`,
  `partner_withdrawal`, `wallet_snapshot`, `poa_withdrawal`,
  `cabinet_purchase/worked_out/blocked`, `prepayment_given`,
  `client_payout`
- [x] Confirm/cancel inline buttons + audit_log
- [x] APScheduler reminders (5 типов)
- [x] Тесты batcher / applier parse helpers / preview

---

## Этап 2 — сложные операции ✅

Закрыто вместе с Этапом 1 (юзер попросил сделать одним заходом).

- [x] POA withdrawals (включая валидацию сумм долей = 100% в preview)
- [x] Связка POA → exchange → автогенерация `partner_contributions`
  (см. `src/db/repositories/poa.py` → `attach_exchange()`)
- [x] Клиенты (CRUD, история, долги) — `/clients`, `/client <name>`, `/debts`
- [x] Кабинеты: purchase / worked_out / blocked
- [x] Авто-генерация `auto_code` для безымянных кабинетов (`Cab-NNN`)
- [x] Prepayments с открытием/закрытием; `/stock` показывает статусы
- [x] Команды `/stock`, `/clients`, `/client`, `/debts`, `/history`, `/undo`

---

## Этап 3 — обучение и полировка (следующие шаги)

- [ ] `verified=true` flow: при ✅ сохранять `(input_text, parsed_json)` в
  `few_shot_examples`. При следующих похожих запросах подгружать этот
  пример в system-prompt → уменьшается неопределённость.
- [ ] Confidence scoring: сейчас `< 0.7` → preview с пометкой, но без
  fallback-диалога. Добавить follow-up question если ambiguities не
  пустые.
- [ ] Feedback loop: passive detection фраз "было бы круто", "неудобно"
  → запись в `feedback` с контекстом, бот ничего не отвечает.
- [ ] `/silent on [2h]` — временно заглушить reminders и chat_reply.
- [ ] Cabinet `recovered` status по "восстановили через нотариалку"
  (сейчас поддерживается only в модели, парсер его не знает).
- [ ] Кабинет `blocked → in_use` обратный переход (нотариалка сработала).
- [ ] Prepayment fulfilment с множеством кабинетов (`intent=
  prepayment_fulfilled`, applier ещё не написан).
- [ ] Админские приколы (`ENABLE_PRANKS=true`) — смена аватарки /
  случайный пин раз в неделю.

---

## Этап 4 — тесты и мониторинг

- [ ] Интеграционные тесты: pytest + pytest-postgresql (fresh DB на тест)
- [ ] Тесты `/report` формулы на живых данных
- [ ] Sentry DSN → интеграция (код готов, добавить ключ в Railway)
- [ ] S3 / Backblaze — ежедневный дамп Postgres
- [ ] Rate limit 20 msg/min на юзера
- [ ] Проверка memory leaks (long-polling процесс работает месяцами)

---

## Ops / QoL

- [ ] Resync worker на старте: пройти по `message_log` свежим, где
  нет `intent_detected`, прогнать через batch_analyzer для catch-up.
- [ ] `/undo` для обновлений (сейчас только creates).
- [ ] Идемпотентность preview-карточек после рестарта (сейчас pending
  ops живут в памяти — при рестарте теряются; хранить в таблице?)
- [ ] Возможный миграционный путь на internal Railway networking когда
  ipv6 egress будет включён в аккаунте (см. DECISIONS.md D-016).

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
