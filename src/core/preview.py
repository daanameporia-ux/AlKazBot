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
    return f"{v:,.0f} ₽".replace(",", " ")


def _fmt_usdt(x) -> str:
    try:
        v = Decimal(str(x))
    except (InvalidOperation, TypeError):
        return str(x)
    return f"{v:,.2f} USDT".replace(",", " ")


def render(intent: str, fields: dict[str, Any], summary: str) -> str:
    intent_value = intent
    lines: list[str] = [f"<b>Записать?</b>  <i>{summary}</i>", ""]

    if intent_value == Intent.EXCHANGE.value:
        lines += [
            f"• Рубли:   {_fmt_rub(fields.get('amount_rub'))}",
            f"• USDT:    {_fmt_usdt(fields.get('amount_usdt'))}",
            f"• Курс:    {fields.get('fx_rate')} ₽/USDT",
        ]
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
    elif intent_value == Intent.CABINET_WORKED_OUT.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}")
    elif intent_value == Intent.CABINET_BLOCKED.value:
        lines.append(f"• Кабинет: {fields.get('name_or_code')}  →  blocked")
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
    else:
        lines.append(f"(fields: {fields})")

    return "\n".join(lines)
