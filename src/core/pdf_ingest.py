"""PDF → plain-text extraction, and a small heuristic to detect Sberbank
statements so the batch analyzer can be hinted.

Uses pdfminer.six which is pure-Python — no poppler/system deps needed on
Railway.
"""

from __future__ import annotations

import io

from pdfminer.high_level import extract_text

SBER_MARKERS = (
    "www.sberbank.ru",
    "СберБанк",
    "ОАО Сбербанк",
    "Выписка по платёжному счёту",
    "Расшифровка операций",
)


def extract_pdf_text(pdf_bytes: bytes, *, max_chars: int = 60_000) -> str:
    """Return extracted plain text, capped at `max_chars` to keep the LLM
    prompt from exploding. Real Sber statements cap out around 12-20k chars
    for a day; 60k gives us headroom for 2-3 days.
    """
    text = extract_text(io.BytesIO(pdf_bytes))
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]..."
    return text


def is_sber_statement(text: str) -> bool:
    t = text.lower()
    return any(marker.lower() in t for marker in SBER_MARKERS)


SBER_HINT = """\
Этот документ — банковская выписка Сбер по счёту процессинга. Формат:
строка с датой + код авторизации, строка с категорией + описанием
(источник платежа / банкомат / магазин), сумма (с «+» = пополнение
счёта / без знака = списание), остаток.

Как интерпретировать для бота:
  * «+N от ВТБ/Т-Банк/Озон/Альфа-Банк/Яндекс/Озон Банк» — поступление
    от клиента на sber_balances. Summarize как один wallet_snapshot
    на дату и текущий остаток — НЕ отдельные операции.
  * «Выдача наличных ATM …» — internal transfer sber_balances → cash
    (intent=expense с category='cash_withdrawal' пока что, пока у нас
    нет отдельного intent для internal transfers).
  * «Прочие расходы» мелкие (Магнит, Яндекс, Wildberries, SberVmeste,
    и т.п. меньше 5000₽) — личные расходы владельца карты, их
    игнорируй (не возвращай).
  * Крупные «Прочие расходы» (≥5000₽, если выглядит как комиссия
    платёжки или перевод контрагенту) — intent=expense с
    category='commission'/'other'.

Итоговый wallet_snapshot на дату выписки бери из строки «Остаток на
<дата>». Это самое важное поле.
"""
