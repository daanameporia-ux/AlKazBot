"""Reusable reply templates. Keep here only *structural* phrases (confirm / deny
/ help text); free-form responses should come from the LLM to stay in-character.
"""

from __future__ import annotations

HELP_TEXT = """\
Чё умею:

*Общее*
/report          — собрать вечерний отчёт (пошагово)
/balance         — быстрый снапшот без записи отчёта
/balance <код>   — остаток одного кошелька
/stock           — что на складе (кабинеты)
/debts           — кому должны / кто должен
/history [N]     — последние N операций
/fx              — текущий курс RUB→USDT
/partners        — текущие доли партнёров

*Клиенты доверенностей*
/clients         — список клиентов
/client <имя>    — история по клиенту

*Обучаемость*
/knowledge       — что я знаю (разбито по категориям)
/knowledge add <факт>
/knowledge forget <id>
/feedback        — пожелания команды что я накопил

*Служебное*
/undo <id>       — откатить операцию (создатель / owner)
/silent on|off   — заткнуться / снова говорить
/chatid          — показать id этого чата
/help            — эту справку

Или просто пиши @бот <что-то по делу>, разберусь.
"""

NOT_WHITELISTED = "Ты не в списке. Если надо — попроси добавить тебя через owner."

BOT_ERROR_FALLBACK = "Щас не соображаю, попробуй через минуту."
