"""Voice-note intake.

Stores the OGG bytes, then kicks off a background task that transcribes
locally via whisper. After transcription:
  * the text goes into `message_log` as context,
  * a local keyword matcher scans it — if any trigger keyword is hit,
    fires the batch analyzer with the voice as a trigger (LLM decides
    if it's actually addressed to the bot or a false positive);
  * otherwise the bot stays silent and the transcript just sits in
    recent history.

Transcription is whisper locally — zero Anthropic API tokens spent on
it. The LLM only runs when keywords hit, same budget discipline as for
text messages.
"""

from __future__ import annotations

import asyncio
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
        voice_id = v.id
    log.info(
        "voice_stored",
        voice_id=voice_id,
        duration=voice.duration,
        size=len(data),
        user=message.from_user.id,
    )

    # Kick off background transcription + keyword check. Whisper runs
    # locally, no Anthropic tokens. LLM only fires if a trigger keyword
    # shows up in the transcript.
    from src.core.voice_trigger import transcribe_and_keyword_check

    task = asyncio.create_task(
        transcribe_and_keyword_check(message.bot, voice_id),
        name=f"voice-kw-{voice_id}",
    )
    _pending_voice_tasks.add(task)
    task.add_done_callback(_pending_voice_tasks.discard)


_pending_voice_tasks: set[asyncio.Task] = set()
