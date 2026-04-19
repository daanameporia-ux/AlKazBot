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


EXPLICIT_INGEST_TOKENS = (
    "запиши", "запишу", "запиш", "внеси", "внести", "оформи", "оформить",
    "занеси", "занести", "посчитай как операции", "добавь как",
    "создай wallet_snapshot", "создать wallet_snapshot",
)


def has_explicit_ingest_request(user_text: str) -> bool:
    """True iff a user's message around the PDF asks us to parse into ops.

    Called by the analyzer prompt (through documentation) and optionally by
    documents.py if we want to reject upfront. Keep the token list short and
    concrete — if in doubt, the LLM should still ask, not guess.
    """
    if not user_text:
        return False
    lo = user_text.lower()
    return any(tok in lo for tok in EXPLICIT_INGEST_TOKENS)


SBER_HINT = """\
Этот документ — банковская выписка Сбер.

# ЖЁСТКОЕ ПРАВИЛО: ПО УМОЛЧАНИЮ operations=[]

Юзер прислал выписку для АНАЛИЗА, не для автозаписи. Default path:
  1. `operations=[]` — пустой список. ВСЕГДА. Не одна строка не
     превращается в preview-карточку без явного запроса юзера.
  2. `chat_reply` — короткая сводка (4-8 строк): что за выписка,
     диапазон дат, поступления/списания, остаток на конец, что-то
     заметное. Без воды.
  3. Текст выписки лежит в recent_history — отвечай на follow-up
     вопросы («сколько пришло от Т-Банка?», «средний чек?»).

# Разрешение на парсинг (ВСЕ три условия одновременно)

(a) В этой выписке или в СОСЕДНИХ сообщениях юзер явно сказал
    одно из: «запиши», «внеси», «оформи операции», «занеси в
    учёт», «посчитай как операции», «добавь как wallet_snapshot».
    Общие фразы «разбери», «посмотри», «что скажешь» — НЕ считаются.
(b) Выписка выглядит как счёт НАШЕЙ КОМАНДЫ / НАШЕГО КАБИНЕТА.
    Если в выписке чужие ФИО (не партнёры и не клиенты из KB) —
    это, скорее всего, ЧЕЙ-ТО ЧУЖОЙ счёт, не наш. Не записываем.
(c) Confidence ≥ 0.8. Если шаткое — положи вопрос в ambiguities.

Если хотя бы одно из (a)(b)(c) не выполнено — `operations=[]`,
`chat_reply` = короткая сводка + вопрос «это по нашему счёту? и
нужно ли занести в учёт?». Хозяин сам решит.

# Разметка строк (когда всё-таки парсим — все три условия ok)

  * «+N от ВТБ/Т-Банк/Озон/Альфа-Банк/Яндекс» — поступление клиента
    на sber_balances. Агрегируй в один wallet_snapshot с полем
    sber_balances = остаток на конец, не плодь отдельные операции.
  * «Выдача наличных ATM …» — internal transfer sber_balances → cash
    (сейчас expense category='cash_withdrawal').
  * Мелкие «Прочие расходы» (<5000₽, личные покупки: Магнит, Яндекс,
    Wildberries, SberVmeste) — игнорируй всегда.
  * Крупные «Прочие расходы» (≥5000₽, похожие на комиссии/переводы
    контрагенту) — expense category='commission'/'other'.
  * «Остаток на <дата>» — итоговое поле sber_balances для
    wallet_snapshot.
"""


ALIEN_PDF_HINT = """\
Этот PDF — НЕ выписка Сбера (или не похож на неё). Вероятно чужой
банк / договор / чек / произвольный документ.

ЖЁСТКО: `operations=[]` в 99% случаев. Мы не парсим чужие банки /
случайные документы в учёт.

В `chat_reply` — одна-две фразы: что это за документ (если ясно
из текста), и краткий итог, если есть цифры. Предложи вариант:
«если надо что-то из этого занести в учёт — скажи конкретно,
какую операцию и куда».

Не проси файл «прислать заново» — у тебя он уже есть в recent_history.
"""
