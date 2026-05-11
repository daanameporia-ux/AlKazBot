"""Resync worker — on startup (or /resync command), walk unprocessed
messages from `message_log` and feed them into the batch analyzer as a
single fake batch per chat. Covers the gap when the bot was offline for
longer than the BatchBuffer's timer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from src.bot.batcher import Batch, BufferedMessage
from src.core.batch_processor import make_flush_handler
from src.db.models import MessageLog
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)

# Bumped 2 → 24 after 2026-04-22 outage where Anthropic API was down 43h
# and 41 of those hours of messages were lost because window was too short.
# 24h covers most realistic outages (Railway redeploy / API key issues /
# overnight gaps) while keeping the catch-up batch size manageable.
RESYNC_WINDOW_HOURS = 24
MIN_BATCH_SIZE = 2


async def resync(bot: Bot) -> dict[int, int]:
    """Scan recent unanalyzed messages, group by chat, feed each group
    as a synthetic batch to the analyzer. Returns {chat_id: size}.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=RESYNC_WINDOW_HOURS)
    async with session_scope() as session:
        # Voice transcripts have intent_detected='voice_transcript' set by
        # the voice middleware — they need to flow through the analyzer
        # too, otherwise voices from an outage window stay unparsed.
        # Live bug 2026-04-22: 78 voice transcripts in outage window were
        # excluded by the IS NULL filter alone.
        from sqlalchemy import or_

        res = await session.execute(
            select(MessageLog)
            .where(
                or_(
                    MessageLog.intent_detected.is_(None),
                    MessageLog.intent_detected == "voice_transcript",
                ),
                MessageLog.is_bot.is_(False),
                MessageLog.created_at >= cutoff,
                MessageLog.text.isnot(None),
            )
            .order_by(MessageLog.chat_id, MessageLog.created_at)
        )
        rows = list(res.scalars().all())

    by_chat: dict[int, list[MessageLog]] = {}
    for r in rows:
        by_chat.setdefault(r.chat_id, []).append(r)

    # ВАЖНО (08.05.2026): resync больше НЕ зовёт LLM analyze_batch.
    # Раньше делал — и при каждом старте контейнера Railway передеплой
    # триггерил resync → LLM видел 24ч-окно старых сообщений → плодил
    # preview-карточки на голую интерпретацию ("Обмен 157к → 2020 USDT"
    # из обрывка обсуждения в чате). См. инцидент 08.05 19:34 и 19:51.
    #
    # Теперь resync просто помечает messages.intent_detected='resync_seen'
    # чтобы они не попали в следующий resync-проход. Реальная обработка
    # пропущенных сообщений идёт только через явный пользовательский
    # триггер (mention/reply/keyword) или manual /resync с явным флагом.
    triggered: dict[int, int] = {}
    async with session_scope() as session:
        from sqlalchemy import update as _update
        for chat_id, msgs in by_chat.items():
            if len(msgs) < MIN_BATCH_SIZE:
                continue
            ids = [m.id for m in msgs]
            await session.execute(
                _update(MessageLog)
                .where(MessageLog.id.in_(ids))
                .where(MessageLog.intent_detected.is_(None))
                .values(intent_detected="resync_seen")
            )
            triggered[chat_id] = len(msgs)

    log.info("resync_marked", chats=len(triggered), messages=sum(triggered.values()))
    # Suppress unused-import warning — make_flush_handler/BufferedMessage/Batch
    # imports kept for /resync command which still wants real reprocess if asked.
    _ = (make_flush_handler, Batch, BufferedMessage, bot)
    return triggered
