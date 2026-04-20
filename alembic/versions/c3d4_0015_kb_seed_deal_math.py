"""KB seed — модель сделки RUB→USDT и формулы прибыли.

Driven by live chat 2026-04-20 15:00–16:10: bot was asked to compute
deal profit three times, gave three different (sometimes wrong)
formulas. Root cause: no canonical deal model in KB; bot reassembled
the math each time, sometimes missing the Rapa fee, sometimes the
cashout loss, sometimes the merchant reward.

Seeded here:
- entity for deal parameters (K_deal, K_rapa, F_rapa, R_merch, L_cashout)
- glossary for screenshot conventions («верхняя/нижняя сумма»)
- glossary for «грязный» / «чистый» / «грязный от рапы» / «чистый спред»
- the canonical profit formula (rule) — cited by bot whenever asked
- default fees: Rapa 1.5%, cashout loss 1%

Revision ID: c3d40015dealmath
Revises: b2c30014karen
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op

revision = "c3d40015dealmath"
down_revision = "b2c30014karen"
branch_labels = None
depends_on = None


_FACTS: list[tuple[str, str | None, str, str]] = [
    # ---- Screenshot conventions ---------------------------------------------
    (
        "glossary",
        "нижняя сумма на скрине",
        "USDT, которые мы отдали клиенту по сделке "
        "(U_client = A_rub / K_deal). Нижняя цифра на "
        "скриншоте площадки-эквайера (TapBank / Deora / Пейго).",
        "confirmed",
    ),
    (
        "glossary",
        "верхняя сумма на скрине",
        "Вознаграждение эквайера (R_merch) в USDT — то, что "
        "процессинг платит нам сверху за проведённую сделку. "
        "Верхняя цифра на скриншоте.",
        "confirmed",
    ),
    # ---- Deal parameters ----------------------------------------------------
    (
        "glossary",
        "курс сделки",
        "K_deal — курс, по которому мы отдаём клиенту USDT за его "
        "рубли (rub/USDT). Всегда выше курса рапиры. Видно в сделке "
        "эквайера или в сообщении «курс сделки X».",
        "confirmed",
    ),
    (
        "glossary",
        "курс рапиры",
        "K_rapa — курс покупки USDT на Rapira (rub/USDT), чистая "
        "цена без комиссии биржи. Из /fx или сообщения «рапира X».",
        "confirmed",
    ),
    (
        "glossary",
        "комиссия рапиры",
        "F_rapa ≈ 1.5% — комиссия биржи при покупке USDT за рубли. "
        "Эффективный курс откупа: K_rapa × (1 + F_rapa). Default: 1.5% "
        "пока не сказано иначе.",
        "confirmed",
    ),
    (
        "glossary",
        "потери на откупе",
        "L_cashout ≈ 1% — прочие потери при конвертации RUB→USDT "
        "(обналичивание, переводы, прилипания). Default: 1% пока не "
        "сказано иначе. Суммарная потеря с рапиры = F_rapa + L_cashout.",
        "confirmed",
    ),
    (
        "glossary",
        "вознаграждение эквайера",
        "R_merch — выплата от процессинга нам за каждую проведённую "
        "сделку (в USDT). Обычно 7-15% от суммы сделки в USDT. "
        "На скрине = верхняя сумма.",
        "confirmed",
    ),
    # ---- Gross vs net conventions -------------------------------------------
    (
        "glossary",
        "грязный процент от рапы",
        "Разница курсов в % относительно рапиры, БЕЗ учёта "
        "комиссий и без учёта вознаграждения эквайера: "
        "(K_deal - K_rapa) / K_rapa × 100%. «Чистая разность курсов на "
        "бумаге». НЕ равно прибыли.",
        "confirmed",
    ),
    (
        "glossary",
        "чистый процент от рапы",
        "Грязный процент минус ВСЕ потери (комиссия рапиры + потери "
        "на откупе): (K_deal - K_rapa) / K_rapa - F_rapa - L_cashout. "
        "Это реальный спред в %, без учёта вознаграждения эквайера "
        "(награда считается отдельно — в USDT или %-ах от сделки).",
        "confirmed",
    ),
    (
        "glossary",
        "чистый спред",
        "Полная чистая маржа: (K_deal - K_rapa)/K_rapa - F_rapa - "
        "L_cashout + R_merch/U_client. Включает И разницу курсов, И "
        "комиссии, И вознаграждение эквайера. Это то, что реально "
        "остаётся команде в USDT / в %.",
        "confirmed",
    ),
    # ---- The canonical formulas (rule) --------------------------------------
    (
        "rule",
        None,
        "МОДЕЛЬ СДЕЛКИ RUB→USDT. "
        "Входные: A_rub (рубли от клиента), K_deal (курс сделки), "
        "K_rapa (курс рапиры), F_rapa=1.5% (комиссия рапиры, default), "
        "L_cashout=1% (потери на откупе, default), R_merch "
        "(вознаграждение эквайера в USDT, из верхней суммы скрина). "
        "Расчёт: (1) U_client = A_rub / K_deal — отдали клиенту. "
        "(2) U_rapa = A_rub / (K_rapa × (1 + F_rapa)) — получили с рапы. "
        "(3) Spread_usdt = U_rapa − U_client — профит от разницы курсов. "
        "(4) Loss_usdt = U_rapa × L_cashout — потери на обнале. "
        "(5) Net_usdt = Spread_usdt + R_merch − Loss_usdt — ЧИСТАЯ "
        "ПРИБЫЛЬ сделки в USDT. "
        "(6) Net_pct = Net_usdt / U_client × 100 — маржа к объёму сделки.",
        "confirmed",
    ),
    (
        "rule",
        None,
        "КОГДА СЧИТАТЬ СДЕЛКУ: юзер пишет «посчитай сделку» / «чистая "
        "прибыль» / «грязный процент» / «чистый процент» / «сколько "
        "заработали». Бери default F_rapa=1.5%, L_cashout=1% если "
        "юзер не указал иначе в этом же или соседнем сообщении. "
        "Если не хватает R_merch или A_rub — СПРОСИ («что на верхней "
        "сумме скрина?» / «какая сумма сделки?»), НЕ додумывай.",
        "confirmed",
    ),
    (
        "rule",
        None,
        "ФОРМАТ ОТВЕТА по сделке: коротко, без длинных шагов. "
        "«Сделка X₽ @ K_deal: отдали Y USDT, с рапы Z USDT (F_rapa+L "
        "= N%), спред +A USDT, награда +B USDT, чистыми = C USDT "
        "(D% к объёму сделки).» Если юзер просит только проценты — "
        "«Грязный от рапы: G%, чистый спред (с учётом комиссии+потерь+"
        "награды): N%».",
        "confirmed",
    ),
    # ---- Pattern triggers ---------------------------------------------------
    (
        "pattern",
        None,
        "«Посчитай сделку / прибыль / спред / доходность / грязный "
        "процент / чистый процент» → применяй МОДЕЛЬ СДЕЛКИ (rule выше). "
        "Результат — `chat_reply`, не операция. В учёт сделка попадает "
        "через отдельный intent=exchange (если юзер скажет записать).",
        "confirmed",
    ),
    # ---- Deora platform reference -------------------------------------------
    (
        "alias",
        "Deora",
        "Ещё одна платёжка-эквайер (наряду с TapBank и Mercurio). "
        "У неё есть конструкторы — курсы и ставки свои под каждого "
        "мерчанта. Ставка ~7.5% к курсу 82.5, что около 10% чистыми.",
        "confirmed",
    ),
    (
        "alias",
        "Пейго",
        "Ещё один эквайер. Ставка ~14% фикс. Траф качественный ~98% "
        "(vs Deora ~90-93%).",
        "confirmed",
    ),
]


def upgrade() -> None:
    for cat, key, content, confidence in _FACTS:
        esc_content = content.replace("'", "''")
        esc_key = f"'{key.replace(chr(39), chr(39)*2)}'" if key else "NULL"
        key_clause = (
            f"AND key = '{key.replace(chr(39), chr(39)*2)}'"
            if key
            else "AND key IS NULL"
        )
        op.execute(
            f"""
            INSERT INTO knowledge_base (category, key, content, confidence, is_active, usage_count)
            SELECT '{cat}', {esc_key}, '{esc_content}', '{confidence}', TRUE, 0
            WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_base
                WHERE category = '{cat}'
                  AND content = '{esc_content}'
                  AND is_active = TRUE
                  {key_clause}
            )
            """
        )


def downgrade() -> None:
    for cat, key, content, _ in _FACTS:
        esc_content = content.replace("'", "''")
        key_clause = (
            f"AND key = '{key.replace(chr(39), chr(39)*2)}'"
            if key
            else "AND key IS NULL"
        )
        op.execute(
            f"""
            UPDATE knowledge_base
            SET is_active = FALSE
            WHERE category = '{cat}'
              AND content = '{esc_content}'
              {key_clause}
            """
        )
