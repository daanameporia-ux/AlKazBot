"""Sticker observer — logs every sticker and expands its pack.

When a user sends a sticker, we:
  1. Log the sticker itself in `seen_stickers`.
  2. If the sticker belongs to a pack (`set_name`), pull the whole pack
     via `bot.get_sticker_set(set_name)` and log each entry too. This
     means after one "лайк" sticker from a pack, the bot knows all the
     other members of that pack and can pick contextually.

The middleware that persists general messages already records the raw
message; this handler is additive.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from src.bot.batcher import is_main_group
from src.config import settings
from src.db.repositories import stickers as sticker_repo
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="stickers")


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


@router.message(F.sticker)
async def on_sticker(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    # Only harvest from the main group (where the team's taste lives).
    if not is_main_group(message.chat.id):
        return

    st = message.sticker
    if st is None:
        return

    async with session_scope() as session:
        await sticker_repo.upsert(
            session,
            file_id=st.file_id,
            file_unique_id=st.file_unique_id,
            sticker_set=st.set_name,
            emoji=st.emoji,
            is_animated=st.is_animated,
            is_video=st.is_video,
        )
        # Expand the entire pack — one roundtrip to Telegram per unique set.
        if st.set_name:
            try:
                pack = await message.bot.get_sticker_set(st.set_name)
            except Exception:
                log.exception("sticker_set_fetch_failed", set_name=st.set_name)
                pack = None
            if pack is not None:
                for item in pack.stickers:
                    await sticker_repo.upsert(
                        session,
                        file_id=item.file_id,
                        file_unique_id=item.file_unique_id,
                        sticker_set=st.set_name,
                        emoji=item.emoji,
                        is_animated=item.is_animated,
                        is_video=item.is_video,
                    )
    log.info(
        "sticker_captured",
        set_name=st.set_name,
        emoji=st.emoji,
        pack_size=len(pack.stickers) if st.set_name and pack else 1,
    )
