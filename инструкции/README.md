# AlKazBot — инструкция для разработчиков и LLM-агентов

> Это авторитативный source-of-truth для любого, кто открывает репозиторий
> **впервые**. Начиная от нового разработчика до Claude/Gemini/GPT-агента,
> которому предстоит что-то здесь чинить или расширять.
>
> Если находишь расхождение между этим файлом и кодом — верь коду и
> обновляй файл. Файл живой, не boilerplate.
>
> Связанные доки:
> * `sber26-bot-SPEC.md` — product-спека (что и зачем со стороны бизнеса).
> * `DECISIONS.md` — архитектурные решения, trade-offs, почему сделано именно так.
> * `CHANGELOG.md` — история релизов.
> * `инструкции/СЕССИЯ_*.md` — архив конкретных рабочих сессий.

---

## 1. Что это за бот и зачем

Telegram-бот для учёта операций processing-команды, которая:

* принимает RUB-платежи от клиентов (через банки-эквайеры TapBank / Mercurio),
* прогоняет деньги через **Сбер-кабинеты** (discrete inventory: каждый
  кабинет — отдельный юнит со своей себестоимостью и состоянием),
* обналичивает или конвертирует RUB → USDT через Rapira,
* возвращает USDT клиенту минус ~7% комиссии.

Параллельный поток: **POA-withdrawal** — клиент выдаёт доверенность,
команда снимает его деньги со Сбер-счёта, конвертирует в USDT, отдаёт
клиенту его долю (обычно 65%), оставшийся процент делится между
партнёрами в кастомной пропорции, разной на каждую операцию.

Итоговая валюта отчётности — **USDT**.

Бот живёт в ОДНОЙ группе (`MAIN_CHAT_ID`) с whitelisted-юзерами
(`ALLOWED_TG_USER_IDS`), куда команда пишет что сделала. Бот:

1. Читает всё, парсит операции, создаёт preview-карточки ✅/❌.
2. Отвечает на вопросы, делает отчёты, фиксит курсы.
3. Копит базу знаний (алиасы клиентов, правила, предпочтения).
4. Напоминает о просрочках.

---

## 2. Bootstrap — что сделать **первым делом** в новом чате

```bash
# 1. Подтянуть секреты из macOS Keychain (Railway-токен, и т.д.)
source scripts/load-secrets.sh

# 2. Проверить env
echo $RAILWAY_API_TOKEN | head -c 12  # должен показать 12 символов токена

# 3. Если нужен ANTHROPIC_API_KEY / TELEGRAM_BOT_TOKEN / DATABASE_URL —
#    они НЕ в Keychain (пока), подтяни из Railway:
source scripts/load-secrets.sh
PROJECT_ID=befcb51e-c4b4-4c3b-a6d4-7eeba2204d81
SERVICE_ID=3a79891c-3ddc-4e20-9a28-cfc65ed0c60d
ENV_ID=4ce7e1fd-5414-4c30-8505-46ce1ff0c5b7
curl -s -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST https://backboard.railway.com/graphql/v2 \
  -d "{\"query\":\"{ variables(projectId: \\\"$PROJECT_ID\\\", serviceId: \\\"$SERVICE_ID\\\", environmentId: \\\"$ENV_ID\\\") }\"}" \
  | python3 -c "import json,sys; [print(f'{k}={v}') for k,v in json.load(sys.stdin)['data']['variables'].items() if k in ['TELEGRAM_BOT_TOKEN','DATABASE_URL','ANTHROPIC_API_KEY']]"

# 4. Для локального запуска тестов достаточно фейковых значений:
export TELEGRAM_BOT_TOKEN=test12345678901234567890
export ANTHROPIC_API_KEY=sk-test1234567890
export DATABASE_URL=postgresql+asyncpg://fake/test
export ALLOWED_TG_USER_IDS=1 OWNER_TG_USER_ID=1
uv run pytest -q
```

Если нужно задеплоить фикс — см. раздел **Deployment flow** ниже.

---

## 3. Архитектура

### 3.1. Слои

```
┌────────────────────────────────────────────────────────┐
│ Telegram (group chat)                                   │
└────────────────────────┬───────────────────────────────┘
                         │ long-polling (getUpdates)
┌────────────────────────▼───────────────────────────────┐
│ aiogram Dispatcher                                      │
│   outer_middlewares: RateLimit → Whitelist             │
│   inner middleware:  MessageLogging (persist to DB)    │
│   routers (в строгом порядке, см. handlers/__init__.py)│
│     1. commands                                         │
│     2. documents (PDF)                                  │
│     3. photo (Vision)                                   │
│     4. voice (Whisper local)                            │
│     5. stickers                                         │
│     6. mentions (AddressedToMe filter)                 │
│     7. callbacks (✅/❌ inline buttons)                │
│     8. admin                                            │
│     9. messages (catch-all — keyword gate)             │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│ BatchBuffer (src/bot/batcher.py) — per-chat buffer     │
│   triggers: @-mention, reply, keyword, command, PDF,   │
│   sticker_reply, voice-with-keyword                    │
│   → flush_handler (src/core/batch_processor.py)        │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│ batch_analyzer.analyze_batch (src/llm/batch_analyzer)  │
│   → Claude Sonnet 4.5 with tool_use=analyze_batch      │
│   returns BatchAnalysis {operations[], chat_reply,     │
│            sticker_emoji, sticker_description_hint, .} │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────┐
│ core/batch_processor.flush:                             │
│   - for each operation: register pending_op,           │
│     render preview card, send with ✅/❌               │
│   - if chat_reply: send text                           │
│   - if sticker_*: resolve via seen_stickers, send      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Callbacks (✅/❌) — src/bot/handlers/callbacks.py       │
│   on ✅: core/applier.apply_operation → persist via     │
│          intent-specific repository method             │
│   on ❌: drop pending_op, acknowledge                   │
└─────────────────────────────────────────────────────────┘
```

### 3.2. Модули по директориям

| Директория | Что там |
|------------|---------|
| `src/bot/main.py`              | Entrypoint, Dispatcher, long-polling runner |
| `src/bot/batcher.py`           | `BatchBuffer` — per-chat buffering & flush |
| `src/bot/filters/addressed.py` | `AddressedToMe` — True если @-mention/reply |
| `src/bot/middlewares/`         | RateLimit, Whitelist, Logging |
| `src/bot/handlers/`            | Все aiogram-роутеры |
| `src/core/`                    | Бизнес-логика, не привязанная к Telegram |
| `src/core/batch_processor.py`  | Собственно flush-handler: LLM→previews→replies |
| `src/core/applier.py`          | Запись confirmed operations в БД |
| `src/core/pending_ops.py`      | In-memory registry для ✅/❌ flow |
| `src/core/preview.py`          | Рендер preview-карточек |
| `src/core/keyword_match.py`    | Локальный substring-matcher |
| `src/core/voice_transcribe.py` | faster-whisper wrapper |
| `src/core/voice_trigger.py`    | Voice transcribe+keyword→analyzer pipeline |
| `src/core/sticker_describe.py` | Claude Haiku Vision для стикеров |
| `src/core/pdf_ingest.py`       | pdfminer + Sber-detection |
| `src/core/reports.py`          | /report собиратель |
| `src/core/report_formula.py`   | Pure math for wallet totals |
| `src/core/fx.py`               | RUB/USDT rate fetch (Rapira) |
| `src/core/pranks.py`           | Rare shitpost replies (feature-flagged) |
| `src/core/reminders.py`        | APScheduler background jobs |
| `src/core/resync.py`           | On-boot pick up of missed messages |
| `src/core/silent.py`           | /silent toggle state |
| `src/llm/`                     | Claude client + prompt builders |
| `src/llm/client.py`            | Async Anthropic client + retry + cache stats |
| `src/llm/system_prompt.py`     | CORE_INSTRUCTIONS + render_* helpers |
| `src/llm/batch_analyzer.py`    | analyze_batch (main entry) |
| `src/llm/classifier.py`        | (legacy) intent-classifier; used in fallback path |
| `src/llm/parser.py`            | (legacy) per-intent parser; used in fallback path |
| `src/llm/schemas.py`           | `Intent` enum + per-intent pydantic parse models |
| `src/llm/few_shot.py`          | verified-example picker |
| `src/llm/knowledge_base.py`    | KB helpers |
| `src/llm/pipeline.py`          | Legacy one-shot: classify→parse→reply (non-batch) |
| `src/personality/voice.py`     | `PERSONALITY_PROMPT` — тон общения |
| `src/personality/phrases.py`   | Error fallbacks, шутки |
| `src/db/models.py`             | Все ORM-модели (см. раздел 4) |
| `src/db/session.py`            | `session_scope()` async context manager |
| `src/db/repositories/*.py`     | Data access methods (async, take session first) |
| `src/config.py`                | Единая точка правды для env |
| `src/logging_setup.py`         | structlog config |

---

## 4. Data model — полный список таблиц

Все таблицы ниже, их назначение и ключевые поля. Amounts всегда
`Numeric(18,6)`, timestamps всегда `TIMESTAMPTZ`.

### Основные бизнес-таблицы

| Таблица | Назначение | Ключевые поля |
|---------|------------|---------------|
| `users`                  | Участники команды (партнёры, whitelisted) | id, tg_user_id, display_name |
| `partners`               | Партнёры-пайщики в POA | id, name |
| `partner_contributions`  | Доли партнёра по конкретной POA | poa_withdrawal_id, partner_id, pct |
| `partner_withdrawal`     | Когда партнёр забирает USDT из общего кошелька | partner_id, amount_usdt, from_wallet |
| `poa_withdrawal`         | POA-снятие (клиент + сумма + доли) | client_name, amount_rub, client_share_pct |
| `exchange`               | RUB→USDT обмен (Rapira) | amount_rub, amount_usdt, fx_rate |
| `expense`                | Расходы (комиссии, кэш-аут, прочее) | category, amount_rub, amount_usdt, description |
| `cabinet`                | Сбер-кабинеты как инвентарь | name, status, cost_rub, acquired_at |
| `prepayment`             | Предоплата поставщику кабинетов | supplier, amount_rub, expected_cabinets |
| `client`                 | Клиенты (TapBank, Mercurio, POA-клиенты) | name, kind, commission_pct |
| `wallet`                 | Кошельки (TapBank, Mercurio, Rapira, Sber, Cash) | name, currency |
| `wallet_snapshot`        | Снимок балансов на дату | wallet_id, amount, taken_at |
| `fx_rate_snapshot`       | Snapshot курса RUB/USDT | pair, rate, taken_at |

### Вспомогательные

| Таблица | Назначение |
|---------|------------|
| `knowledge_base`         | Learned facts (alias/glossary/entity/rule/pattern/preference) с confidence |
| `few_shot_example`       | Verified examples — pair (input_text, intent, parsed_json) |
| `message_log`            | Все входящие/исходящие сообщения — context window для LLM |
| `audit_log`              | Лог кто-что-когда записал (на applied operations) |
| `feedback`               | /feedback команда — пожелания команды |
| `pending_reminders`      | APScheduler reminders |
| `pending_ops`            | In-flight operations ожидающие ✅/❌ (таблица + in-memory registry) |
| `report`                 | Persisted end-of-day reports |
| `trigger_keywords`       | Слова-триггеры (бот, алкаш, ержан, бухгалтер, цифровой пидорас, ...) |
| `voice_messages`         | OGG-данные голосовых + transcribed_text после Whisper |
| `seen_stickers`          | Инвентарь стикеров с emoji, pack, description (Vision) |
| `sticker_usage`          | Лог каждой отправки стикера — кто, когда, какой, контекст |

### Миграции (chronological)

| ID | Содержит |
|----|----------|
| `b4fbd8da6908` | initial_schema — все бизнес-таблицы |
| `c1a0_0002`    | seed: partners + wallets |
| `d1a0_0004`    | seed: critical knowledge base items |
| `e2a0_0006`    | performance indexes + KB |
| `8fe3ebe5800f` | pending_ops table |
| `b0f5e10ebf32` | seen_stickers table |
| `8d208fabd07a` | voice_messages table |
| `8ac1b7763a40` | trigger_keywords table + seed (8 шт.) |
| `e8e607e380f8` | seed «алкаш», «ержан» |
| `1033b73e2805` | seed Latin variants (alkaz, alkash, erzhan) |
| `486b28e0b462` | sticker_usage table + indexes |
| `7ad47bc5c495` | seen_stickers: description/described_at/description_model |

---

## 5. Intent enum — что бот умеет парсить

Находится в `src/llm/schemas.py::Intent`:

| Intent | Когда используется | Ключевые поля в `fields` |
|--------|--------------------|--------------------------|
| `poa_withdrawal`       | «снял с Никонова 150к, мне 25% Арбузу 10%» | client_name, amount_rub, partner_shares:[{partner,pct}], client_share_pct |
| `exchange`             | «280000/3480=80.46» или «обменял 200к на 2500 USDT» | amount_rub, amount_usdt, fx_rate |
| `cabinet_purchase`     | «купил кабинет на 40к у Миши» | name?, cost_rub, prepayment_ref? |
| `cabinet_worked_out`   | «кабинет Серго отработан» | name_or_code |
| `cabinet_blocked`      | «кабинет Лена заблочили» | name_or_code |
| `cabinet_recovered`    | «Лену разблокировали после нотариалки» | name_or_code |
| `prepayment_given`     | «отдал Мише 100к в предоплату» | supplier, amount_rub, expected_cabinets? |
| `prepayment_fulfilled` | «Миша отдал 3 кабинета за предоплату» | supplier, cabinets:[{name,cost_rub}] |
| `expense`              | «купил симку за 2к», «пропавшие %» | category, amount_rub/amount_usdt, description |
| `partner_withdrawal`   | «Арбуз забрал 500 USDT с Rapira» | partner, amount_usdt, from_wallet? |
| `partner_deposit`      | «Арбуз внёс 200 USDT на Rapira» | partner, amount_usdt |
| `client_payout`        | «отдал клиенту 3400 USDT» | client_name, amount_usdt |
| `wallet_snapshot`      | «TapBank = 15000, Rapira = 8400» | tapbank?, mercurio?, rapira?, sber_balances?, cash? |
| `question`             | «сколько было на Rapira?» | (вопрос → chat_reply, не операция) |
| `feedback`             | «давай добавь тебе `/balance` по партнёру» | content |
| `knowledge_teach`      | «запомни: Арнелле = эквайринг» | category, key?, content |
| `chat`                 | Болтовня без операции | — |
| `unclear`              | Не удалось понять, попросить уточнить | — |

Операции с `confidence < 0.7` или непустым `ambiguities[]` → preview
карточка с ✅/❌, пока юзер не подтвердит — ничего не пишется.

---

## 6. Slash-команды — полный список

Все команды в `src/bot/handlers/commands.py`. Обычно работают
только для whitelisted-юзеров в main group.

| Команда | Что делает |
|---------|-----------|
| `/start`       | Приветствие + регистрация user в DB |
| `/help`        | Список команд |
| `/chatid`      | Возвращает текущий chat_id (для настройки MAIN_CHAT_ID) |
| `/balance`     | Сводка балансов по кошелькам |
| `/stock`       | Что на складе — кабинеты in-inventory |
| `/fx`          | Текущий курс RUB→USDT (Rapira) |
| `/partners`    | Доли партнёров — статистика за период |
| `/history [N]` | Последние N операций (default 10) |
| `/clients`     | Список клиентов |
| `/client <name>` | Детали по клиенту |
| `/debts`       | Долги по клиентам |
| `/undo`        | **Только по явному запросу юзера** — откатывает последнюю операцию со cascade |
| `/report`      | End-of-day отчёт — считает фин. показатели за день |
| `/knowledge`   | Показать базу знаний — что бот запомнил |
| `/keywords [add X / remove ID / list]` | Управление trigger_keywords |
| `/feedback`    | Оставить пожелание команды |
| `/voices`      | (admin) транскрибировать все накопленные голосовые |
| `/resync`      | (admin) переобработать пропущенные сообщения |
| `/silent [on/off]` | Отключить LLM-ответы временно (бот молчит) |
| `/avatar`      | Сменить аватарку группы — reply-ни командой на фото |

---

## 7. Trigger logic — когда бот вообще отвечает

`aiogram` routers матчат в строгом порядке (см. `handlers/__init__.py`).

Бот отвечает **только** при одном из условий:

1. **@-mention** бота (`@Al_Kazbot`) в тексте/caption.
2. **Reply** на любое сообщение бота.
3. **external_reply / quote** (Bot API 7.0+) — Telegram тоже считает это обращением.
4. **Slash-command** (`/balance`, `/fx`, и т.д.) — фильтр `Command(...)`.
5. **Keyword hit** — любое слово из `trigger_keywords` как substring в тексте
   или в транскрипте голосового.
6. **Document (PDF)** — сразу триггерит analyzer с `trigger_kind=document`.
7. **Voice + keyword** — Whisper транскрибирует, matcher проверяет, если hit →
   `trigger_kind=voice_keyword`.

Обычное сообщение без триггеров → записывается в `message_log` для
контекста, но LLM не зовётся.

### `AddressedToMe` filter

`src/bot/filters/addressed.py::AddressedToMe`. Возвращает True если (1)/(2)/(3)
выше. Привязан к `mentions.router`. Всё, что не адресовано — падает в
следующий router, где в `messages.py` уже keyword-gate.

**Баг, который мы фиксили:** раньше `mentions.router` был декорирован
`F.text | F.caption` и проверка `_addressed_to_me` была внутри handler'а.
aiogram матчит роутер ПО ФИЛЬТРУ, и если фильтр матчит — следующий
роутер не пробует. Результат: любой текст уходил в mentions, проверка
возвращала False, routing останавливался. `messages.on_message` с
keyword-gate никогда не срабатывал. Фикс — тот самый `AddressedToMe`
как filter, а не как inline-проверка.

---

## 8. Voice pipeline (Whisper + keyword-gate)

### Flow

```
User voice →
  handlers/voice.on_voice:
    - download OGG bytes via Bot API
    - persist to voice_messages (ogg_data, tg_message_id)
    - spawn asyncio.create_task(transcribe_and_keyword_check(bot, voice_id))
      [fire-and-forget, не блокирует polling]
  transcribe_and_keyword_check (voice_trigger.py):
    1. voice_transcribe.transcribe_voice_row(session, voice_id):
       - load model singleton (faster-whisper 'small', int8, CPU)
       - извлечь OGG bytes
       - BUILD initial_prompt из keyword_match.get_active_keywords()
         • только cyrillic keywords (см. _is_cyrillic_word)
         • добавить lead "Разговор в Telegram-чате про Сбер-кабинеты..."
       - model.transcribe(path, language='ru', vad_filter=True,
                         initial_prompt=prompt)
       - save transcribed_text, wipe ogg_data,
         mirror в message_log как '[voice] <text>' с intent='voice_transcript'
    2. keyword_match.find_hits(text):
       - если hits == [] → silent, LLM не вызывается
       - если hits != [] → строим Batch(trigger=BufferedMessage(
         text=f'[voice] {text}'), trigger_kind='voice_keyword')
    3. make_flush_handler(bot)(batch) → тот же flow что для текста.
```

### Whisper model

* `small` multilingual int8 — baseline trade-off скорость/качество.
* Скачивается в образ контейнера на build-time (Dockerfile строка 32).
* Cache dir: `/app/.whisper-cache` (env `FASTER_WHISPER_CACHE_DIR`).
* Runs на CPU (Railway без GPU).

### Latin-bias / initial_prompt

Whisper для коротких русских слов иногда транслитерирует в латиницу:
«Алкаш» → «Alkaz», «Бот» → «Бод/Вот». Фиксы:

1. `_build_whisper_prompt(keywords)` отфильтровывает ASCII-tokens
   (в hint остаются только чисто cyrillic строки).
2. Lead-фраза «Разговор в Telegram-чате…» биасит модель к русскому стилю.
3. В `trigger_keywords` заранее засиден Latin-варианты («alkaz», «alkash»,
   «erzhan») — подстраховка на случай если Whisper всё равно напишет латиницей.

---

## 9. PDF pipeline (выписки Сбера)

### Flow

```
User uploads PDF →
  handlers/documents.on_pdf:
    - проверить что mime_type == application/pdf
    - ответить "принял, разбираю 10-20 сек"
    - download → pdfminer.high_level.extract_text (to_thread, CPU-bound)
    - is_sber_statement(text)? (маркеры: sberbank.ru, СберБанк, ...)
    - собрать header = f"[PDF-документ: name, size]\n" +
                       (SBER_HINT + "\n\n" if is_sber else "") +
                       text (capped 60k chars)
    - BufferedMessage(trigger_kind='document', text=header)
    - flush_now → analyze_batch → Claude видит SBER_HINT + весь текст
```

### ВАЖНО: analyze, не ingest

Политика по умолчанию (см. SBER_HINT и BATCH_INSTRUCTION): **не создавать
operations из строк выписки**. Просто ответить в `chat_reply` короткой
сводкой (даты, приходы, расходы, остаток, notable items). Весь текст
остаётся в recent_history для follow-up вопросов.

**Когда всё же парсить:** только если юзер явно сказал «запиши»,
«внеси», «оформи операции», «занеси в учёт» и т.п. — в том же сообщении
с PDF или следующим триггером.

### Разметка строк (для справки когда всё же парсить)

* `+N от ВТБ / Т-Банк / Озон …` — поступление клиента на `sber_balances`,
  агрегируй в один `wallet_snapshot`.
* `Выдача наличных ATM …` — internal transfer sber_balances → cash
  (пока `expense` с `category='cash_withdrawal'`, потом сделаем отдельный
  intent для internal transfers).
* Мелкие «Прочие расходы» (<5000₽, личные покупки) — **игнорируй**.
* Крупные «Прочие расходы» (≥5000₽, комиссии/переводы контрагенту) —
  `expense` с `category='commission'` или `'other'`.

---

## 10. Sticker pipeline

### 10.1. Ingest

Когда любой whitelisted юзер шлёт стикер в main-group:

```
handlers/stickers.on_sticker:
  1. sticker_repo.upsert(...) — один стикер
  2. bot.get_sticker_set(st.set_name) → развернуть весь пак
     и upsert каждого в seen_stickers
  3. pull preceding ~3 messages из message_log → preceding_text
     sticker_repo.log_usage(sent_by_bot=False, preceding_text=...)
  4. asyncio.create_task(_describe_new_pack())
     — фоном описать каждый новый статичный стикер через Haiku Vision
```

### 10.2. Describe (Vision)

`src/core/sticker_describe.py::describe_one(bot, sticker_id)`:

```
if already has description → return
if is_animated (TGS) or is_video (WebM) → skip (no pipeline)
bot.get_file + bot.download → WebP bytes
base64 encode → anthropic.messages.create(
    model=settings.anthropic_fallback_model,  # claude-haiku-4-5
    messages=[{role: user, content: [image, text-prompt]}]
)
DESCRIBE_PROMPT: "Опиши что изображено на этом стикере в 1-2 коротких
                 фразах на русском. Укажи объекты, эмоции, текст."
Update seen_stickers.description, described_at, description_model.
```

Стоимость: Haiku ~$0.0003 на стикер. 298 статичных → ~$0.09 за полный
бэкфилл.

### 10.3. Backfill worker

`describe_missing(bot)` — idempotent, бегает на старте в background task
(`src/bot/main.py::_startup_describe_stickers`). Проходит по всем
`seen_stickers WHERE description IS NULL AND NOT is_animated AND NOT is_video`
с паузой 0.8 сек между вызовами.

### 10.4. Send path

`_maybe_send_sticker(bot, chat_id, reply_to, emoji, description_hint, pack_hint)`
в `batch_processor.py`:

```
sticker_repo.pick_smart(
    session,
    emoji=?,              # exact match, с ZWJ-стриппингом
    description_hint=?,   # ILIKE '%...%' на description
    pack_hint=?,          # ILIKE '%...%' на sticker_set
    limit=25
)
# пересечение фильтров, берёт low-usage_count половину с 70% prob
# чтобы не спамить одним и тем же.
```

Fallback цепочка: если все три фильтра → пусто, попробовать без hints,
потом без emoji. Если всё равно nothing — тихо skip.

После отправки:
* `bump_usage(sticker_id)` — обновить usage_count
* `log_usage(sent_by_bot=True)` — в sticker_usage
* `log_bot_reply(...)` — в message_log чтобы Claude в следующем analyzer
  видел что стикер уже отправлен.

### 10.5. Tool fields (что Claude возвращает)

В `BatchAnalysis`:
* `sticker_emoji: str | None`            — например "🏢"
* `sticker_description_hint: str | None` — например "офис", "деньги"
* `sticker_pack_hint: str | None`        — например "kontorapidarasov"

Любая комбинация. Ни один не обязателен.

### 10.6. Что Claude видит в prompt

Блок `# Стикеры` (`render_sticker_context`):
1. **Доступный emoji-спектр** — все emoji которые резолвятся в реальные стикеры
2. **Каталог по сюжету** — per-pack список с эмодзи + описанием:
   ```
   ### `kontorapidarasov` (120 описаны)
     - 🏢 — неоновая вывеска КОНТОРА ПИДАРАСОВ на красном фоне
     - 💰 — мешок с деньгами, знак рубля, довольная морда
     ...
   ```
3. **Живые примеры** — последние 10 человеческих отправок с
   preceding-контекстом («Казах отправил 🏢 после: `бот: Просбер — держи`»)

### 10.7. Что НЕ описываем

* TGS (Lottie JSON) — рендер требует отдельной либы.
* WebM video — нужен ffmpeg в Dockerfile + первый кадр.
* 26 TGS + 327 WebM остаются без description (их всё равно можно
  выбирать по emoji).

---

## 11. Knowledge base

`knowledge_base` таблица. Категории:

* **alias** — `key=короткая форма`, `content=канон`. Пример: `"Арнелле" → acquiring`.
* **entity** — `key=имя`, `content=описание`. «Миша Архангельск — раз в 2 нед, 50-150к».
* **rule** — бизнес-правило, без key.
* **glossary** — `key=термин`, `content=значение`.
* **pattern** — типовая формулировка без key.
* **preference** — как юзер хочет чтобы бот работал.

Confidence: `inferred < tentative < confirmed`.

Writes только через preview-card (juser ✅ / ❌). Исключение: явный
«запомни ...» → `KNOWLEDGE_TEACH` intent, preview-карточка всё равно
есть для контроля.

Learning flow:
* Если Claude ошибся → юзер поправил → `inferred` факт.
* Если юзер повторил исправление → `tentative`.
* `/knowledge` команда или ✅ preview → `confirmed`.

---

## 12. LLM integration (Claude)

### 12.1. System prompt blocks

`src/llm/system_prompt.py::build_system_blocks()` возвращает `list[dict]`
для `system=` параметра Claude API. Блоки (в порядке, первые 4 кешируются):

1. **CORE_INSTRUCTIONS** — статичный текст: бизнес-контекст, personality,
   capability-matrix, правила для выписок, правила для стикеров.
2. **Knowledge base** — `render_knowledge_base(knowledge_items)` — все
   confirmed + tentative + inferred факты из `knowledge_base`.
3. **Few-shot examples** — `render_few_shot(examples)` — верифицированные
   примеры парсинга по intent-ам (по 2 на intent для top-8 intent-ов).
4. **Стикеры** — `render_sticker_context(pack_emojis, described_catalog,
   usage_examples)` — emoji-спектр + каталог с описаниями + живые примеры.
5. **Recent chat** — НЕ кешируется, меняется каждый запрос.
   `_recent_history(chat_id)` — последние ~30 сообщений из message_log
   (включая bot replies, с меткой `(голосовым)` для voice transcripts).

### 12.2. Caching

`cache_control={"type": "ephemeral"}` на первых 4 блоках. Cache TTL
Claude ~5 минут. Cache-miss happens когда:
* knowledge_base обновлён (новый ✅ факт)
* sticker library изменилась (новые описания)
* few_shot examples обновлены
* CORE_INSTRUCTIONS изменились (релиз с изменениями prompt)

При нормальном использовании cache hit rate ~80-90% — это сильно
снижает стоимость.

### 12.3. Models

* **Main**: `claude-sonnet-4-6` (env `ANTHROPIC_MODEL`) — для `analyze_batch`.
* **Fallback/Vision**: `claude-haiku-4-5` (env `ANTHROPIC_FALLBACK_MODEL`)
  — для sticker describe. Может быть использован как fallback в
  `pipeline.py` при rate-limit на main.

### 12.4. Retries

`src/llm/client.py` использует tenacity. Retryable: 5xx, 429, APIConnectionError,
APITimeoutError. Max 3 attempts, exponential backoff ~30 сек total.

---

## 13. Whitelist & security

### 13.1. Middleware chain

`src/bot/main.py`:
```python
dp.message.outer_middleware(RateLimitMiddleware())      # 1. первый — самая дешёвая отсечка
dp.message.outer_middleware(WhitelistMiddleware())       # 2. всё что не whitelisted — дроп
dp.callback_query.outer_middleware(WhitelistMiddleware())  # то же для ✅/❌
dp.message.middleware(MessageLoggingMiddleware())        # 3. inner — persist в message_log
```

### 13.2. WhitelistMiddleware logic

```python
allowed_user = user_id in settings.allowed_tg_user_ids
main_group = settings.main_chat_id and chat_id == settings.main_chat_id
if allowed_user or main_group:
    proceed
else:
    log rejected, drop silently
```

**Known gap:** если юзер не в `ALLOWED_TG_USER_IDS` но пишет в main group,
проходит. Handlers делают доп. проверку `_is_whitelisted`, но это хрупко.
Отслеживается в TODO.

### 13.3. Secrets

**НЕ в git.** Хранятся в:
* Railway → Variables (prod env)
* macOS Keychain (для local dev) — `scripts/secrets.sh`, `scripts/load-secrets.sh`

---

## 14. Environment variables — полный список

### Required

| Var | Purpose |
|-----|---------|
| `TELEGRAM_BOT_TOKEN`   | от @BotFather |
| `ANTHROPIC_API_KEY`    | console.anthropic.com |
| `DATABASE_URL`         | postgres dsn (driver нормализуется в `postgresql+asyncpg://`) |

### Strongly recommended

| Var | Purpose |
|-----|---------|
| `MAIN_CHAT_ID`         | int, chat_id группы. Без него бот ни на что не реагирует в групповых чатах. Получить через `/chatid`. |
| `ALLOWED_TG_USER_IDS`  | CSV-список tg user id-шников. Пример: `6885525649,7220305943` |
| `OWNER_TG_USER_ID`     | Главный юзер (Казах). Используется в некоторых местах для приоритизации. |

### Optional

| Var | Default | Purpose |
|-----|---------|---------|
| `APP_ENV`                  | `dev` | `dev` / `prod` |
| `LOG_LEVEL`                | `INFO` | structlog уровень |
| `ANTHROPIC_MODEL`          | `claude-sonnet-4-6` | главная модель |
| `ANTHROPIC_FALLBACK_MODEL` | `claude-haiku-4-5` | для Vision / rate-limit fallback |
| `SENTRY_DSN`               | None | Sentry integration |
| `ENABLE_PRANKS`            | `false` | случайные шутки бота (раз в час, см. `core/pranks.py`) |
| `HYBRID_LISTEN_MODE`       | `true` | legacy-флаг, фактически не используется, можно выпилить |
| `FASTER_WHISPER_CACHE_DIR` | `/app/.whisper-cache` | где Whisper хранит модель |
| `HF_HOME`                  | `/app/.whisper-cache` | для HF-трансформер-кеша (не используется активно) |

### Railway-injected (автоматом)

* `PORT`
* `RAILWAY_PROJECT_ID`, `RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT_ID`
* `RAILWAY_PRIVATE_DOMAIN`
* `RAILWAY_PROJECT_NAME`, `RAILWAY_SERVICE_NAME`, `RAILWAY_ENVIRONMENT_NAME`

---

## 15. Secrets management — macOS Keychain

### 15.1. Скрипты

* `scripts/secrets.sh` — обёртка над `/usr/bin/security`:
  * `set <name> <value>` — сохранить (с `-T /usr/bin/security` для silent-access)
  * `set-prompt <name>` — запросить значение (не засветит в history)
  * `get <name>` — stdout plaintext
  * `list` — имена всех AlKazBot-секретов
  * `remove <name>` — удалить

* `scripts/load-secrets.sh` — source-friendly. Читает Keychain, экспортит
  env-vars по таблице `MAPPING`:
  ```
  railway-token       → RAILWAY_API_TOKEN, RAILWAY_TOKEN
  anthropic-api-key   → ANTHROPIC_API_KEY
  telegram-bot-token  → TELEGRAM_BOT_TOKEN
  prod-database-url   → DATABASE_URL
  ```

### 15.2. Добавление нового секрета

1. `./scripts/secrets.sh set-prompt my-secret-name`  (введи значение)
2. Открыть `scripts/load-secrets.sh`, дописать в `MAPPING`:
   `"my-secret-name=MY_ENV_VAR"`
3. В новом shell → `source scripts/load-secrets.sh`

### 15.3. Безопасность

* Значения лежат в login Keychain, шифруются ключом, который разблочен
  только при логине пользователя.
* `-T /usr/bin/security` в ACL → `security` CLI whitelisted → чтение
  silent (после одного `Always Allow` на первый доступ).
* В git попадают только скрипты, сами значения — никогда.

---

## 16. Railway infrastructure

### 16.1. Project IDs (для копипаста)

| Ресурс | id |
|--------|-----|
| project `peaceful-eagerness`       | `befcb51e-c4b4-4c3b-a6d4-7eeba2204d81` |
| service `AlKazBot`                 | `3a79891c-3ddc-4e20-9a28-cfc65ed0c60d` |
| service `Postgres`                 | `4eecb00e-ad0b-4f11-9a22-ecd4e531c630` |
| environment `production`           | `4ce7e1fd-5414-4c30-8505-46ce1ff0c5b7` |

### 16.2. Telegram-specific

| Что | Значение |
|-----|----------|
| bot telegram id    | `8634741067` |
| bot username       | `@Al_Kazbot` |
| main chat id       | `-1003987039917` (группа «АлКаз крутит сбер») |
| whitelisted users  | `6885525649` (Казах), `7220305943` (Арбуз) |

### 16.3. GraphQL API (Railway)

Endpoint: `https://backboard.railway.com/graphql/v2`
Auth: `Authorization: Bearer $RAILWAY_API_TOKEN`

**Полезные queries:**

```graphql
# Список проектов
{ projects { edges { node { id name } } } }

# Проект с сервисами и средами
{ project(id: "befcb51e-...") {
  name
  services { edges { node { id name } } }
  environments { edges { node { id name } } }
}}

# Последние деплои
{ deployments(first: 5, input: {
  serviceId: "3a79891c-...",
  environmentId: "4ce7e1fd-..."
}) { edges { node { id status createdAt meta } } } }

# Логи конкретного деплоя
{ deploymentLogs(deploymentId: "<uuid>", limit: 200) {
  timestamp message severity
}}

# Env vars сервиса
{ variables(
  projectId: "befcb51e-...",
  serviceId: "3a79891c-...",
  environmentId: "4ce7e1fd-..."
)}
```

### 16.4. Примеры bash-команд

```bash
source scripts/load-secrets.sh

# Статус последнего деплоя
curl -s -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST https://backboard.railway.com/graphql/v2 \
  -d '{"query":"{ deployments(first: 1, input: { serviceId: \"3a79891c-3ddc-4e20-9a28-cfc65ed0c60d\", environmentId: \"4ce7e1fd-5414-4c30-8505-46ce1ff0c5b7\" }) { edges { node { status meta } } } }"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); n=d['data']['deployments']['edges'][0]['node']; print(f\"{n['meta']['commitHash'][:8]}: {n['status']}\")"

# Последние 50 строк логов
DEP=<uuid последнего деплоя>
curl -s -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST https://backboard.railway.com/graphql/v2 \
  -d "{\"query\":\"{ deploymentLogs(deploymentId: \\\"$DEP\\\", limit: 50) { timestamp message } }\"}" \
  | python3 -c "import json,sys; [print(f\"{l['timestamp'][11:19]} {l['message']}\") for l in json.load(sys.stdin)['data']['deploymentLogs']]"
```

### 16.5. Monitor tool (для Claude Code)

Удобно использовать `Monitor` tool с `until` loop:

```bash
until cur=$(curl ... graphql ... deployments ... | parse_status);
      [ "$cur" != "$last" ] && echo "$cur";
      last=$cur;
      case "$cur" in *:SUCCESS|*:FAILED) exit 0;; esac;
      sleep 15;
done
```

Есть готовые примеры в `инструкции/СЕССИЯ_2026-04-19.md`.

---

## 17. Deployment flow

```
дев → git push origin main → Railway webhook → GitHub → Dockerfile build →
  → container startup (scripts/entrypoint.py) →
    1. env-snapshot marker
    2. probe_db (DNS + TCP)
    3. alembic upgrade head
    4. run_bot → src.bot.main:main()
       _runner():
         5. configure_logging, _init_sentry
         6. Bot() + Dispatcher()
         7. get_batch_buffer(make_flush_handler(bot))
         8. add middlewares (RateLimit, Whitelist, Logging)
         9. include router (root → all sub-routers)
         10. set_my_commands
         11. start_scheduler (APScheduler, background jobs)
         12. asyncio.create_task(_startup_resync)           # missed-message catch-up
         13. asyncio.create_task(_startup_describe_stickers) # backfill Haiku descriptions
         14. dp.start_polling(bot, allowed_updates=...)
         15. await stop_event (SIGTERM handling)
```

### Graceful shutdown

* SIGINT / SIGTERM → `stop_event.set()`
* scheduler.shutdown(wait=False)
* dp.stop_polling() + cancel polling_task
* drain in-flight batch flushes (до 15 сек)
* bot.session.close()

---

## 18. Local dev

### 18.1. Setup

```bash
# Установить uv если не стоит
curl -LsSf https://astral.sh/uv/install.sh | sh

# Все deps
uv sync --all-extras

# Proof of life
uv run pytest -q
```

### 18.2. Запустить бота локально (с прод-DB через proxy)

```bash
source scripts/load-secrets.sh  # если забыл запушить DATABASE_URL в Keychain — подтяни из Railway
export MAIN_CHAT_ID=-1003987039917
export ALLOWED_TG_USER_IDS=6885525649,7220305943
export OWNER_TG_USER_ID=6885525649
uv run python -m src.bot.main
```

**ВНИМАНИЕ:** если одновременно запустить локально и на Railway — оба
будут ловить updates через long-poll, Telegram будет распределять
случайно. Для безопасной локальной работы — `/silent on` или
временный Telegram test-bot.

### 18.3. Миграции

```bash
# Применить все up
uv run alembic upgrade head

# Создать новую миграцию
uv run alembic revision -m "my migration name"
# Файл в alembic/versions/<hash>_my_migration_name.py

# Откатить на N шагов
uv run alembic downgrade -<N>

# Показать current
uv run alembic current
```

### 18.4. Тесты

```bash
# Все
uv run pytest -q

# Один файл
uv run pytest tests/test_keyword_match.py -v

# С coverage (не настроено глобально, запускай руками)
uv run pytest --cov=src
```

### 18.5. Lint / format

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/  # менее строго; много legacy-кусков не типизированы
```

---

## 19. Commit style & conventions

```
<type>(<scope?>): <subject — imperative, low-case>

<body — what and WHY, не how>

<footer>
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Types в использовании: `feat`, `fix`, `docs`, `refactor`, `test`,
`chore`. Scope опционально: `mentions`, `voice`, `stickers`, `pdf`.

Примеры из истории:
* `fix(mentions): stop shadowing messages router`
* `feat(voice): bias Whisper with keyword prompt + add алкаш/ержан`
* `feat(stickers+pdf): Vision descriptions + don't auto-parse statements`

---

## 20. Troubleshooting runbook

### 20.1. Бот не отвечает на сообщения

1. `source scripts/load-secrets.sh`
2. Проверить последний deploy: status должен быть SUCCESS (см. 16.4).
3. `getMe` через Telegram API:
   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"
   ```
   Бот должен быть alive, `can_read_all_group_messages: true`.
4. `getChatMember` — проверить что бот admin в главной группе:
   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getChatMember?chat_id=$MAIN_CHAT_ID&user_id=<BOT_ID>"
   ```
   Должно вернуть `status: 'administrator'`.
5. Логи деплоя: ищи `Update id=... is handled`. Если нет — Telegram не
   доставляет апдейты (скорее всего бот не админ или MAIN_CHAT_ID другой).
6. DB check: `SELECT COUNT(*) FROM message_log WHERE created_at > NOW() -
   INTERVAL '1 hour';` — если 0, сообщения не доходят.
7. Если сообщения доходят, но бот молчит:
   * Проверить `trigger_keywords` — есть ли нужные слова?
   * Если есть keyword hit должен быть, логи: `keyword_trigger hits=[...]`
   * Если есть, но ответа нет: `batch_flush_handler_failed` — смотреть exception.

### 20.2. Voice не транскрибируется

1. Логи: `loading_whisper_model` должно быть на старте контейнера.
2. `voice_messages.transcribed_text` должно заполняться. Если NULL —
   whisper упал, смотреть `voice_transcribe_failed` exception.
3. Модель потеряна? `.whisper-cache/` не смонтирован? — проверить в
   `ls /app/.whisper-cache/` (через `railway shell` если доступно).

### 20.3. Migration failed на деплое

* Entrypoint выходит с non-zero exit если alembic fail.
* Посмотреть `[entrypoint] alembic exit=<N>` в логах.
* Фикс: в локальной базе попробовать `alembic upgrade head`, поправить
  миграцию, запушить. Railway перекатит.
* Если миграция уже частично применилась (commit бывает), руками
  `UPDATE alembic_version SET version_num = '<previous>';` и почистить
  созданные таблицы/колонки.

### 20.4. Sticker vision не работает

* Первый запуск после `7ad47bc5c495` деплоя: backfill идёт 5-10 минут.
  Логи: `sticker_described` строки.
* `SELECT COUNT(*) FROM seen_stickers WHERE description IS NOT NULL;` —
  растёт?
* Если нет — смотреть `vision_call_failed` в логах. Скорее всего
  `ANTHROPIC_API_KEY` не валиден.

### 20.5. PDF парсит всё в операции хотя не должен

* Проверить что последний деплой включает коммит
  `feat(stickers+pdf)` или позже. `SBER_HINT` в `src/core/pdf_ingest.py`
  должен содержать блок `ВАЖНО — ПО УМОЛЧАНИЮ НЕ СОЗДАВАЙ ОПЕРАЦИИ`.

---

## 21. Как добавить что-то новое

### 21.1. Новый intent

1. `src/llm/schemas.py::Intent` — добавить enum-член.
2. Если есть специфическая parse-schema (на интент с сложным payload):
   написать pydantic-модель рядом.
3. `src/llm/batch_analyzer.py::ANALYZE_TOOL.input_schema` — описать
   fields в `description` поля `fields`.
4. `src/core/applier.py` — добавить branch в `apply_operation` для
   записи этого intent в БД.
5. Репозиторий: `src/db/repositories/<new_intent>.py` — CRUD.
6. Если нужна новая таблица: модель в `db/models.py` + миграция.
7. Тесты: `tests/test_<new_intent>.py`.

### 21.2. Новая slash-команда

1. `src/bot/handlers/commands.py` — новый handler:
   ```python
   @router.message(Command("mycmd"))
   async def cmd_my(message: Message) -> None:
       ...
   ```
2. Добавить в `src/bot/main.py::BOT_COMMANDS` для `bot.set_my_commands`.
3. Обновить `CLAUDE.md` capability-matrix в system_prompt.
4. Обновить секцию 6 этого README.

### 21.3. Новое keyword для trigger

Вариант 1 (ad-hoc, через бота):
```
/keywords add <слово>
```

Вариант 2 (для сида, воспроизводимо):
Добавить миграцию аналогично `e8e607e380f8_trigger_keywords_alkash_erzhan.py`.

### 21.4. Новый секрет для Keychain

См. раздел 15.2.

### 21.5. Новое tool-поле для Claude в batch output

1. `src/llm/batch_analyzer.py::BatchAnalysis` — добавить pydantic field.
2. `ANALYZE_TOOL.input_schema.properties` — добавить JSON Schema entry с
   `description` объясняющим что это и когда использовать.
3. `src/core/batch_processor.flush` — обработать новое поле.

---

## 22. Known issues & TODO

### 22.1. Whitelist bypass via MAIN_CHAT_ID

`WhitelistMiddleware` пропускает любой update из main group, даже если
user_id не в `ALLOWED_TG_USER_IDS`. Handlers делают доп. проверку, но
это хрупко. Надо tighten middleware.

### 22.2. Dead code в batcher

`BatchBuffer._reset_timer` / `_age_flush` больше никто не зовёт (после
keyword-gating). Можно удалить вместе с полями `_timers`.

### 22.3. TGS + WebM стикеры без описаний

* 26 TGS (Lottie JSON) — нужен lottie-py рендерер → PNG → Vision.
* 327 WebM — нужен ffmpeg в Dockerfile (`apt-get install -y ffmpeg`) +
  первый кадр через ffmpeg cli → Vision.

### 22.4. Middleware не маркирует стикеры как has_media

`MessageLoggingMiddleware._persist` считает `has_media` по
photo/document/video/voice/audio, не по sticker. Косметика, но лучше
исправить.

### 22.5. `/keywords` help

Команда без аргументов показывает список, но без подсказки синтаксиса.
Добавить «usage: /keywords add <слово> | /keywords remove <id>».

### 22.6. Sticker usage learning может быть умнее

Сейчас в system prompt 10 последних human-отправок. Можно ранжировать
по cosine-similarity preceding_text vs текущий контекст (нужны
embeddings + векторный индекс, например pgvector).

### 22.7. `hybrid_listen_mode` unused

Config-флаг объявлен, нигде не читается. Удалить.

### 22.8. Legacy pipeline.py

`src/llm/pipeline.py::process_message` — старый flow без batch (classify
→ parse → reply). Используется только в `mentions.on_mention`
non-main-group fallback. Можно удалить вместе с `classifier.py` /
`parser.py`, просто всегда использовать `analyze_batch`.

---

## 23. Глоссарий

| Термин | Что значит |
|--------|-----------|
| POA            | Power Of Attorney — доверенность, тип операции |
| Откуп          | Когда команда «откупает» клиентские рубли за USDT |
| Кабинет        | Сбер-счёт, используется как одноразовый канал |
| Отработан      | Кабинет завершил цикл — больше не используется |
| Нотариалка     | Восстановление заблокированного кабинета через нотариуса |
| Rapira         | Криптобиржа для конвертации RUB→USDT |
| TapBank        | Эквайер, даёт клиентские рубли |
| Mercurio       | Второй эквайер |
| Арбуз          | Один из партнёров (tg_user_id=7220305943) |
| Казах          | Главный партнёр (tg_user_id=6885525649) |
| АлКаз          | Команда = Арбуз + Казах |

---

## 24. Changelog pointer

Основные релизы с описанием что прилетело:
см. `CHANGELOG.md` для полного списка.

Последние крупные вехи (на момент написания):
* **2026-04-19** — keyword-gated triggers, router-shadow fix, Whisper
  Latin-bias fix, capability matrix, стикеры send+learn+Vision,
  PDF analyze-only by default.

---

## 25. Контакты и владельцы

* **Product owner**: Казах (tg=6885525649)
* **Code owner**: тот же + Claude (`@Al_Kazbot` сам — как исполнитель)
* **GitHub**: https://github.com/daanameporia-ux/AlKazBot
* **Railway**: https://railway.com/ → project `peaceful-eagerness`

---

*Этот README — живой. Если что-то устарело или не соответствует коду —
открой PR или обнови напрямую. Чем он точнее, тем быстрее онбординг
следующего Claude / человека.*
