"""Glue between BatchBuffer.flush → LLM analyzer → preview cards with buttons.

This is registered as the BatchBuffer's flush_handler in main.py. For each
`BatchOperation` returned by the analyzer we:

  1. Register a PendingOp in the in-memory registry.
  2. Send a preview card to the chat with ✅ / ❌ inline buttons.
  3. Store the preview's message_id in the registry.

Callbacks in `src/bot/handlers/callbacks.py` handle confirm/cancel.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.batcher import Batch
from src.bot.middlewares.logging import log_bot_reply
from src.core import pending_ops, silent
from src.core.preview import render_op_card
from src.db.repositories import knowledge as kb_repo
from src.db.session import session_scope
from src.llm.batch_analyzer import analyze_batch
from src.logging_setup import get_logger

log = get_logger(__name__)


def _confirm_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Записать", callback_data=f"confirm:{uid}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"cancel:{uid}"),
            ]
        ]
    )


# Public alias for other handlers that need the same keyboard style.
confirm_keyboard = _confirm_kb


async def _load_kb_items() -> list[dict]:
    async with session_scope() as session:
        facts = await kb_repo.list_facts(session, min_confidence="inferred")
    return [
        {
            "id": f.id,
            "category": f.category,
            "key": f.key,
            "content": f.content,
            "confidence": f.confidence,
        }
        for f in facts
    ]


def make_flush_handler(bot: Bot):
    async def flush(batch: Batch) -> None:
        # Skip empty flushes (timer fired on empty buffer)
        if not batch.messages and batch.trigger is None:
            return

        if silent.is_silent():
            log.info("batch_flush_silenced", chat_id=batch.chat_id)
            return

        kb = await _load_kb_items()
        try:
            analysis = await analyze_batch(batch, knowledge_items=kb)
        except Exception:
            log.exception("batch_analyze_failed", chat_id=batch.chat_id)
            return

        # If it's chat-only: reply only when the batch had a direct trigger
        # (mention/reply/command) AND the analyzer produced a chat_reply.
        # Passive batches stay silent.
        if analysis.chat_only or not analysis.operations:
            log.info(
                "batch_chat_only",
                chat_id=batch.chat_id,
                msgs=len(batch.messages),
                has_reply=bool(analysis.chat_reply),
                had_trigger=batch.trigger is not None,
            )
            if batch.trigger is not None and analysis.chat_reply:
                try:
                    sent = await bot.send_message(
                        chat_id=batch.chat_id,
                        text=analysis.chat_reply,
                        reply_to_message_id=batch.trigger.tg_message_id,
                    )
                    await log_bot_reply(
                        chat_id=batch.chat_id,
                        tg_message_id=sent.message_id,
                        text=analysis.chat_reply,
                        intent_hint="chat_reply",
                    )
                except Exception:
                    log.exception("chat_reply_send_failed")
            return

        created_by = (
            batch.trigger.tg_user_id
            if batch.trigger
            else (batch.messages[-1].tg_user_id if batch.messages else 0)
        )

        for op in analysis.operations:
            entry = await pending_ops.register(
                chat_id=batch.chat_id,
                intent=op.intent.value,
                fields=op.fields,
                summary=op.summary,
                source_message_ids=op.source_message_ids,
                created_by_tg_id=created_by,
            )
            preview_text = render_op_card(
                intent=op.intent.value,
                fields=op.fields,
                summary=op.summary,
                confidence=op.confidence,
                ambiguities=op.ambiguities,
            )
            try:
                sent = await bot.send_message(
                    chat_id=batch.chat_id,
                    text=preview_text,
                    reply_markup=_confirm_kb(entry.uid),
                )
                await pending_ops.attach_preview(entry.uid, sent.message_id)
                await log_bot_reply(
                    chat_id=batch.chat_id,
                    tg_message_id=sent.message_id,
                    text=f"[preview {op.intent.value}] {op.summary}",
                    intent_hint=op.intent.value,
                )
            except Exception:
                log.exception("preview_send_failed", intent=op.intent.value)

    return flush
