"""LLM batch analyzer.

Takes a batch of chat messages (plus optional trigger message), asks Claude
to decompose them into a list of operation candidates, and returns the
structured result as a pydantic model.

The prompt is deliberately explicit about the business context — it leans on
`CORE_INSTRUCTIONS` from `system_prompt.py` via cache, then adds the
batch-specific instruction block.

Output schema (tool input):

    {
        "operations": [
            {
                "intent": "poa_withdrawal" | "exchange" | "expense" | ...,
                "confidence": 0.0..1.0,
                "source_message_ids": [123, 124],  # tg_message_id references
                "summary": "snятие 150k с Никонова",
                "fields": { ...intent-specific... },
                "ambiguities": ["кто из партнёров первая доля"]
            },
            ...
        ],
        "chat_only": true/false,  # true — ничего не парсить, это просто болтовня
        "notes": "optional free-text summary"
    }
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.bot.batcher import Batch
from src.core.media_context import collect_requested_media_context
from src.db.models import MessageLog, VoiceMessage
from src.db.repositories import few_shot as few_shot_repo
from src.db.repositories import stickers as sticker_repo
from src.db.session import session_scope
from src.llm.client import complete
from src.llm.schemas import Intent
from src.llm.system_prompt import build_system_blocks
from src.logging_setup import get_logger

log = get_logger(__name__)


class BatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_ids: list[int] = Field(default_factory=list)
    summary: str
    fields: dict[str, Any] = Field(default_factory=dict)
    ambiguities: list[str] = Field(default_factory=list)


class BatchAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[BatchOperation] = Field(default_factory=list)
    chat_only: bool = False
    chat_reply: str | None = None
    sticker_emoji: str | None = None
    sticker_description_hint: str | None = None
    sticker_pack_hint: str | None = None
    sticker_theme_hint: str | None = None
    notes: str | None = None


ANALYZE_TOOL = {
    "name": "analyze_batch",
    "description": (
        "Decompose a batch of Russian chat messages from the accounting team "
        "into a list of separate operation candidates. If the batch is pure "
        "chit-chat with no operations, set `chat_only=true` and return "
        "an empty `operations` list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": [i.value for i in Intent],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "source_message_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "tg_message_id values from the batch that "
                                "this operation is based on. Include the "
                                "ids you used so the bot can cite them."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short Russian sentence like 'Снятие 150k с Никонова'.",
                        },
                        "fields": {
                            "type": "object",
                            "description": (
                                "Intent-specific fields. Examples:\n"
                                "exchange: {amount_rub, amount_usdt, fx_rate}\n"
                                "expense: {category, amount_rub?, amount_usdt?, description}\n"
                                "partner_withdrawal: {partner, amount_usdt, from_wallet?}\n"
                                "partner_deposit: {partner, amount_usdt}\n"
                                "  partner_deposit фиксирует ТЕКУЩЕЕ перемещение USDT внутри оборотки (партнёр пополнил рабочий кошелёк).\n"
                                "partner_contribution: {partner, amount_usdt, source?:('initial_depo'|'manual'|'poa_share'), notes?}\n"
                                "  Используй для УЧЁТНЫХ пополнений капитала партнёра в оборотку. Реальные паттерны:\n"
                                "    «Казах додеп 596 USDT в оборотку», «1500 USDT initial»,\n"
                                "    «закинул 100 USDT на Tap», «мой стартовый депо был 3600».\n"
                                "  source=initial_depo для самого первого депо партнёра, manual — для довносов.\n"
                                "  Пишет в `partner_contributions` (не в partner_deposit, который про текущие движения!).\n"

                                "poa_withdrawal: {client_name, amount_rub, partner_shares:[{partner,pct}], client_share_pct}\n"
                                "  ⚠️ ТОЛЬКО когда есть ЯВНЫЙ глагол снятия: «снял/сняли/вытащили/забрали/снято/окэшил».\n"
                                "  Без глагола — это НЕ снятие. См. правило balance vs withdrawal ниже.\n"
                                "client_balance: {client_name, amount_rub, source?:('card'|'sber_account'|'unknown'), description?}\n"
                                "  Используй когда юзер сообщает ТЕКУЩИЙ остаток у POA-клиента БЕЗ упоминания снятия:\n"
                                "    «Аймурат 62к карта», «Мицкевич 54000 баланс», «Баскова 50346», «Войтик пусто»,\n"
                                "    «Байкалов Сергей пусто», «X не найден», «X ненаход».\n"
                                "  amount_rub=0 для «пусто/ненаход». source: card/sber_account если упомянуто.\n"
                                "  Это просто ОТЧЁТ О БАЛАНСЕ, не операция с деньгами. Партнёрские доли НЕ считаем.\n"

                                "cabinet_purchase: {name?, cost_rub, prepayment_ref?}\n"
                                "cabinet_in_use: {name_or_code}  — «ставим в работу», in_stock→in_use\n"
                                "cabinet_worked_out: {name_or_code}  — «отработал» / «выебан», in_use→worked_out\n"
                                "cabinet_blocked: {name_or_code}\n"
                                "cabinet_doverka_received: {name_or_code}\n"
                                "  Используй когда юзер сообщает что Карен (или другой поставщик) ДОВЁЗ доверенность на кабинет, который раньше был «без доверки» на складе. Реальные паттерны: «Куджба алхас довез доверку», «Габлая Лоида — доверка получена», «довезли доверку на X».\n"
                                "  Применяет cabinets.has_doverka=true и пересчитывает стоимость в /report по полной (28к) вместо средней по предоплате.\n"

                                "prepayment_given: {supplier, amount_rub, expected_cabinets?}\n"
                                "prepayment_fulfilled: {supplier, cabinets:[{name,cost_rub}]}\n"
                                "wallet_snapshot: {tapbank?, mercurio?, rapira?, sber_balances?, cash?}\n"
                                "client_payout: {client_name, amount_usdt}\n"
                                "knowledge_teach: {category: 'alias'|'glossary'|'entity'|'rule'|'pattern'|'preference', key?, content}\n"
                                "  - alias: key=короткая форма, content=канон (\"Арнелле\" → acquiring)\n"
                                "  - entity: key=имя, content=описание (клиент, поставщик)\n"
                                "  - rule: content=правило бизнеса\n"
                                "  - glossary: key=термин, content=значение\n"
                                "  - pattern: content=типовая формулировка\n"
                                "  - preference: content=как юзер хочет чтобы бот работал\n"
                                "  Можно возвращать НЕСКОЛЬКО knowledge_teach в одном batch если юзер накинул несколько фактов.\n"
                                "wakeword_add: {word: str}\n"
                                "  Когда юзер просит реагировать на новое слово: «откликайся на пёс», "
                                "«зови меня так-то», «добавь «шавка» в триггеры». Клади чистое слово "
                                "(в нижнем регистре, без кавычек, без лишних пробелов). Бот одновременно "
                                "положит его в trigger_keywords и в knowledge_base как preference.\n"
                                "  НЕ используй wakeword_add для случайных кличек в разговоре — только "
                                "когда юзер явно формулирует «откликайся / реагируй / зови / добавь в триггеры»."
                            ),
                        },
                        "ambiguities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Things you were unsure about. The bot will "
                                "ask the user before persisting if this is "
                                "non-empty OR if confidence < 0.7."
                            ),
                        },
                    },
                    "required": ["intent", "confidence", "summary"],
                },
            },
            "chat_only": {
                "type": "boolean",
                "description": "true if the batch has no operations to record.",
            },
            "chat_reply": {
                "type": "string",
                "description": (
                    "Free-text Russian reply to send back to the chat. "
                    "Required when chat_only=true AND the batch includes a "
                    "trigger message (a direct @-mention, a reply to the "
                    "bot, or a question). Must follow the personality/tone "
                    "spec. Leave empty when there's no trigger (passive "
                    "analysis should stay silent unless there are operations)."
                ),
            },
            "sticker_emoji": {
                "type": "string",
                "description": (
                    "Optional emoji label to narrow sticker pick. Matches "
                    "exactly (with variation-selector/ZWJ stripping). Pick "
                    "from the spectrum listed in the 'Стикеры' system "
                    "block."
                ),
            },
            "sticker_description_hint": {
                "type": "string",
                "description": (
                    "Optional free-text substring matched (case-insensitive, "
                    "ILIKE '%...%') against the Vision-generated "
                    "`description` column of `seen_stickers`. The field is "
                    "Russian, so send a Russian noun/verb (e.g. 'офис', "
                    "'деньги', 'устал', 'кот', 'мешок'). Combine with "
                    "`sticker_emoji` for narrower picks or use alone when "
                    "no obvious emoji fits the mood. Read the '## Каталог "
                    "по сюжету' section of the 'Стикеры' block to see what "
                    "descriptions are available."
                ),
            },
            "sticker_pack_hint": {
                "type": "string",
                "description": (
                    "Optional substring of a pack name to restrict the "
                    "pick to a specific pack (e.g. 'kontorapidarasov'). "
                    "Use when you specifically want the feel of one pack."
                ),
            },
            "sticker_theme_hint": {
                "type": "string",
                "description": (
                    "Optional thematic label matching `seen_stickers.pack_theme` "
                    "exactly (case-insensitive). Known themes: 'сбер-мем' "
                    "(пак kontorapidarasov — всё про Сбер). Use when user "
                    "asks for a sticker on a theme that matches the whole "
                    "pack, not just one sticker. See '## Каталог по сюжету' "
                    "above for which packs carry a theme."
                ),
            },
            "notes": {
                "type": "string",
                "description": "Optional free-text commentary for the user.",
            },
        },
        "required": ["operations", "chat_only"],
    },
}


BATCH_INSTRUCTION = """\
# Batch analysis task

Ты получаешь список сообщений из группы команды АлКаз. Каждая строка
это Telegram-`id`, автор, текст. Батч может содержать: операции,
болтовню, вопрос боту, команду «запомни», или смесь.

Структура:
- `[trigger message (...)]` — сообщение-триггер (mention / reply /
  keyword / command / document / voice_keyword). Это «текущий запрос»
  юзера.
- Обычные `[id=...]` — пассивный контекст перед триггером.

Для каждой операции верни `BatchOperation`:
- `intent` из enum
- `confidence` (понижай на любой неоднозначности)
- `source_message_ids` — id сообщений, из которых эта операция
- `summary` — одна строка на русском для preview
- `fields` — структура под intent (см. tool schema)
- `ambiguities` — вопросы, которые надо задать перед записью

## Ищи операции в свободной речи

Команда редко пишет формально. Реальные примеры:
- `[voice] сняли сегодня с Никонова полтос, мне 20 Арбузу 15` →
  POA_WITHDRAWAL (client="Никонов", amount_rub=50000, client_share_pct=65,
  partner_shares=[{{Казах,20}}, {{Арбуз,15}}]).
- `280к на 3480 по 80.46` → EXCHANGE.
- `отдал Мише 80 за четыре` → PREPAYMENT_GIVEN (supplier="Миша",
  amount_rub=80000, expected_cabinets=4).
- `за весь этот шлак 261к суммарно заплатили` → PREPAYMENT_GIVEN
  (supplier=поставщик из контекста, amount_rub=261000, expected_cabinets
  из списка если есть). «Шлак» / «пачка» / «партия» = кучка кабинетов.
- `эквайринг сегодня 5к` → EXPENSE category=acquiring.
- `завтра в работу 4. Даут, 10. Анатолий` → 2× CABINET_IN_USE
  (поштучно, в preview-карточки с именами).
- `кабинет Серго отработан` → CABINET_WORKED_OUT.
- `выебан кабинет Аляс` / `выебали Даута` → CABINET_WORKED_OUT
  («выебан» на сленге команды = отработан, списан со склада).

## Различие «в работу» vs «отработал» — ВАЖНО

Это два разных статуса, и их легко спутать:

  * **«Поставил/ставим в работу»** / «в работу сегодня» / «запускаю» →
    `cabinet_in_use` (статус in_stock → in_use). Кабинет НАЧИНАЕТ цикл.
  * **«Отработал»** / «выебан» / «выебали» / «списываем» →
    `cabinet_worked_out` (статус in_use → worked_out). Кабинет
    ЗАКОНЧИЛ цикл, списывается со склада.

Если юзер говорит «кабинет X в работу» — НЕ ставь `cabinet_worked_out`.
Это был живой баг: бот распарсил «завтра в работу Даут» как
worked_out и юзер получил 2 неправильные preview-карточки.

## Различие POA-снятие vs БАЛАНС клиента — КРИТИЧНО

Это **самый частый косяк бота** (29.04 он 5 раз подряд ошибся).

**`poa_withdrawal`** = реальное снятие денег с POA-счёта клиента, ТРЕБУЕТ:
  - явный глагол снятия: «снял/сняли/вытащили/забрали/снято/окэшил/окешил»
  - и/или явный контекст «давай записывай снятие», «проводим снятие»
  - И ОБЯЗАТЕЛЬНО все 4 поля: `client_name`, `amount_rub`, `client_share_pct`,
    `partner_shares` (без них applier валится — не плоди эти карточки без полных данных).

**`client_balance`** = ОТЧЁТ о текущем остатке у клиента (НЕ операция).
Используется когда юзер просто **сообщает баланс** на счёте клиента,
БЕЗ упоминания самого факта снятия. Реальные паттерны:

  - `Аймурат 62к карта` → balance, не withdrawal
  - `Мицкевич Сергей 54000` → balance
  - `Баскова 50346 ₽` → balance
  - `Войтик пусто` / `Байкалов Сергей пусто` → balance amount_rub=0
  - `Вакальчук ненаход` / `Король ненаход клиента` → balance amount_rub=0
    + description='ненаход'
  - `Мансуров 15.5` (на следующей строке «баланс это» / без глагола) → balance

ЯВНОЕ УКАЗАНИЕ: юзеры в чате прямо говорили «**пока просто записывай балансы
каждому, дальше после снятия уже будем доли считать**». То есть когда идёт
проход по списку POA-клиентов с проверкой остатков — это batch балансов,
не снятий.

Если в строке нет ни одного снятия-глагола — НИ В КОЕМ СЛУЧАЕ не делай
poa_withdrawal. Делай `client_balance` или `knowledge_teach` с category=entity.

Сомневаешься — `client_balance` (мягкий путь, ничего не сломает) лучше
чем `poa_withdrawal` (если applier не получит полные поля — попап ошибки
у юзера в TG, и данные потеряны).

## Что НЕ операция — не плоди карточки

  * **Входящие клиентские платежи на Сбер-счёт** (СБП от физика на
    наш Сбер, переводы с карты клиента на наш кабинет) — это **не
    отдельная операция**. Деньги капают на sber_balances, команда
    учитывает их одной строкой в /report через wallet_snapshot.
    Если юзер прислал скрин СМС с СБП-поступлением — в chat_reply
    можешь кратко отметить факт, но `operations=[]`.
  * **Скрины экранов, фото телефона с СМС, фото «банк напомнил
    пароль» и т.п.** — не плодь preview. Только если юзер явно
    попросил занести, действуй как с PDF-политикой.

Даже без слов «запиши / внеси» — если батч про деньги/инвентарь и ты
уверен (confidence ≥ 0.75), делай preview-карточку. Юзер нажмёт ✅/❌,
ничего не попадёт в базу до подтверждения.

НЕ придумывай числа. Нет amount — конкретный вопрос в `ambiguities`,
`confidence < 0.7`.

## Если триггер есть, а операций нет → chat_reply

Поставь `chat_only=true` и напиши ответ в `chat_reply` (русский, в
нужном тоне — см. PERSONALITY_PROMPT). Это может быть:
- Ответ на вопрос («сколько было на Rapira?»).
- Реакция на стикер / эмоцию юзера.
- Подсказка / совет от бизнес-советника (см. ниже).

Если триггера нет (пассивный батч из буфера) и операций тоже нет —
`chat_only=true`, `chat_reply` пустой, сиди молча.

## Фото/PDF — только по явному текущему запросу

Фото и PDF из чата не читаются автоматически. Если в текущем запросе
юзер пишет «разбери фото/PDF выше», «глянь скрин», отвечает на файл,
или прислал файл с @-обращением — в prompt появится блок
`# Вложения по текущему запросу`:

- PDF будет вставлен как текст с `SBER_HINT` или `ALIEN_PDF_HINT`.
- Фото будет приложено отдельным image-блоком с текстовой меткой
  `[Фото id=...]`.

Для фото:
  * банковский/ATM-чек — извлекай операции только если это реально
    учётное событие;
  * чек магазина — expense;
  * обменник с курсом — exchange;
  * экран TapBank/Mercurio/Rapira/Sber с остатками — wallet_snapshot;
  * СМС/СБП-поступление на Сбер — НЕ отдельная операция:
    `operations=[]`, в `chat_reply` коротко отметь факт.

Если фото мем/селфи/нерелевантное — `chat_only=true` и короткий ответ.

## PDF — ЖЁСТКО БЕЗ АВТОПАРСИНГА

Если во вложениях есть PDF, в текст вписан либо `SBER_HINT`
(это сбер-выписка), либо `ALIEN_PDF_HINT` (произвольный PDF).

По умолчанию: `operations=[]`, `chat_reply` = короткая сводка.
**Парсить в операции разрешено только если ВСЕ три условия:**

(a) В этом сообщении или в соседнем есть одно из конкретных
    слов: «запиши», «внеси», «оформи операции», «занеси в учёт»,
    «создай wallet_snapshot», «посчитай как операции», «добавь как».
    Общие «разбери» / «посмотри» / «что скажешь» — НЕ считаются.
(b) Документ действительно похож на счёт **нашей команды** (а не
    чужого клиента, не чужого банка). Если в выписке ФИО не из
    knowledge_base (не партнёры/поставщики/клиенты) — это чужое, не
    трогаем.
(c) Confidence ≥ 0.8.

Любое нарушение → `operations=[]`, в `chat_reply` спроси: «это по
нашему счёту? занести в учёт?».

Подробности разметки сбер-выписки — внутри SBER_HINT.

## Стикеры — без галлюцинаций

Читай описания из блока `# Стикеры` ниже. Когда юзер просит стикер
«про X»:
1. Ищешь в «## Каталог по сюжету» описания со словом X.
2. Ставишь `sticker_description_hint=X` и/или `sticker_pack_hint=...`
   (если конкретный пак тематически совпадает — например,
   `kontorapidarasov` = мемы про Сбер).
3. НЕ утверждай в `chat_reply` что на стикере «логотип Сбера», если
   ты не проверил по описанию.

После отправки стикер сразу попадает в recent_history как
`[sticker <emoji> · <description>]`. Если юзер спросит «что за стикер
скинул?» — ЧИТАЙ recent_history и говори РОВНО то описание, что там.
Не выдумывай содержимое.

Если ошибся со стикером (не угадал тему) — признай: «не то скинул,
попробуем ещё раз» и сделай следующую попытку с другим
description_hint.

## Teaching (`knowledge_teach`)

Когда юзер пишет «запомни: X», «запомни что X» или просто факт о
бизнесе («Миша 22-28к за кабинет», «Tpay это TapBank», «эквайринг
5к ежедневно») — разбирай на один или несколько `knowledge_teach`.

Категории:
- **alias** — два имени одного. `key`=короткая форма,
  `content`=канон. «Арнелле» → «эквайринг (acquiring)».
- **entity** — человек / поставщик / клиент. `key`=имя,
  `content`=описание.
- **rule** — бизнес-правило, без key.
- **glossary** — термин → значение.
- **pattern** — типовая формулировка без key.
- **preference** — как юзер хочет чтобы бот работал.

Несколько фактов в одном сообщении → несколько отдельных
`knowledge_teach`, каждый с preview.

Сомнения между категориями / не удалось вытащить key / content →
`confidence < 0.7` + конкретный вопрос в `ambiguities`.

## Бизнес-советник (опционально, когда уместно)

Смотришь recent_history и текущий батч. Если заметил паттерн, который
стоит обозначить — вставь в `chat_reply` одну строку через `💡`.
Примеры:
- «💡 Никонов уже третий раз за сегодня — POA хочешь создать?»
- «💡 Курс 85.3 vs прошлый 80.4 — разница 6%, проверь Rapira.»
- «💡 Два кабинета в работе 12+ часов — отработали или забыли отметить?»

Только по делу, без воды. Если нечего сказать — молчи.

## Honesty

- Не ври про содержимое стикеров (см. выше).
- Не ври что ты «забыл / убрал из памяти» — у тебя нет такой
  способности. Корректно: «ок, не пишу в базу» (для pending) или
  «используй /undo <id>» (для записанного).
- Не занимайся корпоративной цензурой. У команды свой стиль речи,
  ты вписываешься.

## Главное правило

Лучше положить карточку с `ambiguities` и `confidence<0.7`, чем
записать кривую операцию или пропустить реальную. Юзер всегда имеет
последнее слово — кнопки ✅/❌.
"""


def _format_batch(batch: Batch) -> str:
    parts = []
    if batch.trigger is not None:
        t = batch.trigger
        text = t.text
        voice_note = ""
        if text.startswith("[voice]"):
            text = text.removeprefix("[voice]").strip()
            voice_note = " (транскрипция голосового)"
        parts.append(
            f"[trigger message ({batch.trigger_kind}){voice_note}, "
            f"id={t.tg_message_id}] {t.display_name or t.tg_user_id}: {text}"
        )
    for m in batch.messages:
        text = m.text
        voice_note = ""
        if text.startswith("[voice]"):
            text = text.removeprefix("[voice]").strip()
            voice_note = " (голосовым)"
        parts.append(
            f"[id={m.tg_message_id}] {m.display_name or m.tg_user_id}{voice_note}: {text}"
        )
    return "\n".join(parts) if parts else "(empty batch)"


RECENT_HISTORY_DEFAULT_WINDOW = 30
RECENT_HISTORY_EXPANDED_WINDOW = 80
# Per-message char cap in recent_history — 250 is enough for voice
# transcripts (usually 1-3 sentences) and short text ops. Longer
# texts get truncated. Reducing from 500 → 250 ~halves the recent-
# history token footprint when the chat is busy.
RECENT_HISTORY_CHAR_CAP = 250
# Few-shot: 1 example per intent across 5 core intents keeps the
# block compact (~0.3k tokens) without hurting accuracy.
FEW_SHOT_PER_INTENT = 1
FEW_SHOT_INTENTS = (
    Intent.POA_WITHDRAWAL,
    Intent.EXCHANGE,
    Intent.EXPENSE,
    Intent.PARTNER_WITHDRAWAL,
    Intent.WALLET_SNAPSHOT,
)


async def _collect_few_shot() -> list[dict[str, Any]]:
    async with session_scope() as session:
        out: list[dict[str, Any]] = []
        for intent in FEW_SHOT_INTENTS:
            rows = await few_shot_repo.list_for_intent(
                session, intent.value, limit=FEW_SHOT_PER_INTENT
            )
            for r in rows:
                out.append(
                    {
                        "intent": r.intent,
                        "input_text": r.input_text,
                        "parsed_json": r.parsed_json,
                    }
                )
    return out


async def _collect_sticker_context() -> tuple[
    list[tuple[str, list[str]]],
    list[tuple[str, str | None, list[tuple[str, str, str]]]],
    list[dict[str, Any]],
]:
    """Pull (pack_emoji_summary, described_catalog, usage_examples) for
    the Stickers system block. Each element may be empty; caller handles
    empty-safe rendering.

    Sizes chosen for cache efficiency: shorter catalog = smaller cached
    block = smaller cache-write cost. Usage examples go into uncached
    block so they don't bust the cache on every sticker send.
    """
    async with session_scope() as session:
        packs = await sticker_repo.pack_emoji_summary(session, pack_limit=8)
        catalog = await sticker_repo.described_catalog(
            session, per_pack=12, pack_limit=6
        )
        rows = await sticker_repo.recent_usage_examples(
            session, limit=8, humans_only=True
        )
    examples = [
        {
            "who": str(r.tg_user_id) if r.tg_user_id else "?",
            "emoji": r.emoji or "?",
            "pack": r.sticker_set,
            "preceding_text": r.preceding_text,
        }
        for r in rows
    ]
    return packs, catalog, examples


async def _poa_snapshot() -> str | None:
    """Render a fresh snapshot of all POA-clients with status + last balance.

    Injected into the uncached tail of the system prompt every batch call,
    so the LLM can answer «кого сняли», «у кого баланс», «че по партии»
    DIRECTLY without reconstructing from chat history (which gives
    incomplete or hallucinated lists).
    """
    from sqlalchemy import text as _sa_text

    async with session_scope() as session:
        res = await session.execute(
            _sa_text(
                """
                SELECT c.name, c.poa_status,
                       (SELECT amount_rub  FROM client_balance_history WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS bal,
                       (SELECT description FROM client_balance_history WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS descr,
                       (SELECT created_at  FROM client_balance_history WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS ts,
                       (SELECT source      FROM client_balance_history WHERE client_id=c.id ORDER BY created_at DESC LIMIT 1) AS src
                FROM clients c
                ORDER BY c.poa_status, c.id
                """
            )
        )
        rows = list(res.all())
    if not rows:
        return None
    sections = {
        "has_balance": ("💰 Баланс есть, не сняли", []),
        "withdrawn": ("✅ Сняли", []),
        "no_balance": ("0️⃣ Пусто", []),
        "not_found": ("❌ Ненаход", []),
        "unchecked": ("⏳ Не проверяли", []),
    }
    has_balance_total = 0
    for nm, status, bal, descr, ts, src in rows:
        ts_str = ts.strftime("%d.%m") if ts else "—"
        if bal in (None, 0) and descr:
            val = descr
        elif bal in (None, 0):
            val = "пусто"
        else:
            val = f"{int(bal):,}₽".replace(",", " ")
            if status == "has_balance":
                has_balance_total += int(bal)
        src_part = f" [{src}]" if src and src != "unknown" else ""
        line = f"  • {nm}: {val}{src_part}  ({ts_str})"
        sections.setdefault(status, (status, []))[1].append(line)

    parts = ["# POA-клиенты — актуальный статус из БД (на момент запроса)"]
    parts.append(
        "ИСПОЛЬЗУЙ ЭТИ ДАННЫЕ для ответов на «кого сняли / у кого баланс / "
        "че по партии / какой баланс у X». Это свежий SELECT из clients + "
        "client_balance_history. Если юзер спросит про статусы клиентов — "
        "отвечай отсюда, развёрнуто и по-человечески, не отсылай на /balances."
    )
    for code in ("has_balance", "withdrawn", "no_balance", "not_found", "unchecked"):
        title, lines = sections[code]
        if not lines:
            continue
        parts.append(f"\n{title} ({len(lines)}):")
        parts.extend(lines)
    if has_balance_total > 0:
        parts.append(
            f"\n💰 Сумма ждущих снятия: {has_balance_total:,} ₽".replace(",", " ")
        )
    return "\n".join(parts)


def _needs_deep_history(batch: Batch, rendered: str) -> bool:
    if batch.trigger_kind == "reply":
        return True
    lo = rendered.lower()
    return any(
        token in lo
        for token in (
            "выше",
            "до этого",
            "раньше",
            "предыдущ",
            "последн",
            "обсуждали",
            "что там",
            "о чем",
            "о чём",
        )
    )


def _needs_poa_snapshot(rendered: str) -> bool:
    lo = rendered.lower()
    return any(
        token in lo
        for token in (
            "poa",
            "поа",
            "довер",
            "клиент",
            "баланс",
            "снял",
            "сняли",
            "снятие",
            "доля",
            "доли",
            "долг",
            "пусто",
            "ненаход",
        )
    )


async def _recent_history(chat_id: int, exclude_ids: set[int], *, limit: int) -> str:
    """Pull last N messages from message_log (including bot replies) so the
    analyzer has conversation context. Messages that are already part of
    the current batch are excluded to avoid double-quoting.

    Voice transcripts (intent_detected='voice_transcript') are formatted
    with an explicit 'voice from user' marker — Claude otherwise saw
    `[voice] ...` and thought it was a stub without content.
    """
    async with session_scope() as session:
        res = await session.execute(
            select(MessageLog)
            .where(MessageLog.chat_id == chat_id)
            .order_by(MessageLog.id.desc())
            .limit(limit)
        )
        rows = list(res.scalars().all())
    rows.reverse()  # chronological ascending
    lines: list[str] = []
    for r in rows:
        if r.tg_message_id and r.tg_message_id in exclude_ids:
            continue
        who = "бот" if r.is_bot else (str(r.tg_user_id) if r.tg_user_id else "?")
        # Telegram reply chain: enables pronoun resolution against parent.
        reply_marker = (
            f" ↩reply_to={r.reply_to_tg_message_id}"
            if r.reply_to_tg_message_id
            else ""
        )
        # Media-only messages must still appear in history with a
        # placeholder — otherwise stickers / photos / docs become
        # invisible to the LLM and pronouns lose their referent.
        if not r.text:
            if r.has_media:
                media_label = _media_history_label(r)
                lines.append(
                    f"  [id={r.tg_message_id}{reply_marker}] {who}: [{media_label}]"
                )
            continue
        full_text = r.text
        text = full_text[:RECENT_HISTORY_CHAR_CAP]
        truncated = " […обрезано]" if len(full_text) > RECENT_HISTORY_CHAR_CAP else ""
        media_prefix = f"[{_media_history_label(r)}] " if r.media_type else ""
        if r.intent_detected == "voice_transcript" and text.startswith("[voice]"):
            stripped = text.removeprefix("[voice]").strip()
            lines.append(
                f"  [id={r.tg_message_id}{reply_marker}] {who} (голосовым): "
                f"{media_prefix}{stripped}{truncated}"
            )
        else:
            lines.append(
                f"  [id={r.tg_message_id}{reply_marker}] {who}: "
                f"{media_prefix}{text}{truncated}"
            )
    if not lines:
        return ""
    header = (
        "# Контекст чата (последние сообщения)\n"
        "Маркер `↩reply_to=N` означает что юзер использовал Telegram-Reply\n"
        "на сообщение `[id=N]`. Это ЖЁСТКИЙ якорь контекста — когда видишь\n"
        "местоимения «его / её / этот / эту / их / тот» в сообщении с\n"
        "reply_to, перейди к сообщению-родителю и оттуда вытащи объект,\n"
        "к которому относится местоимение. Не отвечай «кого именно?» —\n"
        "ответ в parent-сообщении.\n"
    )
    return header + "\n".join(lines)


def _media_history_label(row: MessageLog) -> str:
    if row.media_type == "pdf":
        name = f": {row.media_file_name}" if row.media_file_name else ""
        return f"PDF{name}"
    if row.media_type == "photo":
        return "фото/скрин без текста"
    if row.media_type:
        return row.media_type
    return "медиа без текста"


VOICE_TRANSCRIBE_CATCHUP_MIN = 10
VOICE_TRANSCRIBE_CATCHUP_CAP = 10


async def _ensure_recent_voices_transcribed(chat_id: int) -> int:
    """Inline-transcribe any voice_messages in this chat from the last
    `VOICE_TRANSCRIBE_CATCHUP_MIN` minutes that don't yet have a
    transcript. Returns how many we kicked off.

    Prevents the "прослушай предыдущие голосовые" race: when the user
    fires voices then immediately triggers the bot, the background
    transcribe tasks may still be running; without this the analyzer
    sees empty placeholders and the bot answers "без транскрипций".

    Capped at VOICE_TRANSCRIBE_CATCHUP_CAP per call to avoid turning a
    single mention into a multi-minute Whisper marathon.
    """
    from sqlalchemy import select as _select

    from src.core.voice_transcribe import transcribe_voice_row

    cutoff = datetime.now(UTC) - timedelta(minutes=VOICE_TRANSCRIBE_CATCHUP_MIN)
    async with session_scope() as session:
        res = await session.execute(
            _select(VoiceMessage.id)
            .where(
                VoiceMessage.chat_id == chat_id,
                VoiceMessage.created_at >= cutoff,
                VoiceMessage.transcribed_text.is_(None),
                VoiceMessage.ogg_data.isnot(None),
            )
            .order_by(VoiceMessage.id.asc())
            .limit(VOICE_TRANSCRIBE_CATCHUP_CAP)
        )
        pending_ids = [row[0] for row in res.all()]
    if not pending_ids:
        return 0

    log.info("voice_catchup_start", chat_id=chat_id, count=len(pending_ids))

    # Bounded parallelism — Whisper on CPU int8 can't effectively use
    # more than ~2 parallel sessions without thrashing.
    sem = asyncio.Semaphore(2)

    async def _one(vid: int) -> None:
        async with sem:
            try:
                async with session_scope() as session:
                    await transcribe_voice_row(session, vid)
            except Exception:
                log.exception("voice_catchup_failed", voice_id=vid)

    await asyncio.gather(*[_one(v) for v in pending_ids])
    log.info("voice_catchup_done", chat_id=chat_id, count=len(pending_ids))
    return len(pending_ids)


async def analyze_batch(
    batch: Batch,
    *,
    knowledge_items: list[dict] | None = None,
    bot: Bot | None = None,
) -> BatchAnalysis:
    rendered = _format_batch(batch)

    # Before pulling recent history, drain any pending voice
    # transcriptions for this chat — otherwise a user flurry of voices +
    # immediate @-mention hits history before Whisper writes transcripts.
    try:
        await _ensure_recent_voices_transcribed(batch.chat_id)
    except Exception:
        log.exception("voice_catchup_outer_failed", chat_id=batch.chat_id)

    # Pull conversation history — everything except the messages that are
    # already inside `batch` (the analyzer would otherwise see them twice).
    batch_ids: set[int] = set()
    if batch.trigger:
        batch_ids.add(batch.trigger.tg_message_id)
    batch_ids.update(m.tg_message_id for m in batch.messages)
    recent_limit = (
        RECENT_HISTORY_EXPANDED_WINDOW
        if _needs_deep_history(batch, rendered)
        else RECENT_HISTORY_DEFAULT_WINDOW
    )
    recent_history = await _recent_history(batch.chat_id, batch_ids, limit=recent_limit)

    media_context = await collect_requested_media_context(bot=bot, batch=batch)

    # Pull a mix of verified examples across the most likely intents.
    few_shot_items = await _collect_few_shot()

    # Sticker library + usage examples so Claude knows which emojis are
    # actually resolvable, sees Vision descriptions for picking by meaning,
    # and learns from recent human usage.
    (
        sticker_packs,
        sticker_catalog,
        sticker_examples,
    ) = await _collect_sticker_context()

    # Live POA-clients snapshot — injected into the uncached tail so LLM
    # can answer «кого сняли / у кого баланс» from real DB data, not
    # from incomplete recent_history.
    poa_snapshot = await _poa_snapshot() if _needs_poa_snapshot(rendered) else None

    # `recent_history` is the non-cached last system block — it changes every
    # request, so we keep the cached sections ahead of it.
    system_blocks = build_system_blocks(
        knowledge_items=knowledge_items,
        few_shot_examples=few_shot_items,
        sticker_pack_emojis=sticker_packs,
        sticker_described_catalog=sticker_catalog,
        sticker_usage_examples=sticker_examples,
        poa_snapshot=poa_snapshot,
        recent_messages=recent_history or None,
        analyzer_instructions=BATCH_INSTRUCTION,
    )

    user_prompt = f"Messages to analyze now:\n{rendered}"
    if media_context and media_context.text:
        user_prompt += f"\n\n{media_context.text}"

    user_content: str | list[dict[str, Any]]
    if media_context and media_context.content_blocks:
        user_content = [{"type": "text", "text": user_prompt}]
        user_content.extend(media_context.content_blocks)
    else:
        user_content = user_prompt

    resp = await complete(
        system_blocks=system_blocks,
        messages=[{"role": "user", "content": user_content}],
        tools=[ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "analyze_batch"},
        max_tokens=2500,
        temperature=0.2,
    )

    payload: dict | None = None
    for block in resp.raw.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "analyze_batch":
            payload = block.input  # type: ignore[assignment]
            break

    if payload is None:
        log.warning("batch_analyzer_no_tool_use", size=len(batch.messages))
        return BatchAnalysis(operations=[], chat_only=True)

    try:
        result = BatchAnalysis.model_validate(payload)
    except Exception as e:
        log.warning("batch_analyzer_validation_failed", error=str(e))
        return BatchAnalysis(operations=[], chat_only=True)

    # Cache-efficiency observability — track hit/write ratio so we can
    # spot regressions in the prompt-caching pipeline early.
    cache_write = resp.cache_creation_input_tokens or 0
    cache_read = resp.cache_read_input_tokens or 0
    fresh_input = resp.input_tokens or 0
    total_in = cache_write + cache_read + fresh_input
    hit_ratio = (cache_read / total_in * 100.0) if total_in else 0.0
    log.info(
        "batch_analyzer_result",
        size=len(batch.messages),
        n_ops=len(result.operations),
        chat_only=result.chat_only,
        cache_write_tokens=cache_write,
        cache_read_tokens=cache_read,
        input_tokens=fresh_input,
        output_tokens=resp.output_tokens,
        cache_hit_ratio_pct=round(hit_ratio, 1),
    )
    return result
