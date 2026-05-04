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
                            "description": "tg_message_id-ы из батча, на которых эта операция.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short Russian sentence like 'Снятие 150k с Никонова'.",
                        },
                        "fields": {
                            "type": "object",
                            "description": (
                                # Только сигнатуры полей. Семантика, разница «balance vs withdrawal»,
                                # «in_use vs worked_out», категории knowledge_teach, паттерны
                                # partner_contribution — всё в BATCH_INSTRUCTION (analyzer_instructions
                                # подклеен к CORE). Раньше всё дублировалось здесь и в инструкции —
                                # это +1.4k tokens cached без надобности.
                                "Intent-specific fields:\n"
                                "exchange: {amount_rub, amount_usdt, fx_rate}\n"
                                "expense: {category, amount_rub?, amount_usdt?, description}\n"
                                "partner_withdrawal: {partner, amount_usdt, from_wallet?}\n"
                                "partner_deposit: {partner, amount_usdt}\n"
                                "partner_contribution: {partner, amount_usdt,"
                                " source?:('initial_depo'|'manual'|'poa_share'), notes?}\n"
                                "poa_withdrawal: {client_name, amount_rub,"
                                " partner_shares:[{partner,pct}], client_share_pct}\n"
                                "client_balance: {client_name, amount_rub,"
                                " source?:('card'|'sber_account'|'unknown'), description?}\n"
                                "cabinet_purchase: {name?, cost_rub, prepayment_ref?}\n"
                                "cabinet_in_use: {name_or_code}\n"
                                "cabinet_worked_out: {name_or_code}\n"
                                "cabinet_blocked: {name_or_code}\n"
                                "cabinet_doverka_received: {name_or_code}\n"
                                "prepayment_given: {supplier, amount_rub, expected_cabinets?}\n"
                                "prepayment_fulfilled: {supplier, cabinets:[{name,cost_rub}]}\n"
                                "wallet_snapshot: {tapbank?, mercurio?, rapira?, sber_balances?, cash?}\n"
                                "client_payout: {client_name, amount_usdt}\n"
                                "knowledge_teach: {category, key?, content}\n"
                                "wakeword_add: {word: str}"
                            ),
                        },
                        "ambiguities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Что неясно. Бот спросит юзера перед записью "
                                "если non-empty ИЛИ confidence<0.7."
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
                    "Russian reply to chat. Required iff chat_only=true И в "
                    "батче есть trigger. Без триггера — пустая строка."
                ),
            },
            "sticker_emoji": {
                "type": "string",
                "description": "Emoji to narrow sticker pick (см. блок Стикеры).",
            },
            "sticker_description_hint": {
                "type": "string",
                "description": (
                    "Russian noun/verb для substring-match по Vision-описанию "
                    "(ILIKE '%X%'). Примеры: 'офис', 'деньги', 'устал', 'кот'."
                ),
            },
            "sticker_pack_hint": {
                "type": "string",
                "description": "Substring имени пака (e.g. 'kontorapidarasov').",
            },
            "sticker_theme_hint": {
                "type": "string",
                "description": (
                    "Точная label из seen_stickers.pack_theme. Known: 'сбер-мем'."
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

Ты получаешь список сообщений из группы. `[trigger message (...)]` —
текущий запрос. Обычные `[id=...]` — пассивный контекст.

Для каждой операции верни `BatchOperation`: intent (enum),
source_message_ids, summary (одна строка), fields (см. schema),
ambiguities (вопросы перед записью), confidence (понижай на сомнении).

Поиск операций в свободной речи и общие правила тона/честности — см.
CORE_INSTRUCTIONS блок «Поиск операций» и «Жёсткие правила». Здесь —
только batch-специфичные нюансы.

## Cabinet «в работу» vs «отработал» — критично

  * «в работу / запускаю / ставим» → `cabinet_in_use` (in_stock→in_use)
  * «отработан / выебан / списываем» → `cabinet_worked_out` (in_use→worked_out)

Живой баг: «завтра в работу Даут» парсилось как worked_out. Не путай.

## POA-снятие vs БАЛАНС клиента — критично

Самый частый косяк. Различие:

**`poa_withdrawal`** требует ЯВНЫЙ глагол снятия («снял/вытащили/
забрали/окэшил») И все 4 поля (client_name, amount_rub,
client_share_pct, partner_shares) — иначе applier падает. ВРЕМЕННО
доли НЕ нужны (см. KB rule `shares-disabled-temp` — грузится в kernel).

**`client_balance`** = просто отчёт остатка БЕЗ глагола снятия:
  - `Аймурат 62к карта` / `Баскова 50346` → balance
  - `Войтик пусто` → balance amount_rub=0
  - `Вакальчук ненаход` → balance amount_rub=0, description='ненаход'

Нет глагола снятия → НИКОГДА не делай poa_withdrawal. Сомневаешься
— делай `client_balance` (мягкий путь).

## НЕ операции

  * Входящие СБП на наш Сбер от физиков — НЕ отдельная операция.
    Учитываются через wallet_snapshot одной строкой в /report.
  * Скрины СМС, экран «банк напомнил пароль» — не плоди preview без
    явного «запиши/внеси».

Без явного «запиши» — если батч про деньги/инвентарь и confidence>=0.75,
делай preview. Юзер ✅/❌. Нет числа — `ambiguities`+confidence<0.7.

## chat_reply / молчание

Триггер есть, операций нет → `chat_only=true` + `chat_reply`. Триггера
нет и операций нет → `chat_only=true`, пустой reply, молчишь.

## Вложения (фото/PDF)

Если в prompt есть `# Вложения по текущему запросу` — там фото
(image-block) или PDF-текст с `SBER_HINT`/`ALIEN_PDF_HINT`.

Для фото: ATM-чек/expense/exchange/wallet_snapshot — извлекай.
Мем/селфи → chat_only.

PDF в операции — только если ВСЕ три: (a) явное «запиши/внеси/
оформи операции» в сообщении, (b) документ похож на счёт нашей команды
(ФИО есть в KB), (c) confidence≥0.8. Иначе спроси «это по нашему
счёту? занести?».

## Стикеры

Когда юзер просит «про X» — найди в каталоге описание с X, поставь
`sticker_description_hint=X` (+`sticker_pack_hint` если пак
тематически совпадает). НЕ ври про содержимое — если описание есть,
оно у тебя в кэше.

После отправки стикер попадает в recent_history как
`[sticker <emoji> · <description>]`. Спросят «что скинул» — цитируй
оттуда, не выдумывай.

## Teaching

«Запомни: X» / факт о бизнесе → один или несколько `knowledge_teach`.
Категории и формат — в schema (см. fields description). Несколько
фактов в одном сообщении → несколько отдельных карточек.

## Бизнес-советник

Заметил полезный паттерн (Никонов 3-й раз за день / курс прыгнул /
кабинет завис 12ч) → вставь одну строку через `💡` в chat_reply.
Только по делу.

## Главное

Лучше карточка с ambiguities+confidence<0.7, чем кривая запись или
пропуск реальной операции. Юзер имеет последнее слово через ✅/❌.
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
        # min_usage=1 → в системный prompt идут только реально юзаные
        # стикеры. Остальные 450+ Vision-описаний не грузим в кэш —
        # экономит ~12k tokens cached. Новые стикеры подтянутся как
        # только команда впервые их пошлёт (usage_count перестаёт быть 0).
        catalog = await sticker_repo.described_catalog(
            session, per_pack=12, pack_limit=6, min_usage=1
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


async def _lazy_kb_for_batch(rendered: str) -> list[dict[str, Any]]:
    """Find KB facts whose key/content overlaps with the current batch text.

    These are non-kernel facts (always_inject=false) that we only want
    in the prompt when actually relevant — e.g. «карен-pack-balance-2»
    when the batch mentions Карен. Loaded into uncached tail to avoid
    cache-write costs for every справочный fact in the DB.
    """
    from src.db.repositories import knowledge as kb_repo

    if not rendered:
        return []
    async with session_scope() as session:
        rows = await kb_repo.lookup_for_text(
            session, rendered, limit=5, min_confidence="inferred"
        )
    return [
        {
            "id": r.id,
            "category": r.category,
            "key": r.key,
            "content": r.content,
            "confidence": r.confidence,
        }
        for r in rows
    ]


async def _poa_snapshot(rendered: str = "") -> str | None:
    """Render a fresh snapshot of POA-clients with status + last balance.

    Modes:
      • если в `rendered` упомянуты конкретные имена клиентов (substring
        match по полному имени или фамилии) — рендерим **только** этих
        клиентов компактным блоком. Экономит ~700 tokens uncached на
        запросах вида «какой баланс у Семак».
      • если совпадений нет — рендерим только активные секции
        (has_balance / on_hold / unchecked). Закрытые (withdrawn /
        no_balance / not_found) выкидываем, они не нужны для типичных
        вопросов «че по партии» и режут ~30-40% объёма блока.
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

    lo_text = (rendered or "").lower()

    # Mode 1: name-specific. Find which client names appear in batch text.
    matched_names: set[str] = set()
    if lo_text:
        for nm, *_ in rows:
            # Check full name OR any 4+ char surname token (handles
            # «Семак» matching "Семак" в "семак 93к", and «Аймурат Думан»
            # matching either token).
            full = nm.lower()
            if full in lo_text:
                matched_names.add(nm)
                continue
            for tok in full.split():
                tok = tok.strip(".,;:!?«»\"'")
                if len(tok) >= 4 and tok in lo_text:
                    matched_names.add(nm)
                    break

    if matched_names:
        # Compact block — just the matched clients. Header без preamble:
        # инструкция «как читать этот блок» уже в CORE_INSTRUCTIONS.
        parts = ["# POA-клиенты (упомянутые в запросе)"]
        for nm, status, bal, descr, ts, src in rows:
            if nm not in matched_names:
                continue
            ts_str = ts.strftime("%d.%m") if ts else "—"
            if bal in (None, 0) and descr:
                val = descr
            elif bal in (None, 0):
                val = "пусто"
            else:
                val = f"{int(bal):,}₽".replace(",", " ")
            src_part = f" [{src}]" if src and src != "unknown" else ""
            parts.append(f"  • {nm}: {val}{src_part}  [{status}, {ts_str}]")
        return "\n".join(parts)

    # Mode 2: nothing matched → active sections only.
    sections = {
        "has_balance": ("💰 Баланс есть, не сняли", []),
        "on_hold": ("⏸ Проблемные (паспорт/блок)", []),
        "search_request": ("🔎 Нужно обращение на розыск счёта", []),
        "unchecked": ("⏳ Не проверяли", []),
    }
    has_balance_total = 0
    for nm, status, bal, descr, ts, src in rows:
        if status not in sections:
            continue
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
        sections[status][1].append(f"  • {nm}: {val}{src_part}  ({ts_str})")

    parts = ["# POA-клиенты — активные (закрытые см. /balances)"]
    for code in ("has_balance", "on_hold", "search_request", "unchecked"):
        title, lines = sections[code]
        if not lines:
            continue
        parts.append(f"\n{title} ({len(lines)}):")
        parts.extend(lines)
    if has_balance_total > 0:
        parts.append(
            f"\n💰 Сумма ждущих снятия: {has_balance_total:,} ₽".replace(",", " ")
        )
    return "\n".join(parts) if len(parts) > 1 else None


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


def _needs_sticker_examples(batch: Batch, rendered: str) -> bool:
    """Decide if uncached sticker_usage_examples block is worth ~570 tokens.

    Yes when:
      - текущий триггер = sticker (юзер прислал стикер боту);
      - в батче есть стикеры от людей (бот учится на них);
      - юзер прямо просит стикер ("кинь стикер", "стикер пж", "ответь стикером");
      - батч короткий и эмодзи-heavy (вероятно реакция-стикером ожидается).
    """
    if batch.trigger_kind == "sticker":
        return True
    lo = rendered.lower()
    if any(t in lo for t in ("стикер", "стик", "кинь стик", "ответь стик")):
        return True
    # Detect any sticker placeholder in the rendered batch (means bot has
    # something to learn from this turn).
    return "[sticker " in lo or "[стикер " in lo


def _needs_poa_snapshot(rendered: str) -> bool:
    lo = rendered.lower()
    # Substring-match (русские падежи: «снят» ловит «снять/снятие/снятия/снятий»;
    # «довер» ловит «доверка/доверенность/доверок»; «партии» через «парт»).
    # Был баг 04.05: «список на снятия» не триггерил → бот зачитал старую
    # KB-rule вместо актуального snapshot.
    return any(
        token in lo
        for token in (
            "poa",
            "поа",
            "довер",       # доверка / доверенность / доверок
            "клиент",
            "баланс",
            "снят",        # снятие / снятий / снятия / снят / сняли / снять
            "сним",        # снимать / снимаем / сними
            "снима",
            "доля",
            "доли",
            "долг",
            "пусто",
            "ненаход",
            "розыск",      # 'на розыск счёта'
            "обращени",    # 'обращение в банк'
            "паспорт",     # 'паспорт-проблема'
            "партии",      # 'из партии 02.05'
            "партия",
            "партию",
            "пачк",        # 'пачка кабинетов', 'пачка доверок'
            "список",      # «дай список» → почти всегда про POA в этом боте
            "сводк",       # «сводка по партии»
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
    # Header сокращён: `↩reply_to` правило теперь в CORE_INSTRUCTIONS
    # (не дублируем 320 chars в каждый uncached запрос).
    return "# Контекст чата (последние сообщения)\n" + "\n".join(lines)


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

    # Sticker library + usage examples. Library (cached) грузим всегда —
    # дёшево и без него бот не знает что выбрать. Usage examples
    # (uncached, ~570 tokens) — только когда батч намекает на стикер-режим
    # (sticker-only, emoji-heavy reaction, прямая просьба «кинь стикер»).
    # Раньше грузились на каждый батч включая длинные talky-разборы — пустая трата.
    (
        sticker_packs,
        sticker_catalog,
        sticker_examples_full,
    ) = await _collect_sticker_context()
    sticker_examples = (
        sticker_examples_full if _needs_sticker_examples(batch, rendered) else None
    )

    # Live POA-clients snapshot — injected into the uncached tail so LLM
    # can answer «кого сняли / у кого баланс» from real DB data, not
    # from incomplete recent_history.
    poa_snapshot = (
        await _poa_snapshot(rendered) if _needs_poa_snapshot(rendered) else None
    )

    # Lazy KB facts: pull only the справочные records whose key/content
    # actually matches the current batch text. Cached KB block holds only
    # the kernel set (always_inject=true) — see kb_repo.list_facts(only_kernel)
    # and the 0024 migration for context.
    lazy_kb = await _lazy_kb_for_batch(rendered)

    # `recent_history` is the non-cached last system block — it changes every
    # request, so we keep the cached sections ahead of it.
    system_blocks = build_system_blocks(
        knowledge_items=knowledge_items,
        few_shot_examples=few_shot_items,
        sticker_pack_emojis=sticker_packs,
        sticker_described_catalog=sticker_catalog,
        sticker_usage_examples=sticker_examples,
        poa_snapshot=poa_snapshot,
        lazy_kb_facts=lazy_kb,
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
        max_tokens=1500,  # 2500 был запас «на всякий», реальный p99 ответ <1200
        temperature=0.2,
        call_kind="batch_analyzer",
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
