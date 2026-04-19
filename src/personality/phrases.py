"""Reusable reply templates. Keep here only *structural* phrases (confirm / deny
/ help text); free-form responses should come from the LLM to stay in-character.
"""

from __future__ import annotations

HELP_TEXT = """\
<b>Команды:</b>

<b>Учёт</b>
/report — вечерний отчёт
/balance [код] — снапшот кошельков (или одного)
/stock — кабинеты на складе
/fx — текущий курс RUB→USDT
/partners — доли партнёров
/history [N] — последние N операций
/undo [id] — откатить операцию (создатель / owner)

<b>Клиенты</b>
/clients — все клиенты
/client [имя] — история + долг
/debts — все открытые долги

<b>Обучение</b>
/knowledge — что я запомнил
/knowledge add|forget|edit|search
/feedback — пожелания команды
/feedback add [текст]

<b>Служебное</b>
/keywords — trigger-слова (owner add/remove)
/silent on|off — замолкнуть / включиться (owner)
/voices — нетранскрибированные голосовые
/resync — переобработать пропущенные (owner)
/avatar — сменить аватарку (reply фото, owner)
/chatid — id этого чата
/help — эта справка

Или просто @Al_Kazbot текстом / голосом — я пойму.
"""

NOT_WHITELISTED = "Ты не в списке. Если надо — попроси добавить тебя через owner."

BOT_ERROR_FALLBACK = "Щас не соображаю, попробуй через минуту."
