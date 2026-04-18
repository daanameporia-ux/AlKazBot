"""Voice-note intake — store OGG bytes in Postgres for later transcription.

Real transcription is done out-of-band in a Claude Code session
(scripts/transcribe_voices.py) — we just capture here.
"""

from __future__ import annotations

import io

from aiogram import F, Router
from aiogram.types import Message

from src.config import settings
from src.db.repositories import voice as voice_repo
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="voice")

MAX_VOICE_BYTES = 4_000_000  # 4 MB — one Telegram voice capped ~2 min


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


@router.message(F.voice)
async def on_voice(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    voice = message.voice
    if voice is None:
        return
    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await message.reply(
            f"Голосовое слишком длинное ({voice.file_size // 1024} КБ). "
            "Разбей на 1-2-минутные куски."
        )
        return

    try:
        buf = io.BytesIO()
        await message.bot.download(voice, destination=buf)
    except Exception:
        log.exception("voice_download_failed")
        return

    data = buf.getvalue()
    async with session_scope() as session:
        v = await voice_repo.store(
            session,
            tg_message_id=message.message_id,
            tg_user_id=message.from_user.id,
            chat_id=message.chat.id,
            duration_sec=voice.duration,
            mime_type=voice.mime_type,
            ogg_data=data,
        )
    log.info(
        "voice_stored",
        voice_id=v.id,
        duration=voice.duration,
        size=len(data),
        user=message.from_user.id,
    )
    # No auto-reply — the whole point is to stash and transcribe later.
