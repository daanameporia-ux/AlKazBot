"""Slash commands. Stage 0 ships /start /help /chatid; the rest are stubs that
point users to the right flow while warning they aren't implemented yet.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.logging_setup import get_logger
from src.personality.phrases import HELP_TEXT
from src.personality.voice import GREETING_FIRST_RUN

log = get_logger(__name__)
router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(GREETING_FIRST_RUN)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    """Handy for the SETUP flow — helps the user grab MAIN_CHAT_ID."""
    await message.answer(
        f"chat_id = `{message.chat.id}`\n"
        f"your user_id = `{message.from_user.id if message.from_user else '?'}`",
        parse_mode="Markdown",
    )


# --------------------------------------------------------------------------- #
# Stubs — visible in /help, reply "скоро будет" so users know work is queued.
# Populated on Stages 1-3.
# --------------------------------------------------------------------------- #

STAGE1_COMMANDS = ("report", "balance", "fx", "partners", "knowledge", "feedback")
STAGE2_COMMANDS = ("stock", "clients", "client", "debts", "history", "undo")
STAGE3_COMMANDS = ("silent",)


@router.message(Command(*STAGE1_COMMANDS, *STAGE2_COMMANDS, *STAGE3_COMMANDS))
async def cmd_stub(message: Message) -> None:
    cmd = (message.text or "").split()[0] if message.text else "?"
    await message.reply(
        f"{cmd} — пока не реализовано. Я только что родился на Этапе 0, "
        f"бизнес-логика приезжает следующими коммитами. /help — что уже умею."
    )


__all__ = ["router"]

# Silence unused-import lint (`F` reserved for upcoming filters).
_ = F
