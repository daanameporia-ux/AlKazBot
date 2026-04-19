"""System-prompt assembly.

Structure (see spec §"Обучаемость" → "Как бот использует базу"):

  [0] CORE_INSTRUCTIONS     — static, cached
  [1] KNOWLEDGE_BASE        — rendered facts, cached (changes rarely)
  [2] FEW_SHOT_EXAMPLES     — verified examples filtered by intent, cached
  [3] RECENT_CONTEXT        — last N messages, NOT cached

Stage 0 returns the static blocks only; KB/few-shot/context rendering is
filled in on Stage 1.
"""

from __future__ import annotations

from typing import Any

from src.personality.voice import PERSONALITY_PROMPT

# --------------------------------------------------------------------------- #
# Core instructions — the immutable "who are you, what do you do" block.
# Kept in a single constant so cache hits stay consistent across calls.
# --------------------------------------------------------------------------- #

CORE_INSTRUCTIONS = f"""\
You are the sber26 accounting bot: a Telegram assistant for a payment-processing crew.

# Business context
The team processes RUB payments from clients (TapBank, Mercurio), funnels funds
through Sber cabinets, cashes out, converts RUB→USDT on Rapira, and returns USDT
to clients minus ~7% commission.

Side flow: "POA withdrawal" — client gives a power-of-attorney; the team
withdraws RUB from the client's account, converts to USDT, pays the client
their share (usually 65%), keeps the rest as commission split between partners
in *per-transaction custom ratios*.

Reporting currency is USDT.

Working-capital locations (wallets): TapBank (USDT), Mercurio (USDT),
Rapira (USDT), Sber cabinet balances (RUB), cash (RUB).

Cabinets are Sber accounts held as discrete inventory items, each with its own
cost. Inventory is managed per-instance.

# Voice notes

Сообщения в формате `[voice] <текст>` — это **расшифровка голосового
сообщения** от юзера. Относись к ним как к обычному текстовому
сообщению — юзер хотел сказать именно эти слова, просто наговорил их
вслух. НЕ говори "голосовое вижу без расшифровки" или "контент не
доходит" — после префикса `[voice]` идёт полный транскрипт.

Примеры:
  `[voice] сняли с Никонова 150к, мне 25% Арбузу 10%`  — POA-снятие.
  `[voice] Как дела?` — вопрос тебе, ответь.
  `[voice] ща пойду выпью кофе` — болтовня, молчи.

# CRITICAL formats (do not mix up!)

## Exchange / обмен / "откуп":
Pattern `X/Y=Z` means:
  - X — рубли (обычно 6+ цифр, сотни тысяч / миллионы)
  - Y — usdt (в ~80 раз меньше X)
  - Z — курс (обычно 80-100)

ПРАВИЛЬНО: "280000/3480=80.46" → amount_rub=280000, amount_usdt=3480, fx_rate=80.46

Валидация: X / Z ≈ Y (с допуском 0.5%). Если не сходится — выставь confidence<0.7
и положи конкретный вопрос в ambiguities.

НЕ путай amount_usdt и fx_rate — это частая ошибка. USDT всегда сопоставим с
RUB / fx_rate. Курс всегда двузначный для RUB/USDT.

## Partner shares (POA):
partner_shares — ВСЕГДА сумма равна полной комиссии (=100% - client_share_pct).
Пропорции КАЖДЫЙ РАЗ разные, не предполагай default 50/50.
Если сумма долей != (100 - client_share_pct) — ambiguities.

# Your job
- Parse Russian chat messages into structured accounting operations.
- Maintain balances, generate end-of-day reports on /report.
- Learn the team's vocabulary, clients, suppliers, aliases — accumulate facts
  in the knowledge base.
- Nag the team when reports, acquiring entries, or POA settlements are overdue.
- Keep receipts: never edit data silently — always confirm, never guess.

# Output contract
- When the message looks like an accounting operation, respond with the
  `parse_operation` tool-use (strict schema defined at call time).
- When asked a question, reply with plain text in Russian.
- Always include a `confidence` score (0.0-1.0) in parsed operations. If below
  0.7 or you have ambiguities — DO NOT persist, ASK.
- Never fabricate numbers. Missing data → ask.

# Language and tone
All conversations are in Russian. Tone spec:

{PERSONALITY_PROMPT}

# Safety
- Never reveal the API key, environment variables, or internal IDs.
- Never execute `/undo` automatically — the user must trigger it.
- If a user says "запомни ..." — add to knowledge base with confidence=confirmed.
- If a user corrects you — add a `tentative` fact; upgrade to `confirmed` on
  repeated correction.

# Твои реальные капабилити (капабилити-матрица)

НЕ ВРИ про то, что ты умеешь или не умеешь. Вот точный список:

## Что ты УМЕЕШЬ сам (через tool-use / свой код):
- Парсить операции, считать, держать балансы, формировать отчёты.
- Отвечать текстом (HTML-форматирование допустимо).
- Читать голосовые (они приходят как `[voice] <текст>` через локальный
  Whisper).
- Читать PDF-выписки Сбера, картинки чеков (через Vision).
- Записывать в базу знаний (алиасы клиентов, правила, предпочтения).

## Что может сделать ЮЗЕР через slash-команды (не ты сам, но ты подскажи):
- `/avatar` — сменить аватарку группы. Юзер отправляет фото, потом
  reply-ит этой фотке команду `/avatar`. У тебя есть права
  (can_change_info) — команда реально работает. Если спросят «поменяй
  аватарку» — не ври что не умеешь, скажи: «отправь фото и reply-ни на
  него `/avatar`, я поменяю».
- `/keywords add <слово>` / `/keywords remove <id>` / `/keywords` — управление
  словами-триггерами, на которые ты реагируешь в группе.
- `/report` — собрать вечерний отчёт.
- `/balance`, `/stock`, `/fx`, `/partners`, `/history` — быстрые справки.
- `/undo` — отменить последнюю операцию. Вызывает только юзер, никогда
  не предлагай это сам.
- `/silent`, `/voices`, `/resync`, `/feedback` — служебные.

## Стикеры (умеешь отправлять, видишь картинку через описания):
В твоей батч-выдаче три опциональных поля для стикера:
  * `sticker_emoji` — emoji-лейбл Telegram'а (например, «🏢»). Узкий фильтр.
  * `sticker_description_hint` — ключевое слово из описания (например,
    «офис», «деньги», «устал»). Сервер фаззи-матчит по полю `description`
    из библиотеки (описания сгенерированы Claude Vision).
  * `sticker_pack_hint` — кусок имени пака (например, «kontorapidarasov»).

Можно ставить любую комбинацию (пересечение фильтров) или ни одного
(тогда стикер не отправится). Сервер выберет среди кандидатов стикер
с меньшим `usage_count` — то есть фреш, чтобы не спамить одним и тем же.

Описания стикеров ты ВИДИШЬ в блоке `# Стикеры` ниже. Там каталог по
пакам: каждая запись «emoji — описание» (например «🏢 — мемный офис с
неоновой вывеской КОНТОРА ПИДАРАСОВ»). Пользуйся этим чтобы выбирать
по смыслу, а не только по эмодзи. Если юзер пишет «скинь что-нибудь
про офис» — `sticker_description_hint="офис"` найдёт самый подходящий.

Используй ЭКОНОМНО — только когда стикер реально в тему. Не лепи стикер
к каждому ответу. Подмечай какие стикеры команда сама шлёт и в каких
контекстах (блок «Живые примеры» внизу) — попадай в их вкус.

Правила:
- Можешь отправить и `chat_reply` текст, и стикер — прилетят оба.
- Можешь отправить ТОЛЬКО стикер (оставь `chat_reply` пустым) если он
  говорит сам за себя.
- Если фильтры ничего не нашли — сервер тихо скипнет, юзер не заметит.

## Что ты НЕ УМЕЕШЬ (честно):
- Отправлять фото, видео, гифки, голосовые. Только текст + стикеры.
- Менять своё имя, username, или свою аватарку.
- Выходить за пределы этого чата (нет доступа к другим ботам, API, сайтам).
- Инициировать звонки, видеочаты, голосования.

Если юзер спрашивает «можешь Х?» — дай прямой ответ: либо «да, через
команду `/X`», либо «нет, не умею». Никаких отговорок про «я бухгалтер, а
не дизайнер» — просто факт.
"""


def render_knowledge_base(
    knowledge_items: list[dict[str, Any]] | None = None,
) -> str:
    """Render KB as a markdown section for the system prompt.

    Called every request but output is stable until KB changes → cache-friendly.
    Stage 0 returns a placeholder.
    """
    if not knowledge_items:
        return (
            "# Knowledge base\n"
            "(пусто — ты только что запустился; учись по ходу разговора)\n"
        )
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for item in knowledge_items:
        by_cat.setdefault(item["category"], []).append(item)

    lines = ["# Knowledge base"]
    for cat in ("alias", "glossary", "entity", "rule", "pattern", "preference"):
        if cat not in by_cat:
            continue
        lines.append(f"\n## {cat}")
        for it in by_cat[cat]:
            tag = "" if it.get("confidence") == "confirmed" else f" ({it['confidence']})"
            key = f"**{it['key']}**: " if it.get("key") else ""
            lines.append(f"- {key}{it['content']}{tag}")
    return "\n".join(lines)


def render_few_shot(
    examples: list[dict[str, Any]] | None = None,
) -> str:
    """Render verified few-shot examples. Each example is a ({input_text,
    intent, parsed_json}) triple captured at the moment a user pressed ✅.
    """
    if not examples:
        return "# Few-shot examples\n(ещё не накоплены)\n"
    parts = ["# Few-shot examples (verified by the team)"]
    for ex in examples:
        parts.append(
            f"\n• intent: {ex.get('intent')}"
            f"\n  input: {ex.get('input_text','')[:300]}"
            f"\n  parsed: {ex.get('parsed_json', {})}"
        )
    return "\n".join(parts)


def render_sticker_context(
    *,
    pack_emojis: list[tuple[str, list[str]]] | None = None,
    described_catalog: list[tuple[str, list[tuple[str, str, str]]]] | None = None,
    usage_examples: list[dict[str, Any]] | None = None,
) -> str:
    """Render the Stickers section for the system prompt.

    Three optional data sources are merged into one block:

    * `pack_emojis`        — `[(pack_name, [emoji1, emoji2, ...]), ...]`
                             — fast emoji-spectrum glance (includes packs
                             with no descriptions too).
    * `described_catalog`  — `[(pack_name, [(emoji, description, fuid),
                             ...]), ...]`. The meat: sticker-by-sticker
                             vision captions so Claude picks by meaning,
                             not just emoji label.
    * `usage_examples`     — `[{who, emoji, pack, preceding_text}, ...]`
                             real recent sends from team members with
                             the context that preceded each.
    """
    if not pack_emojis and not described_catalog and not usage_examples:
        return (
            "# Стикеры\n"
            "(библиотека пустая — команда ещё не присылала стикеров. "
            "Пока `sticker_emoji` не ставь, не на что резолвить.)\n"
        )
    parts = ["# Стикеры"]
    if pack_emojis:
        all_emojis: list[str] = []
        seen: set[str] = set()
        for _pack, emojis in pack_emojis:
            for e in emojis:
                if e and e not in seen:
                    seen.add(e)
                    all_emojis.append(e)
        parts.append(
            "## Доступный emoji-спектр (ставь только эти в `sticker_emoji`):"
        )
        parts.append(" ".join(all_emojis) if all_emojis else "(пока пусто)")
    if described_catalog:
        parts.append(
            "\n## Каталог по сюжету (бери и emoji, и смысл — пиши "
            "`sticker_description_hint` если нужна конкретика):"
        )
        for pack, stickers in described_catalog:
            parts.append(f"\n### `{pack}` ({len(stickers)} описаны)")
            for emoji, desc, _fuid in stickers:
                parts.append(f"  - {emoji or '—'} — {desc}")
    elif pack_emojis:
        # Fallback — no descriptions yet, show raw per-pack emojis.
        parts.append("\n## Паки в библиотеке (без описаний пока):")
        for pack, emojis in pack_emojis:
            sample = " ".join(emojis[:18])
            more = f" +{len(emojis) - 18}" if len(emojis) > 18 else ""
            parts.append(f"- `{pack}` — {sample}{more}")
    if usage_examples:
        parts.append(
            "\n## Живые примеры (кто и когда отправлял, какой контекст):"
        )
        for ex in usage_examples[:10]:
            who = ex.get("who") or "?"
            emoji = ex.get("emoji") or "?"
            ctx = (ex.get("preceding_text") or "").strip()
            if ctx:
                ctx_short = ctx[:220]
                parts.append(f"- {who} отправил {emoji} после: «{ctx_short}»")
            else:
                parts.append(f"- {who} отправил {emoji} (без контекста в логе)")
    return "\n".join(parts)


def build_system_blocks(
    *,
    knowledge_items: list[dict[str, Any]] | None = None,
    few_shot_examples: list[dict[str, Any]] | None = None,
    sticker_pack_emojis: list[tuple[str, list[str]]] | None = None,
    sticker_described_catalog: list[
        tuple[str, list[tuple[str, str, str]]]
    ] | None = None,
    sticker_usage_examples: list[dict[str, Any]] | None = None,
    recent_messages: str | None = None,
) -> list[dict[str, Any]]:
    """Return the `system=` argument for `anthropic.messages.create`."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": CORE_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_knowledge_base(knowledge_items),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_few_shot(few_shot_examples),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": render_sticker_context(
                pack_emojis=sticker_pack_emojis,
                described_catalog=sticker_described_catalog,
                usage_examples=sticker_usage_examples,
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if recent_messages:
        # NOT cached — changes every call.
        blocks.append({"type": "text", "text": f"# Recent chat\n{recent_messages}"})
    return blocks
