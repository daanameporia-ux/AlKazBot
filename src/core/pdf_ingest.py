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
Этот документ — банковская выписка Сбер по счёту процессинга.

ВАЖНО — ПО УМОЛЧАНИЮ НЕ СОЗДАВАЙ ОПЕРАЦИИ из строк выписки.
Юзер прислал выписку для АНАЛИЗА, не для автозаписи. Твоя задача:
  1. Верни `operations=[]` (пустой список).
  2. В `chat_reply` дай краткую сводку: что за выписка, диапазон
     дат, сколько всего поступлений/списаний, итоговый остаток,
     что-то подозрительное/заметное. 4-8 строк, в стиле: факты без
     воды.
  3. Оставь себе в голове (доступно через recent_history) весь
     текст выписки, чтобы отвечать на follow-up вопросы юзера
     («сколько пришло от Т-Банка?», «какие расходы?», «посчитай
     средний чек»).

КОГДА ВСЁ ЖЕ ПАРСИТЬ В ОПЕРАЦИИ?
Только если юзер ЯВНО попросил в том же сообщении или следующим
триггером: «запиши», «внеси», «оформи операции», «занеси в учёт»,
«посчитай как операции», «добавь как wallet_snapshot», и т.п.
Если такого не сказано — не надо. Просто отвечай текстом.

Формат выписки (для справки когда юзер будет задавать вопросы):
  * Строка с датой + код авторизации → идентификатор транзакции
  * Строка с категорией + описанием (источник/банкомат/магазин)
  * Сумма: «+» = пополнение, без знака = списание
  * Остаток — бегущий баланс счёта

Когда юзер всё же попросит внести — используй эту разметку:
  * «+N от ВТБ/Т-Банк/Озон/Альфа-Банк/Яндекс» — поступление клиента
    на sber_balances. Агрегируй в один wallet_snapshot с полем
    sber_balances = остаток на конец, не плодь отдельные операции.
  * «Выдача наличных ATM …» — internal transfer sber_balances → cash
    (сейчас expense category='cash_withdrawal', пока нет отдельного
    intent для internal transfers).
  * Мелкие «Прочие расходы» (<5000₽, личные покупки: Магнит, Яндекс,
    Wildberries, SberVmeste) — игнорируй.
  * Крупные «Прочие расходы» (≥5000₽, похожие на комиссии/переводы
    контрагенту) — expense category='commission'/'other'.
  * «Остаток на <дата>» — итоговое поле sber_balances для
    wallet_snapshot.
"""
