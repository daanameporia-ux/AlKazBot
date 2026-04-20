"""Fix deal-math terminology per owner's clarification (2026-04-20):

- «Грязный процент от рапы» УЖЕ включает вознаграждение эквайера,
  но БЕЗ расходов на откуп (1-1.5%).
- «Чистый процент» = грязный − расходы на откуп.
- «Откуп» — ОДНА статья расходов (1-1.5%), покрывающая всё, что
  теряется на стороне Рапиры (комиссия биржи + прилипания +
  обналичка). Не два отдельных параметра F_rapa и L_cashout, а один
  F_откуп.

Deactivates the previous (too-mechanical) definitions from migration
c3d40015dealmath and seeds corrected versions.

Revision ID: d4e50016dealfix
Revises: c3d40015dealmath
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op

revision = "d4e50016dealfix"
down_revision = "c3d40015dealmath"
branch_labels = None
depends_on = None


# Deactivate the old versions by exact key match (since we seeded with key).
_DEACTIVATE_KEYS = [
    "грязный процент от рапы",
    "чистый процент от рапы",
    "чистый спред",
    "комиссия рапиры",
    "потери на откупе",
]

# Also deactivate the mega-rule so we replace it cleanly.
_DEACTIVATE_RULE_PREFIXES = (
    "МОДЕЛЬ СДЕЛКИ RUB→USDT.",
    "КОГДА СЧИТАТЬ СДЕЛКУ:",
    "ФОРМАТ ОТВЕТА по сделке:",
)

_NEW_FACTS: list[tuple[str, str | None, str, str]] = [
    # ---- Single unified Rapa-side loss --------------------------------------
    (
        "glossary",
        "откуп",
        "F_откуп — единая статья потерь при конвертации RUB→USDT на "
        "Рапире. Покрывает комиссию биржи, прилипания, обналичивание. "
        "Обычно 1-1.5%. Default: 1% пока юзер не сказал иначе (например, "
        "«1.5% на откупе»).",
        "confirmed",
    ),
    # ---- Gross / net per owner's definition (2026-04-20) -------------------
    (
        "glossary",
        "грязный процент от рапы",
        "Маржа УЖЕ с вознаграждением эквайера, но БЕЗ расходов на "
        "откуп: Грязный % = (K_deal − K_rapa)/K_rapa × 100 + "
        "(R_merch / U_client) × 100. В USDT: Grossy_usdt = "
        "(U_rapa_gross − U_client) + R_merch, где "
        "U_rapa_gross = A_rub / K_rapa.",
        "confirmed",
    ),
    (
        "glossary",
        "чистый процент от рапы",
        "Грязный минус расходы на откуп: Чистый % = Грязный % − "
        "F_откуп × 100. В USDT: Net_usdt = Gross_usdt − "
        "U_rapa_gross × F_откуп. Это реальный чистый профит команды "
        "со сделки.",
        "confirmed",
    ),
    (
        "glossary",
        "чистый спред",
        "Синоним чистого процента (полная маржа после откупа, с "
        "учётом вознаграждения эквайера). Не путать с «чистым "
        "спредом без награды» — по умолчанию в чате имеется в виду "
        "полный.",
        "confirmed",
    ),
    # ---- The canonical formula (corrected) ----------------------------------
    (
        "rule",
        None,
        "МОДЕЛЬ СДЕЛКИ RUB→USDT (канон, 2026-04-20). "
        "Входные: A_rub (рубли от клиента), K_deal (курс сделки), "
        "K_rapa (курс рапиры), F_откуп (единая потеря на стороне "
        "рапы, default 1%, обычно 1-1.5%), R_merch (вознаграждение "
        "эквайера в USDT, верхняя сумма на скрине). "
        "Расчёт: "
        "(1) U_client = A_rub / K_deal — отдали клиенту "
        "(нижняя сумма на скрине). "
        "(2) U_rapa_gross = A_rub / K_rapa — USDT с рапы по ставке без "
        "учёта откупа. "
        "(3) Spread_usdt = U_rapa_gross − U_client — разница курсов. "
        "(4) Grossy_usdt = Spread_usdt + R_merch — ГРЯЗНАЯ прибыль "
        "(с наградой, без откупа). "
        "(5) Откуп_usdt = U_rapa_gross × F_откуп — расходы на откупе. "
        "(6) Net_usdt = Grossy_usdt − Откуп_usdt — ЧИСТАЯ прибыль. "
        "(7) Net_pct = Net_usdt / U_client × 100 — маржа к объёму.",
        "confirmed",
    ),
    (
        "rule",
        None,
        "КОГДА СЧИТАТЬ СДЕЛКУ: юзер пишет «посчитай сделку / прибыль / "
        "грязный процент / чистый процент / доходность / сколько "
        "заработали». Default F_откуп = 1% если юзер не сказал иначе "
        "(«1.5% на откупе»). "
        "Если не хватает R_merch или A_rub — СПРОСИ («что на верхней "
        "сумме скрина?» / «какая сумма сделки?»), НЕ додумывай. "
        "Если юзер просит ТОЛЬКО «грязный/чистый процент» без цифр "
        "конкретной сделки — применяй формулу в %: "
        "Грязный % = (K_deal−K_rapa)/K_rapa × 100 + (R_merch/U_client) × 100, "
        "Чистый % = Грязный % − F_откуп × 100. "
        "Если R_merch / U_client не дал — ответь грязным ТОЛЬКО по курсам "
        "и напиши что «без награды эквайера; дай цифры — доклею».",
        "confirmed",
    ),
    (
        "rule",
        None,
        "ФОРМАТ ОТВЕТА по сделке — короткий, без длинных шагов. "
        "Пример: «Сделка 12 000 ₽ @ 82.54 → клиенту 145.4 USDT. "
        "С рапы @ 79.60 = 150.75 USDT gross. Спред +5.37 USDT, "
        "награда +7.26, итого грязными 12.63 USDT. Минус откуп 1% "
        "(1.51 USDT) → чистыми 11.12 USDT (7.7% к сделке)». "
        "Если юзер попросил ТОЛЬКО проценты — «Грязный от рапы: G%, "
        "чистый: N% (минус откуп F%)».",
        "confirmed",
    ),
]


def upgrade() -> None:
    # Deactivate old glossary entries by key.
    for k in _DEACTIVATE_KEYS:
        esc = k.replace("'", "''")
        op.execute(
            f"""
            UPDATE knowledge_base
            SET is_active = FALSE
            WHERE category = 'glossary'
              AND key = '{esc}'
              AND is_active = TRUE
            """
        )

    # Deactivate old mega-rules by content prefix.
    for prefix in _DEACTIVATE_RULE_PREFIXES:
        esc = prefix.replace("'", "''")
        op.execute(
            f"""
            UPDATE knowledge_base
            SET is_active = FALSE
            WHERE category = 'rule'
              AND content LIKE '{esc}%'
              AND is_active = TRUE
            """
        )

    # Seed new corrected facts.
    for cat, key, content, confidence in _NEW_FACTS:
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
    # Best-effort reverse: deactivate new facts, reactivate old ones.
    for cat, key, content, _ in _NEW_FACTS:
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
    # We don't try to restore the old content — the new content is the
    # corrected version. Downgrade just removes the new rows.
