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
from src.core.pending_ops import PendingOp, get_registry
from src.core.preview import render as render_preview
from src.db.repositories import knowledge as kb_repo
from src.db.session import session_scope
from src.llm.batch_analyzer import analyze_batch
from src.logging_setup import get_logger

log = get_logger(__name__)

CONFIDENCE_ACCEPT_THRESHOLD = 0.7


def _confirm_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Записать", callback_data=f"confirm:{uid}"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"cancel:{uid}"),
            ]
        ]
    )


def _ambiguities_footer(op) -> str:
    if not op.ambiguities:
        return ""
    bullets = "\n".join(f"• {a}" for a in op.ambiguities)
    return f"\n\n<i>Сомнения:</i>\n{bullets}"


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

        kb = await _load_kb_items()
        try:
            analysis = await analyze_batch(batch, knowledge_items=kb)
        except Exception:
            log.exception("batch_analyze_failed", chat_id=batch.chat_id)
            return

        if analysis.chat_only or not analysis.operations:
            log.info(
                "batch_chat_only",
                chat_id=batch.chat_id,
                msgs=len(batch.messages),
            )
            return

        registry = get_registry()
        created_by = (
            batch.trigger.tg_user_id
            if batch.trigger
            else (batch.messages[-1].tg_user_id if batch.messages else 0)
        )

        for op in analysis.operations:
            entry: PendingOp = await registry.register(
                chat_id=batch.chat_id,
                intent=op.intent.value,
                fields=op.fields,
                summary=op.summary,
                source_message_ids=op.source_message_ids,
                created_by_tg_id=created_by,
            )
            preview_text = render_preview(op.intent.value, op.fields, op.summary)
            if op.confidence < CONFIDENCE_ACCEPT_THRESHOLD:
                preview_text += (
                    f"\n\n<i>Confidence {op.confidence:.2f} — перепроверь.</i>"
                )
            preview_text += _ambiguities_footer(op)
            try:
                sent = await bot.send_message(
                    chat_id=batch.chat_id,
                    text=preview_text,
                    reply_markup=_confirm_kb(entry.uid),
                )
                await registry.attach_preview(entry.uid, sent.message_id)
            except Exception:
                log.exception("preview_send_failed", intent=op.intent.value)

    return flush
