"""Voice message storage + transcription lookup."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import VoiceMessage


async def store(
    session: AsyncSession,
    *,
    tg_message_id: int,
    tg_user_id: int,
    chat_id: int,
    duration_sec: int | None,
    mime_type: str | None,
    ogg_data: bytes,
) -> VoiceMessage:
    # Dedup by tg_message_id within chat.
    res = await session.execute(
        select(VoiceMessage).where(
            VoiceMessage.tg_message_id == tg_message_id,
            VoiceMessage.chat_id == chat_id,
        )
    )
    existing = res.scalar_one_or_none()
    if existing is not None:
        return existing
    v = VoiceMessage(
        tg_message_id=tg_message_id,
        tg_user_id=tg_user_id,
        chat_id=chat_id,
        duration_sec=duration_sec,
        mime_type=mime_type,
        file_size_bytes=len(ogg_data),
        ogg_data=ogg_data,
    )
    session.add(v)
    await session.flush()
    return v


async def count_pending(session: AsyncSession) -> int:
    res = await session.execute(
        select(func.count(VoiceMessage.id)).where(
            VoiceMessage.transcribed_text.is_(None)
        )
    )
    return int(res.scalar_one() or 0)


async def list_pending(
    session: AsyncSession, *, limit: int = 50
) -> list[VoiceMessage]:
    res = await session.execute(
        select(VoiceMessage)
        .where(VoiceMessage.transcribed_text.is_(None))
        .order_by(VoiceMessage.created_at)
        .limit(limit)
    )
    return list(res.scalars().all())


async def set_transcription(
    session: AsyncSession, voice_id: int, text: str
) -> None:
    await session.execute(
        update(VoiceMessage)
        .where(VoiceMessage.id == voice_id)
        .values(transcribed_text=text, transcribed_at=datetime.now(UTC))
    )


async def mark_analyzed(session: AsyncSession, voice_id: int) -> None:
    await session.execute(
        update(VoiceMessage)
        .where(VoiceMessage.id == voice_id)
        .values(analyzed=True)
    )


async def list_transcribed_unanalyzed(
    session: AsyncSession, *, limit: int = 50
) -> list[VoiceMessage]:
    res = await session.execute(
        select(VoiceMessage)
        .where(
            VoiceMessage.transcribed_text.isnot(None),
            VoiceMessage.analyzed.is_(False),
        )
        .order_by(VoiceMessage.created_at)
        .limit(limit)
    )
    return list(res.scalars().all())
