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

    flush_handler = make_flush_handler(bot)
    triggered: dict[int, int] = {}
    for chat_id, msgs in by_chat.items():
        if len(msgs) < MIN_BATCH_SIZE:
            continue
        batch = Batch(
            chat_id=chat_id,
            messages=[
                BufferedMessage(
                    tg_message_id=m.tg_message_id or 0,
                    tg_user_id=m.tg_user_id or 0,
                    display_name=None,
                    text=m.text or "",
                    received_at=m.created_at.timestamp(),
                )
                for m in msgs
            ],
            trigger=None,
            trigger_kind="resync",
        )
        try:
            await flush_handler(batch)
            triggered[chat_id] = len(msgs)
        except Exception:
            log.exception("resync_flush_failed", chat_id=chat_id)

    log.info("resync_done", chats=len(triggered), messages=sum(triggered.values()))
    return triggered
