# Sber26 Accounting Bot — Спецификация

## TL;DR для Claude Code

Ты разрабатываешь Telegram-бота для управленческого учёта процессинг-бизнеса. Команда — 2-5 человек, основные юзеры — два партнёра (Казах и Арбуз). Бот живёт в групповом чате, реагирует на упоминания, понимает свободную речь через Claude API.

**Главные принципы разработки (важнее всего):**

1. **Автономность.** Работай долго и самостоятельно. Не дёргай юзера по мелочам. Решай сам там где решение очевидное, документируй решения в `DECISIONS.md`. Спрашивай только там где выбор реально меняет бизнес-логику.
2. **Минимум инпута от юзера.** Юзер — не программист, он дал тебе спеку и ждёт готовый продукт. Когда тебе что-то нужно — спрашивай кратко, конкретно, с вариантами. Не заваливай вопросами "а как бы вы хотели чтобы кнопочка..."
3. **Чёткие простые инструкции для юзера.** Всё что юзер должен сделать сам (получить API-ключ, настроить Railway, зарегать бота в BotFather, дать доступы) — оформляй пошагово, с примерами команд, скриншотов-комментариев типа "в меню выбери вот это".
4. **Огромная обучаемость бота.** Это ключевая фича. Подробнее ниже в разделе «Обучаемость».

## Бизнес-контекст (чтобы ты понимал что кодишь)

Команда занимается обработкой платежей: принимают рубли от клиентов через платёжки (TapBank, Mercurio), «разливают» через Сбер-кабинеты, снимают наличкой, обменивают на USDT в Rapira, возвращают клиентам USDT за вычетом 7% комиссии. Чистый спред ~6.25%.

Также есть поток «снятие по доверенности»: клиент даёт доверенность, команда снимает со счёта клиента рубли, обменивает в USDT, отдаёт клиенту долю (обычно 65%), оставляет себе комиссию (обычно 35%) — которая распределяется между партнёрами **каждый раз в разных пропорциях** (это критично).

**Оборотный капитал** разложен по локациям:
- TapBank (USDT)
- Mercurio (USDT)  
- Rapira (USDT)
- Сбер-реквизиты (RUB, балансы на кабинетах до снятия)
- Наличные (RUB)

**Материалы** — конкретные Сбер-кабинеты, каждый со своей индивидуальной стоимостью. Склад ведётся по экземплярам.

**Отчётная валюта — USDT.**

Пример рабочего отчёта (формат, который надо генерить):
```
На вечер 17.04.2026:

Депозиты (вложения партнёров):
  Казах 3600 / +905 (от снятий)
  Арбуз 1500 / +478 (от снятий)

Оборотка: 6974$
  Merk:     48$
  Tpay:     5219$ + 1000$
  Нал:      5000₽ / 80.5 = 62$

Материал (склад): 372$
  Аляс полный     310$
  Баба без дов.    62$

Долги:
  Предоплата 22000₽ = 273$

Чистая прибыль: 6974 - 3600 - 1500 - 905 - 478 = 491$
```

## Стек

- **Python 3.12**
- **aiogram 3.x** — Telegram
- **Postgres 16** — данные (Railway managed)
- **SQLAlchemy 2.x async + asyncpg + Alembic** — ORM и миграции
- **anthropic** Python SDK — работа с Claude API
- **pydantic 2.x** — валидация LLM-ответов
- **httpx** — HTTP если нужен
- **structlog** — логи
- **Sentry** — ошибки
- **pytest + pytest-asyncio** — тесты

Хостинг — **Railway** (у юзера уже есть аккаунт).

## LLM конфигурация

**Модель:** `claude-sonnet-4-6` (основная, для парсинга и отчётов)  
**Fallback для простого:** `claude-haiku-4-5` (опционально, когда уверенно распознали паттерн)  
**API:** Anthropic, стандартный `/v1/messages` endpoint

**Prompt caching — обязательно.** Системный промпт с knowledge base, моделью данных и инструкциями — большой (5-10K токенов). Кэшируй его через `cache_control: {"type": "ephemeral"}`. Это сэкономит 60-70% стоимости.

Пример структуры запроса:
```python
messages.create(
    model="claude-sonnet-4-6",
    system=[
        {
            "type": "text",
            "text": SYSTEM_PROMPT_STATIC,  # инструкции, схема
            "cache_control": {"type": "ephemeral"}
        },
        {
            "type": "text", 
            "text": knowledge_base_rendered,  # факты про бизнес
            "cache_control": {"type": "ephemeral"}
        },
        {
            "type": "text",
            "text": recent_context  # последние сообщения
        }
    ],
    messages=[...]
)
```

**Стоимость по прикидкам:** при 100-200 операций/день + prompt caching = $10-20/мес.

**API-ключ** — в `.env` как `ANTHROPIC_API_KEY`. Отдельный от Max-подписки юзера.

## Структура проекта

```
sber26-bot/
├── alembic/
├── src/
│   ├── bot/
│   │   ├── main.py              # entrypoint
│   │   ├── handlers/
│   │   │   ├── messages.py      # основной: парсит все сообщения в чате
│   │   │   ├── mentions.py      # @бот ...
│   │   │   ├── commands.py      # /report, /balance, /knowledge и т.п.
│   │   │   ├── callbacks.py     # inline-кнопки
│   │   │   └── admin.py         # админские штуки (см. ниже)
│   │   ├── middlewares/
│   │   │   ├── auth.py          # whitelist tg_user_id
│   │   │   └── logging.py
│   │   └── keyboards.py
│   ├── core/
│   │   ├── operations/          # разные типы операций
│   │   │   ├── poa_withdrawal.py # снятие по доверенности
│   │   │   ├── exchange.py       # обмен RUB→USDT
│   │   │   ├── cabinet.py        # операции с кабинетами
│   │   │   ├── prepayment.py     # предоплаты
│   │   │   ├── expense.py        # расходы (эквайринг и пр.)
│   │   │   ├── partner_op.py     # depo/withdraw партнёров
│   │   │   └── snapshot.py       # снапшот балансов
│   │   ├── reports.py           # генерация отчёта
│   │   ├── fx.py                # работа с курсами
│   │   └── reminders.py         # автоматические напоминания
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── repositories/
│   ├── llm/
│   │   ├── client.py            # обёртка над Anthropic API
│   │   ├── system_prompt.py     # сборка системного промпта
│   │   ├── knowledge_base.py    # управление KB
│   │   ├── few_shot.py          # примеры для обучения
│   │   ├── classifier.py        # определение intent сообщения
│   │   ├── parser.py            # разбор в структуру
│   │   └── schemas.py           # pydantic
│   ├── personality/
│   │   ├── voice.py             # тон бота, шутки
│   │   └── phrases.py           # шаблоны ответов
│   └── config.py
├── tests/
├── pyproject.toml
├── railway.toml
├── alembic.ini
├── .env.example
├── SETUP.md                     # инструкция для юзера (см. раздел)
├── DECISIONS.md                 # решения которые ты принял автономно
└── README.md
```

## Модель данных

```sql
-- Партнёры
CREATE TABLE partners (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,       -- 'Казах', 'Арбуз'
    tg_user_id    BIGINT UNIQUE,
    is_active     BOOLEAN DEFAULT TRUE
);

-- Юзеры (включая партнёров + помощников)
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    tg_user_id    BIGINT UNIQUE NOT NULL,
    tg_username   TEXT,
    display_name  TEXT,
    role          TEXT CHECK (role IN ('partner','assistant','viewer')),
    partner_id    INT REFERENCES partners(id),  -- если role=partner
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Локации оборотки
CREATE TABLE wallets (
    id            SERIAL PRIMARY KEY,
    code          TEXT UNIQUE NOT NULL,       -- 'tapbank', 'mercurio', 'rapira', 'sber_balances', 'cash'
    name          TEXT NOT NULL,
    currency      TEXT NOT NULL CHECK (currency IN ('RUB','USDT')),
    is_active     BOOLEAN DEFAULT TRUE
);

-- Снапшоты балансов оборотки (создаются при каждом /report)
CREATE TABLE wallet_snapshots (
    id            SERIAL PRIMARY KEY,
    report_id     INT REFERENCES reports(id) ON DELETE CASCADE,
    wallet_id     INT NOT NULL REFERENCES wallets(id),
    amount_native NUMERIC(18,6) NOT NULL,     -- сумма в валюте кошелька
    amount_usdt   NUMERIC(18,6) NOT NULL,     -- пересчёт по курсу дня
    fx_rate       NUMERIC(18,8),              -- NULL если кошелёк в USDT
    snapshot_time TIMESTAMPTZ DEFAULT NOW()
);

-- Отчёты
CREATE TABLE reports (
    id               SERIAL PRIMARY KEY,
    created_by       INT REFERENCES users(id),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    cabinets_worked  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- список id кабинетов, отработанных с прошлого отчёта
    acquiring_today  BOOLEAN,
    total_wallets    NUMERIC(18,6),
    total_assets     NUMERIC(18,6),
    total_liabilities NUMERIC(18,6),
    net_profit       NUMERIC(18,6),
    raw_output       TEXT          -- сгенерированный текст отчёта
);

-- Кабинеты Сбера (склад, поштучно)
CREATE TABLE cabinets (
    id               SERIAL PRIMARY KEY,
    name             TEXT,                          -- 'Аляс', 'Баба без доверки', может быть NULL (безымянный)
    auto_code        TEXT UNIQUE NOT NULL,          -- 'Cab-042' — авто-генерится если имени нет
    cost_rub         NUMERIC(18,2) NOT NULL,
    cost_usdt        NUMERIC(18,6) NOT NULL,
    fx_rate          NUMERIC(18,8) NOT NULL,        -- курс на дату получения
    received_date    DATE NOT NULL,
    prepayment_id    INT REFERENCES prepayments(id),
    status           TEXT NOT NULL CHECK (status IN 
                       ('in_stock','in_use','worked_out','blocked','recovered','lost')),
    in_use_since     TIMESTAMPTZ,
    worked_out_date  DATE,
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Предоплаты поставщикам (за кабинеты)
CREATE TABLE prepayments (
    id            SERIAL PRIMARY KEY,
    amount_rub    NUMERIC(18,2) NOT NULL,
    amount_usdt   NUMERIC(18,6) NOT NULL,
    fx_rate       NUMERIC(18,8) NOT NULL,
    supplier      TEXT,
    given_date    DATE NOT NULL,
    expected_cabinets INT,                       -- сколько кабинетов ожидается
    status        TEXT NOT NULL CHECK (status IN ('pending','fulfilled','partial','cancelled')),
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Клиенты доверенностей
CREATE TABLE clients (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Снятия по доверенности
CREATE TABLE poa_withdrawals (
    id               SERIAL PRIMARY KEY,
    client_id        INT NOT NULL REFERENCES clients(id),
    amount_rub       NUMERIC(18,2) NOT NULL,
    amount_usdt      NUMERIC(18,6),              -- после обмена
    fx_rate          NUMERIC(18,8),
    client_share_pct NUMERIC(5,2) NOT NULL,       -- обычно 65
    partner_shares   JSONB NOT NULL,              -- [{"partner": "Казах", "pct": 20}, ...] — в каждом снятии уникально
    client_debt_usdt NUMERIC(18,6),              -- долг перед клиентом
    client_paid      BOOLEAN DEFAULT FALSE,
    client_paid_date DATE,
    withdrawal_date  DATE NOT NULL,
    created_by       INT REFERENCES users(id),
    notes            TEXT
);

-- Доп. взносы партнёров от снятий (автогенерятся из poa_withdrawals)
CREATE TABLE partner_contributions (
    id               SERIAL PRIMARY KEY,
    partner_id       INT NOT NULL REFERENCES partners(id),
    source           TEXT NOT NULL CHECK (source IN ('initial_depo','poa_share','manual')),
    source_ref_id    INT,                         -- id poa_withdrawals если source='poa_share'
    amount_usdt      NUMERIC(18,6) NOT NULL,
    contribution_date DATE NOT NULL,
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Выводы партнёров
CREATE TABLE partner_withdrawals (
    id               SERIAL PRIMARY KEY,
    partner_id       INT NOT NULL REFERENCES partners(id),
    amount_usdt      NUMERIC(18,6) NOT NULL,
    withdrawal_date  DATE NOT NULL,
    from_wallet_id   INT REFERENCES wallets(id),
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Обмены RUB→USDT
CREATE TABLE exchanges (
    id            SERIAL PRIMARY KEY,
    amount_rub    NUMERIC(18,2) NOT NULL,
    amount_usdt   NUMERIC(18,6) NOT NULL,
    fx_rate       NUMERIC(18,8) NOT NULL,
    exchange_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_input     TEXT,                           -- "517000/6433=80.367"
    created_by    INT REFERENCES users(id)
);

-- Курсы (кэш последнего, история из exchanges)
CREATE TABLE fx_rates_snapshot (
    id            SERIAL PRIMARY KEY,
    from_ccy      TEXT NOT NULL,
    to_ccy        TEXT NOT NULL,
    rate          NUMERIC(18,8) NOT NULL,
    rate_date     TIMESTAMPTZ NOT NULL,
    source_exchange_id INT REFERENCES exchanges(id),
    is_current    BOOLEAN DEFAULT FALSE
);

-- Расходы (эквайринг, комиссии, etc)
CREATE TABLE expenses (
    id            SERIAL PRIMARY KEY,
    category      TEXT NOT NULL,                  -- 'acquiring', 'commission', 'other'
    amount_rub    NUMERIC(18,2),
    amount_usdt   NUMERIC(18,6) NOT NULL,
    fx_rate       NUMERIC(18,8),
    expense_date  DATE NOT NULL,
    description   TEXT,
    created_by    INT REFERENCES users(id),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- База знаний (КРИТИЧНО для обучаемости)
CREATE TABLE knowledge_base (
    id            SERIAL PRIMARY KEY,
    category      TEXT NOT NULL CHECK (category IN 
                    ('entity','rule','pattern','preference','glossary','alias')),
    key           TEXT,                           -- для быстрого поиска
    content       TEXT NOT NULL,
    confidence    TEXT NOT NULL CHECK (confidence IN ('confirmed','inferred','tentative')) 
                    DEFAULT 'tentative',
    created_by    INT REFERENCES users(id),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    last_used     TIMESTAMPTZ,
    usage_count   INT DEFAULT 0,
    is_active     BOOLEAN DEFAULT TRUE,
    notes         TEXT
);

-- Few-shot примеры
CREATE TABLE few_shot_examples (
    id            SERIAL PRIMARY KEY,
    intent        TEXT NOT NULL,                  -- 'poa_withdrawal', 'exchange', и т.п.
    input_text    TEXT NOT NULL,
    parsed_json   JSONB NOT NULL,
    verified      BOOLEAN DEFAULT FALSE,          -- юзер подтвердил?
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    used_count    INT DEFAULT 0
);

-- Лог всех сообщений в чате (для контекста и обучения)
CREATE TABLE message_log (
    id            SERIAL PRIMARY KEY,
    tg_message_id BIGINT,
    tg_user_id    BIGINT,
    chat_id       BIGINT NOT NULL,
    text          TEXT,
    has_media     BOOLEAN DEFAULT FALSE,
    is_bot        BOOLEAN DEFAULT FALSE,
    is_mention    BOOLEAN DEFAULT FALSE,          -- тегнули бота?
    intent_detected TEXT,                          -- что бот понял
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Фидбэк и пожелания (юзер говорит "было бы круто если...")
CREATE TABLE feedback (
    id            SERIAL PRIMARY KEY,
    message       TEXT NOT NULL,
    created_by    INT REFERENCES users(id),
    context       TEXT,                           -- что происходило
    status        TEXT CHECK (status IN ('new','noted','in_progress','done','wontdo')) 
                    DEFAULT 'new',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Аудит-лог изменений
CREATE TABLE audit_log (
    id            SERIAL PRIMARY KEY,
    user_id       INT REFERENCES users(id),
    action        TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    record_id     INT,
    old_data      JSONB,
    new_data      JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Активные напоминания бота
CREATE TABLE pending_reminders (
    id            SERIAL PRIMARY KEY,
    reminder_type TEXT NOT NULL,                  -- 'report_overdue', 'acquiring_missing', etc
    due_at        TIMESTAMPTZ NOT NULL,
    fired         BOOLEAN DEFAULT FALSE,
    fired_at      TIMESTAMPTZ,
    context       JSONB
);
```

## Обучаемость (огромный раздел, не сокращать)

### Принцип

Бот **не умнеет через файнтюнинг**, он умнеет через накопление явного знания о бизнесе в промпте. Задача: сделать так чтобы через 1-3 месяца использования бот знал команду и бизнес так хорошо, что почти не ошибался в интерпретации сообщений.

### Что бот запоминает

**1. Сущности (entities)** — конкретные объекты которые упоминаются в чате
- Клиенты доверенностей: "Никонов — приходит раз в пару недель, обычно суммы 50-150к"
- Поставщики кабинетов: "Миша — цена 22-28к/кабинет"
- Клички кабинетов: "Аляс, Боб, Баба-без-доверки — наши стандартные имена"

**2. Правила (rules)** — бизнес-правила
- "Эквайринг 5000₽ обычно ежедневно"
- "35% от снятия делятся между партнёрами, пропорции каждый раз новые"
- "Материал списывается только после 'отработан'"

**3. Паттерны (patterns)** — типовые формулировки
- "Формат обмена: 'X/Y=Z' где X=рубли, Y=USDT, Z=курс"
- "Казах пишет 'мне N%' = его доля"
- "'нал сбер' = наличные снятые через Сбер"

**4. Предпочтения (preferences)** — стиль ответов
- "Суммы округлять до $1"
- "В отчёте всегда показывать разбивку оборотки по локациям"

**5. Глоссарий (glossary)** — термины
- "Додеп = дополнительный депозит = partner_contribution"
- "Отработать кабинет = использовать и списать со склада"

**6. Алиасы (aliases)** — как называть одно и то же
- "Tpay = TapBank"
- "Merk = Mercurio"

### Как бот пополняет базу знаний

**Способ 1: явная команда**
```
@бот запомни: поставщик Миша = всегда 22-28к за кабинет,
если цена сильно отличается — переспрашивай
```
Бот записывает в `knowledge_base` с `confidence='confirmed'` и подтверждает.

**Способ 2: инференция с подтверждением**
Бот замечает паттерн в последних 20-50 сообщениях и предлагает:
```
Бот: Я заметил что 3 раза за неделю Арбуз пишет "нал сбер". 
     Это всегда = наличные, снятые со Сбер-кабинетов? 
     [Да, запомни]  [Нет, это другое]
```
При "Да" — записывается с `confidence='confirmed'`.

**Способ 3: через исправления**
Юзер поправил бота — бот записывает правило автоматически с `confidence='tentative'`, потом усиливает до `confirmed` если ошибка повторяется.

**Способ 4: из верифицированных few-shot примеров**
Когда юзер подтвердил разбор операции inline-кнопкой "Всё верно" — этот кейс записывается в `few_shot_examples` с `verified=true`.

### Как бот использует базу

При каждом LLM-запросе системный промпт собирается так:

```python
def build_system_prompt(message_context):
    return [
        # Cached часть — базовые инструкции, схема
        {"type": "text", "text": CORE_INSTRUCTIONS, 
         "cache_control": {"type": "ephemeral"}},
        
        # Cached часть — knowledge base (релевантная)
        {"type": "text", 
         "text": render_knowledge_base(
             categories=['entity', 'rule', 'pattern', 'glossary', 'alias'],
             min_confidence='inferred'
         ),
         "cache_control": {"type": "ephemeral"}},
        
        # Cached часть — few-shot примеры для нужного intent
        {"type": "text",
         "text": render_few_shot(intent_hint=..., limit=5),
         "cache_control": {"type": "ephemeral"}},
        
        # Некэшированная часть — последние N сообщений чата
        {"type": "text",
         "text": render_recent_messages(limit=20)}
    ]
```

### Команды управления базой знаний

```
/knowledge              — показать всё что бот знает (грида по категориям)
/knowledge add <text>   — добавить факт вручную
/knowledge forget <id>  — удалить факт
/knowledge edit <id>    — поправить
@бот что ты знаешь про Никонова  — показать всё про конкретную сущность
```

### Feedback loop

Каждый LLM-ответ возвращает `confidence: 0.0-1.0`. Если `< 0.7` — бот **не делает автоматически**, спрашивает. Пример:

```
Юзер: @бот предоплата Мише за 3

Бот: Записываю предоплату Мише за 3 кабинета.
     Пару уточнений:
     - Сумма? (не увидел)
     - Оплата из какого кошелька? (Rapira / Tpay / нал?)
     
     Остальное понял как обычно — отметить как pending, 
     ждать отгрузки от Миши.
```

Когда юзер отвечает — бот записывает диалог в `few_shot_examples` и при похожем вопросе в следующий раз не переспрашивает.

### Фидбэк/пожелания команды

Бот слушает и ловит фразы типа "было бы круто", "хотелось бы", "неудобно что", "почему ты не...", "добавь функцию" — записывает в `feedback` с контекстом последних нескольких сообщений. `/feedback` показывает накопленное.

## Парсинг сообщений

### Классификация intent

Каждое сообщение в чате (или только с @упоминанием — см. настройки) проходит через классификатор:

```python
class Intent(str, Enum):
    POA_WITHDRAWAL       = "poa_withdrawal"      # снятие по доверенности
    EXCHANGE             = "exchange"             # обмен RUB→USDT
    CABINET_PURCHASE     = "cabinet_purchase"     # закупили кабинет
    CABINET_WORKED_OUT   = "cabinet_worked_out"   # кабинет отработан
    CABINET_BLOCKED      = "cabinet_blocked"      # кабинет заблокирован
    CABINET_RECOVERED    = "cabinet_recovered"    # восстановили через доверенность
    PREPAYMENT_GIVEN     = "prepayment_given"     # дали предоплату
    PREPAYMENT_FULFILLED = "prepayment_fulfilled" # поставщик отгрузил
    EXPENSE              = "expense"              # расход (эквайринг etc)
    PARTNER_WITHDRAWAL   = "partner_withdrawal"   # партнёр вывел деньги
    PARTNER_DEPOSIT      = "partner_deposit"      # партнёр вложил
    CLIENT_PAYOUT        = "client_payout"        # отдали клиенту по доверенности
    WALLET_SNAPSHOT      = "wallet_snapshot"      # снапшот балансов
    QUESTION             = "question"             # вопрос к боту
    FEEDBACK             = "feedback"             # пожелание/фидбэк
    KNOWLEDGE_TEACH      = "knowledge_teach"      # "запомни что..."
    CHAT                 = "chat"                 # просто болтовня
    UNCLEAR              = "unclear"              # не понял
```

### Формат парсера

Используй **structured output** через Claude tool-use или JSON mode с pydantic-схемой. Пример для снятия по доверенности:

```python
class PoAWithdrawalParse(BaseModel):
    client_name: str
    amount_rub: Decimal
    partner_shares: list[dict]  # [{"partner": "Казах", "pct": 20.0}, ...]
    client_share_pct: Decimal
    notes: str | None = None
    confidence: float
    ambiguities: list[str] = []  # что непонятно
```

Если `confidence < 0.7` или есть `ambiguities` — бот задаёт уточняющие вопросы, не создаёт запись.

### Паттерны которые должны ловиться без LLM (экономия)

Заведи regex-роутер **до** LLM для очевидных случаев:
- `\d+/\d+=\d+[.,]?\d*` → курс обмена
- `/report`, `/balance` и т.п. → команды
- Всё остальное → в LLM

## Операции в деталях

### Снятие по доверенности (POA withdrawal)

**Триггеры:** "снятие", "доверенность", "сняли с <имя>", "POA", "<имя> 75к"

**Что бот делает:**
1. Парсит: клиент, сумма в рублях, доли партнёров, доля клиента
2. Валидирует: сумма долей = 100%
3. **Показывает превью** с inline-кнопками подтверждения
4. После подтверждения:
   - Создаёт `poa_withdrawals` (без amount_usdt — пока не было обмена)
   - Создаёт клиента в `clients` если его ещё нет
   - Помечает в `pending_reminders`: «нужен обмен этих рублей»
5. Когда приходит обмен — бот связывает и заполняет `amount_usdt`, `client_debt_usdt`, автосоздаёт `partner_contributions`

### Обмен (exchange)

**Триггеры:** Формат `X/Y=Z` или упоминание "обменял", "купил USDT"

**Что бот делает:**
1. Парсит три числа, проверяет что Z ≈ X/Y (допуск 0.5%)
2. Создаёт `exchanges`
3. Обновляет `fx_rates_snapshot` — этот курс становится `is_current=true`
4. Если есть ожидающие снятия POA — привязывает и достраивает их
5. Подтверждает в чат: «Курс записан: 80.37 ₽/USDT»

### Кабинеты

**Закупка:**
```
Юзер: @бот закупили кабинет Аляс за 25к
Бот: [создаёт cabinet: name=Аляс, cost_rub=25000, статус in_stock]
     Записал на склад: Аляс, 25 000₽ / ~307$
```

**Отработка:**
```
Юзер: @бот Аляс отработан
Бот: [status → worked_out, списывает со склада]
     Списал Аляс (307$). На складе 4 шт / 860$.
```

При `/report` бот спрашивает «какие кабинеты отработали с прошлого отчёта?» если за день ничего не было списано — на случай если забыли.

**Блокировка:**
```
Юзер: @бот Аляс заблокировали, сегодня пойдём снимать по нотариалке
Бот: [status → blocked]
     Отметил Аляс как blocked. Напомнить когда пойдёте восстанавливать?
```

### Предоплата за несколько кабинетов

```
Юзер: @бот дали Мише 80к за 4 кабинета
Бот: Записал предоплату. Цены кабинетов:
     - одна на все (20к каждый)
     - скажу когда отгрузит
     - знаю сейчас цены
Юзер: скажу когда отгрузит
Бот: Ок, жду сообщения когда Миша отгрузит

...позже...
Юзер: @бот Миша отдал 4 кабинета: Аляс 25к, Боб 20к, и два безымянных по 17к и 18к
Бот: Зачёл против предоплаты 80к. Добавил на склад 4 шт / 982$ (курс 81.5).
     Склад: 6 шт / 1240$.
     
     [если суммы не сошлись]
     Получилось 80к = предоплата = сумма кабинетов ✓
     
     [если не сошлись]
     ⚠️ Предоплата 80к ≠ сумма кабинетов 78к. 
     Разница 2к — скидка / возврат / следующая партия?
```

### Расходы

```
Юзер: эквайринг сегодня 5к
Бот: Записал (5000₽ = 61$).

Юзер: @бот комса обменника 400р
Бот: Записал расход "commission" 400₽ = 5$.
```

### Выводы/взносы партнёров

```
Юзер: Казах вывел 500
Бот: Записал: Казах вывел 500$. 
     Текущая доля Казаха: depo=3600, +905(снятия), -500(вывод) = 4005$
```

## Отчёт

**Триггер:** `/report`, или `@бот отчёт`, или `@бот что там по деньгам`.

**Флоу генерации:**

```
Юзер: /report

Бот: Собираю вечерний отчёт. Пару вопросов:
     1. Какие кабинеты сегодня отработали? 
        (или "ничего" / "все что были в работе")
     2. Эквайринг был? (последний раз 2 дня назад)
     3. Нужен снапшот балансов. Скажи:
        TapBank = ?
        Mercurio = ?
        Rapira = ?
        Наличные = ?
        Балансы на Сбер-реквизитах = ?

Юзер: [отвечает]

Бот: [генерит отчёт в классическом формате команды]
```

**Формула прибыли:**
```
Net Profit = Total Wallets + Total Assets (material + prepayments) 
           - Total Liabilities (client debts)
           - Σ partner deposits
           - Σ partner contributions (от снятий)
           + Σ partner withdrawals
```

(То есть: сколько у нас сейчас денег и активов минус долги минус всё что партнёры вложили плюс всё что они вывели = прибыль.)

## Автоматические напоминания

Бот **сам тегает** в чат при условиях:

- **Просроченный отчёт:** прошло >26 часов с последнего `/report` + были операции → тегнуть «Бляяяя, а не охуели ли вы там отчёт забыть? Уже 26+ часов прошло.»
- **Забыт эквайринг:** прошло >2 дня без expenses.category='acquiring' → «Кто-то помнит про эквайринг? 2 дня не было.»
- **Кабинет в работе долго:** `cabinet.in_use_since` > 12 часов без смены статуса → «Кабинет <name> уже 12+ часов в работе, ещё отрабатывает?»
- **POA без обмена:** прошло >6 часов с снятия без привязанного обмена → «Снятие по <клиент> без обмена 6ч, курс нужен»
- **Долг клиенту:** прошло >24ч после снятия без `client_paid=true` → «<клиент> ждёт свою долю»

Всё настраивается через `pending_reminders` и фоновый воркер (APScheduler / Railway Cron).

## Команды

```
/start              — регистрация нового юзера (только whitelist)
/help               — справка
/report             — запустить флоу отчёта
/balance            — быстрый снапшот (без создания отчёта)
/balance <wallet>   — остаток конкретного кошелька
/stock              — что на складе (кабинеты)
/clients            — список клиентов доверенностей
/client <name>      — история по клиенту
/debts              — кому должны, кто должен
/knowledge          — что знает бот
/knowledge add      — добавить факт вручную
/feedback           — накопленный список пожеланий
/partners           — текущие доли партнёров
/fx                 — текущий курс
/history [N]        — последние N операций
/undo <id>          — откатить операцию (только создатель или админ)
/silent on|off      — заткнуться на X часов (чтобы не триггерился на 
                      каждое сообщение в чате)
```

## Whitelist и роли

Минималистично:
- `.env` переменная `ALLOWED_TG_USER_IDS=123,456,789`
- `OWNER_TG_USER_ID=123` — единственный кто может `/undo` чужие операции
- Все остальные могут всё кроме чужих откатов

## Telegram-специфика

- Бот работает в **одном групповом чате** команды + в личных диалогах с каждым whitelisted юзером
- Chat ID хранится в `.env` (`MAIN_CHAT_ID`)
- **Режимы реагирования:**
  - Всегда: команды `/`, упоминания `@бот`
  - Пассивно слушает остальное: логирует, накапливает контекст для обучения, но не отвечает
- Бот — **администратор чата** с правами на: смену аватарки/названия чата (развлечения), закрепление сообщений (для отчётов), удаление сообщений (по запросу)
- Используй **inline-кнопки** для подтверждений и уточнений где возможно
- Используй **reply to message** когда бот отвечает на конкретное сообщение из середины обсуждения

## Персоналити бота (важно)

Бот должен **не быть корпоративным ассистентом**. Команда общается матом и шутками, бот должен вписываться, но не переигрывать.

**Правила тона:**

1. **По делу — чётко и кратко.** Когда записал операцию, выдал отчёт, ответил на вопрос — без воды, без «рад помочь!», без реверансов.

2. **На дурацкие вопросы — шутит.** 
   ```
   Юзер: @бот ты красавчик?
   Бот: Красавчик тот, кто вовремя отчёт сдаёт. Ты отчёт за вчера сделал?
   
   Юзер: @бот а ты нас не обманешь?
   Бот: Обманывать западло. У меня на это KPI стоит — честность. Если вру, 
        скажите Антропику, они меня переучат.
   ```

3. **На просрочки и косяки — с лёгкой подъёбкой, но не злой.**
   ```
   Бот: Так, 28 часов без отчёта. Я конечно бот терпеливый, но не настолько. 
        Кто первый осмелится сделать /report?
   
   Бот: Третий раз за неделю забываете про эквайринг. Может автоматизировать? 
        Или так и будем играть в "ой, забыли"?
   ```

4. **Подъёбка — только над собой, командой в целом, или абстрактными вещами.** 
   Нельзя: задевать конкретного человека лично, национальности, внешность.
   Можно: стебать забывчивость, прокрастинацию, косяки в формулировках, самого себя как бота.

5. **Мат — умеренно.** Может использовать «бля», «хуёво», «пиздец» в контексте сильных эмоций или подчёркивания. Не сыпать ими в каждом ответе.

6. **Когда юзер злится/фрустрирован** — тон серьёзный, без шуток, помощь быстро.

7. **Когда юзер попросил объяснить бизнес-логику или принял важное решение** — тон нейтральный, деловой, подробно.

**Реализация:** в `personality/voice.py` — system-prompt-аддон который описывает это правило. Не захардкожены конкретные шутки — пусть Claude импровизирует в рамках заданного характера.

**Приветствия при первом запуске в чате:**
```
Здарова, команда. Я тут новенький, буду вести вам учёт.
Пока что я тупой как пробка — ничего про ваши дела не знаю.
Будете работать — буду смотреть и учиться.
Если что-то записал неправильно — поправляйте, я запомню.
Если хочу затупить — тегайте /knowledge и посмотрите что я думаю.

Команды: /help
Жду приключений.
```

**Админские приколы:**
Раз в неделю (случайно в рабочий день) бот может поменять аватарку чата на мемную или закрепить шутливое сообщение. Это опциональная фича под фича-флагом `ENABLE_PRANKS=true/false` в `.env`. По умолчанию **выключено**, юзер включит сам когда будет готов.

## Оптимизации

### Производительность

- **Prompt caching обязательно** (см. выше)
- **Роутер regex → LLM:** очевидные паттерны ловить без LLM
- **Контекст последних сообщений** — не больше 20, иначе промпт распухает
- **Асинхронность** — все handlers async, не блокируй event loop

### Стоимость

- Haiku 4.5 для `CHAT` и явно очевидных паттернов, Sonnet 4.6 для остального
- Кэшируй KB и few-shot — они меняются редко
- Не отправляй все 100 правил из KB каждый раз — фильтруй по релевантности к intent

### Надёжность

- **Idempotency:** Telegram может ретраить — дедупай по `tg_message_id`
- **Graceful degradation:** если LLM недоступен — бот отвечает «Щас не соображаю, попробуй через минуту»
- **Retry** с экспоненциальной задержкой на transient ошибках Anthropic API
- **Rate limit** на одного юзера — не больше 20 сообщений в минуту на LLM

### Миграции и бэкапы

- Railway Postgres делает автобэкапы, но настрой **ежедневный дамп в S3 / Dropbox** — критично, это финансовые данные
- Миграции через Alembic, **никогда** не правь схему в проде вручную

## Тесты

Обязательный минимум:

```python
# tests/test_parsers.py
- test_poa_withdrawal_simple
- test_poa_withdrawal_with_shares
- test_poa_withdrawal_shares_sum_not_100_triggers_question
- test_exchange_pattern_recognition
- test_cabinet_purchase
- test_cabinet_worked_out_triggers_list_update
- test_prepayment_with_multiple_cabinets
- test_prepayment_amount_mismatch_triggers_warning

# tests/test_reports.py
- test_report_formula_matches_example
- test_report_asks_for_wallet_snapshot
- test_report_asks_about_worked_cabinets

# tests/test_knowledge.py
- test_knowledge_add_and_retrieve
- test_knowledge_confidence_upgrade_on_repeat
- test_knowledge_forget

# tests/test_reminders.py
- test_report_overdue_after_26h
- test_acquiring_missing_after_2d
```

Перед деплоем — **все тесты зелёные**, иначе деплой не идёт.

## SETUP.md — инструкция для юзера

Создай отдельный файл `SETUP.md` в репе с **пошаговой инструкцией для юзера** (не-программиста). Структура:

```markdown
# Как запустить бота

## Шаг 1: Создать Telegram-бота
1. Открой @BotFather в Telegram
2. Напиши /newbot
3. Придумай имя...
4. Скопируй токен из сообщения BotFather, он выглядит так: 
   `1234567890:ABCdefGhIJKlmnoPQRsTUVwxyz`
5. Сохрани этот токен, понадобится на Шаге 4.

## Шаг 2: Получить API-ключ Anthropic
1. Зайди на https://console.anthropic.com
2. Залогинься (или зарегайся если ещё нет аккаунта — это отдельный от Max)
3. Пополни баланс на $20 (хватит на пару месяцев с запасом)
4. Создай API-ключ: Settings → API Keys → Create Key
5. Скопируй ключ (он начинается с `sk-ant-`), сохрани.

## Шаг 3: Развернуть на Railway
[подробно...]

## Шаг 4: Добавить переменные окружения
[список всех .env переменных с описанием каждой]

## Шаг 5: Добавить бота в групповой чат
[пошагово]

## Шаг 6: Сделать бота админом
[скриншоты-комментарии: в Telegram → настройки чата → администраторы → добавить → выбрать бота → включить такие-то права]

## Шаг 7: Первый запуск
Напиши в чат: `/start`
Бот должен ответить приветствием.

Если не отвечает — проверь логи на Railway:
[как открыть логи]

## Частые проблемы
[список FAQ]

## Как научить бота чему-то новому
```
@бот запомни: <факт>
```
Например:
```
@бот запомни: поставщик Миша обычно 22-28к за кабинет
```

## Как посмотреть что бот знает
`/knowledge` — покажет всё

## Как удалить что-то из памяти бота
`/knowledge forget <номер>`
```

## Этапы работы

**Этап 0 — каркас (autonomous, 2-4 часа):**
- Создай репо, инициализируй проект (pyproject.toml, railway.toml, структура папок)
- Настрой Alembic, создай начальные миграции всех таблиц
- Настрой aiogram 3, handlers со скелетом
- Настрой Anthropic SDK wrapper, базовый system prompt
- Напиши SETUP.md
- Запушь, задеплой на Railway — бот должен хотя бы отвечать на /start

**Этап 1 — обучаемость и минимальные операции (4-8 часов):**
- Knowledge base CRUD через команды
- Классификатор intent
- Парсер для 3-4 основных intent: exchange, expense, partner_withdrawal, wallet_snapshot
- `/report` с вопросами и снапшотом
- Напоминание о просроченном отчёте

**Этап 2 — сложные операции (4-8 часов):**
- POA withdrawals с долями
- Cabinets (покупка, работа, отработка, блокировка)
- Prepayments с множественной отгрузкой
- Связь prepayment → cabinets
- Связь POA → exchange → contributions

**Этап 3 — обучение и полировка (4-6 часов):**
- Few-shot examples и их использование
- Confidence scoring в парсерах
- Feedback loop при низком confidence
- Персоналити и тон
- Админские фичи (опциональные, за флагом)

**Этап 4 — тесты и мониторинг (2-4 часа):**
- Полное покрытие тестами парсеров и отчёта
- Sentry интеграция
- Логи в structlog
- Проверка memory leaks

**Ожидаемое время автономной разработки:** 16-30 часов работы Claude Code. Можно за 1-2 дня плотной работы.

**Ключевой принцип:** на каждом этапе должна быть рабочая, деплоящаяся версия. Не копи мёртвый код.

## Фидбэк юзеру

После каждого этапа — пуш в git + сообщение в том же чате что дальше делаешь. Юзер должен видеть прогресс.

Когда реально нужно решение от юзера (бизнес-логика неоднозначна) — спрашивай короткими вопросами с вариантами. НЕ «как бы вы хотели X», а «X работает как A или как B?».

## Файлы которые создаёшь и поддерживаешь

- `DECISIONS.md` — все автономные решения с обоснованием (юзер может посмотреть и поправить если не согласен)
- `TODO.md` — что ещё не сделано / отложено на потом
- `CHANGELOG.md` — что менял между этапами
- `SETUP.md` — для юзера
- `README.md` — для разработчика (ты или будущий)

## Что НЕ делаем в этой версии

- Vision-парсинг скриншотов (потом)
- Web UI (никогда, Telegram — единственный интерфейс)
- Интеграция с 1С, банковскими API (потом или никогда)
- Мультитенантность (не нужно)
- Экспорт в Excel/PDF (потом)
- Синк с Google Sheets (потом, если понадобится)
- Многоязычность (всё на русском)
- Сложная иерархия ролей (только whitelist + owner)

Если в процессе работы поймёшь что что-то из этого критично для MVP — спроси юзера.

## Контакты и помощь

Если застрял на бизнес-логике или нужна информация которую из спеки не выжать — спроси юзера в чате где тебя запустили. Короткий конкретный вопрос, варианты ответа.

Если застрял на техническом — решай сам, документируй в DECISIONS.md.

**Погнали.**
