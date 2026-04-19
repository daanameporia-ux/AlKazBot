"""Filter: is this message actually addressed to the bot?

Matches only if:
  * text / caption contains `@<bot_username>`, OR
  * it's a Telegram reply to one of the bot's own messages, OR
  * it's a Bot-API-7.0 external_reply whose origin is the bot.

Pure boolean — if false, dispatcher moves on to later routers so the
keyword gate in `handlers/messages` gets a chance.
"""

from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message


class AddressedToMe(Filter):
    async def __call__(self, message: Message) -> bool:  # type: ignore[override]
        me = await message.bot.me()
        text = (message.text or message.caption or "").lower()
        if me.username and f"@{me.username}".lower() in text:
            return True

        rpy = message.reply_to_message
        if rpy and rpy.from_user and rpy.from_user.id == me.id:
            return True

        ext = getattr(message, "external_reply", None)
        if ext is not None:
            origin = getattr(ext, "origin", None)
            sender = getattr(origin, "sender_user", None) if origin else None
            if sender is not None and sender.id == me.id:
                return True
        return False
