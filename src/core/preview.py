"""Human-friendly preview text for each operation intent.

Each preview renders the operation as it will be persisted so the user can
verify before pressing ✅. Keep strings short — Telegram shows the card
inline in chat.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from src.llm.schemas import Intent


def _fmt_rub(x) -> str:
    try:
        v = Decimal(str(x))
    except (InvalidOperation, TypeError):
        return str(x)
    # KB preference: round RUB to 100₽ for values >= 10_000, 1₽ for smaller.
    abs_v = abs(v)
    if abs_v >= Decimal("10000"):
        rounded = (v / Decimal("100")).to_integral_value() * Decimal("100")
        return f"{rounded:,.0f} ₽".replace(",", " ")
    return f"{v:,.0f} ₽".replace(",", " ")


def _fmt_usdt(x) -> str:
    try:
        v = Decimal(str(x))
    except (InvalidOperation, TypeError):
        return str(x)
    # KB preference: USDT round to $1 in previews/reports.
    return f"{v:,.0f}$".replace(",", " ")


CONFIDENCE_ACCEPT_THRESHOLD = 0.7


def render_op_card(
    *,
    intent: str,
    fields: dict[str, Any],
    summary: str,
    confidence: float = 1.0,
    ambiguities: list[str] | None = None,
) -> str:
    """Full preview text for a candidate operation — body + confidence
    footer + ambiguities footer. Shared between batch_processor (group
    chat) and photo.py (vision).
    """
    text = render(intent, fields, summary)
    if confidence < CONFIDENCE_ACCEPT_THRESHOLD:
        text += f"\n\n<i>Confidence {confidence:.2f} — перепроверь.</i>"
    if ambiguities:
        text += "\n\n<i>Сомнения:</i>\n" + "\n".join(f"• {a}" for a in ambiguities)
    return text


def render(intent: str, fields: dict[str, Any], summary: str) -> str:
    intent_value = intent
    lines: list[str] = [f"<b>Записать?</b>  <i>{summary}</i>", ""]

    if intent_value == Intent.EXCHANGE.value:
        rub = _fmt_rub(fields.get("amount_rub"))
        usdt = _fmt_usdt(fields.get("amount_usdt"))
        rate = fields.get("fx_rate")
        lines.append(f"• {rub}  @ {rate} ₽/USDT  =  {usdt}")
    elif intent_value == Intent.EXPENSE.value:
        lines += [
            f"• Категория: {fields.get('category', '?')}",
            f"• Сумма:     {_fmt_rub(fields.get('amount_rub')) if fields.get('amount_rub') else _fmt_usdt(fields.get('amount_usdt'))}",
            f"• Описание:  {fields.get('description') or '—'}",
        ]
    elif intent_value == Intent.PARTNER_DEPOSIT.value:
        lines += [
            f"• Партнёр: {fields.get('partner')}",
            f"• Сумма:   {_fmt_usdt(fields.get('amount_usdt'))}",
        ]
    elif intent_value == Intent.PARTNER_WITHDRAWAL.value:
        lines += [
            f"• Партнёр:    {fields.get('partner')}",
            f"• Вывел:      {_fmt_usdt(fields.get('amount_usdt'))}",
            f"• Из кошелька: {fields.get('from_wallet') or '—'}",
        ]
    elif intent_value == Intent.WALLET_SNAPSHOT.value:
        wallets = ("tapbank", "mercurio", "rapira", "sber_balances", "cash")
        for w in wallets:
            if w in fields:
                lines.append(f"• {w:<14} = {fields[w]}")
    elif intent_value == Intent.POA_WITHDRAWAL.value:
        lines += [
            f"• Клиент:        {fields.get('client_name')}",
            f"• Сумма снятия:  {_fmt_rub(fields.get('amount_rub'))}",
            f"• Доля клиента:  {fields.get('client_share_pct')}%",
        ]
        shares = fields.get("partner_shares") or []
        if shares:
            lines.append("• Доли партнёров:")
            for s in shares:
                lines.append(f"    - {s.get('partner')}: {s.get('pct')}%")
    elif intent_value == Intent.CABINET_PURCHASE.value:
        lines += [
            f"• Имя:     {fields.get('name') or '(без имени, сгенерю Cab-NNN)'}",
            f"• Цена:    {_fmt_rub(fields.get('cost_rub'))}",
            f"• Против предоплаты: {fields.get('prepayment_ref') or 'нет'}",
        ]
    elif intent_value == Intent.CABINET_IN_USE.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}  →  в работу (in_use)")
    elif intent_value == Intent.CABINET_WORKED_OUT.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}  →  отработан")
    elif intent_value == Intent.CABINET_BLOCKED.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}  →  blocked")
    elif intent_value == Intent.CABINET_RECOVERED.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}  →  recovered")
    elif intent_value == Intent.PREPAYMENT_GIVEN.value:
        lines += [
            f"• Поставщик: {fields.get('supplier')}",
            f"• Сумма:     {_fmt_rub(fields.get('amount_rub'))}",
            f"• Ждём кабинетов: {fields.get('expected_cabinets') or '?'}",
        ]
    elif intent_value == Intent.PREPAYMENT_FULFILLED.value:
        lines += [f"• Поставщик: {fields.get('supplier')}"]
        cabs = fields.get("cabinets") or []
        for c in cabs:
            lines.append(f"    - {c.get('name') or '?'}: {_fmt_rub(c.get('cost_rub'))}")
    elif intent_value == Intent.CLIENT_PAYOUT.value:
        lines += [
            f"• Клиент:   {fields.get('client_name')}",
            f"• Отдали:   {_fmt_usdt(fields.get('amount_usdt'))}",
        ]
    elif intent_value == Intent.KNOWLEDGE_TEACH.value:
        lines += [
            f"• Категория: <b>{fields.get('category', 'rule')}</b>",
        ]
        if fields.get("key"):
            lines.append(f"• Ключ:      {fields.get('key')}")
        lines.append(f"• Текст:     {fields.get('content', '')}")
    elif intent_value == Intent.WAKEWORD_ADD.value:
        lines += [
            f"• Триггер-слово: <b>{fields.get('word', '?')}</b>",
            "• Будет добавлено и в trigger_keywords, и в KB как preference.",
        ]
    else:
        lines.append(f"(fields: {fields})")

    return "\n".join(lines)
