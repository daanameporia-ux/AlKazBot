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
from src.db.repositories import stickers as sticker_repo
from src.db.session import session_scope
from src.llm.batch_analyzer import analyze_batch
from src.logging_setup import get_logger

log = get_logger(__name__)


async def _maybe_send_sticker(
    bot: Bot,
    *,
    chat_id: int,
    reply_to: int | None,
    emoji: str | None,
    description_hint: str | None = None,
    pack_hint: str | None = None,
    theme_hint: str | None = None,
) -> None:
    """If Claude set any of sticker_emoji / _description_hint / _pack_hint /
    _theme_hint, resolve to a real sticker from `seen_stickers` and send
    it. Silent no-op when all are empty or no match — `chat_reply` (if
    any) still went through separately.

    Resolution cascade (each next step relaxes one filter):
      1. all filters (intersection);
      2. drop `description_hint` + `pack_hint`, keep emoji + theme;
      3. description_hint alone;
      4. theme alone;
      5. give up silently.
    This stops the "asked for Sber, got Blizzard" class of miss: theme
    pins the pack, description narrows within it, emoji can be a tie-
    breaker but doesn't drive the choice.
    """
    if not emoji and not description_hint and not pack_hint and not theme_hint:
        return
    async with session_scope() as session:
        chosen = await sticker_repo.pick_smart(
            session,
            emoji=emoji,
            description_hint=description_hint,
            pack_hint=pack_hint,
            theme_hint=theme_hint,
        )
        # Fallback 1: keep emoji + theme, drop description/pack text.
        if chosen is None and (description_hint or pack_hint):
            chosen = await sticker_repo.pick_smart(
                session,
                emoji=emoji,
                theme_hint=theme_hint,
            )
        # Fallback 2: description alone (when emoji was the red herring).
        if chosen is None and description_hint:
            chosen = await sticker_repo.pick_smart(
                session,
                description_hint=description_hint,
            )
        # Fallback 3: theme alone.
        if chosen is None and theme_hint:
            chosen = await sticker_repo.pick_smart(
                session,
                theme_hint=theme_hint,
            )
        if chosen is None:
            log.info(
                "sticker_pick_no_match",
                emoji=emoji,
                description_hint=description_hint,
                pack_hint=pack_hint,
                theme_hint=theme_hint,
            )
            return
        try:
            sent = await bot.send_sticker(
                chat_id=chat_id,
                sticker=chosen.file_id,
                reply_to_message_id=reply_to,
            )
        except Exception:
            log.exception("sticker_send_failed", emoji=emoji)
            return
        await sticker_repo.bump_usage(session, chosen.id)
        await sticker_repo.log_usage(
            session,
            sticker_file_unique_id=chosen.file_unique_id,
            sticker_set=chosen.sticker_set,
            emoji=chosen.emoji,
            tg_user_id=None,
            chat_id=chat_id,
            tg_message_id=sent.message_id,
            preceding_text=None,
            sent_by_bot=True,
        )
        # Explicit log format: distinguish a real description (Vision-
        # generated, meaningful) from just-pack-fallback. If the bot
        # glances at recent_history and sees only a pack name, it must
        # know "i don't know what's on this sticker" rather than invent
        # a description from the pack name alone.
        if chosen.description:
            sticker_marker = f"[sticker {chosen.emoji or '?'} · {chosen.description}]"
        else:
            pack = chosen.sticker_set or "?"
            sticker_marker = (
                f"[sticker {chosen.emoji or '?'} · pack={pack}, описания нет]"
            )
        await log_bot_reply(
            chat_id=chat_id,
            tg_message_id=sent.message_id,
            text=sticker_marker[:220],
            intent_hint="sticker_reply",
        )
        log.info(
            "sticker_sent",
            emoji=chosen.emoji,
            pack=chosen.sticker_set,
            description_preview=(chosen.description or "")[:60],
            file_unique_id=chosen.file_unique_id,
        )


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
    """Load all active KB facts above `inferred` confidence.

    Tiered loading (since 2026-05-03):
      • kernel set (`always_inject=true`): canonical rules / aliases — pulled
        every call, lives in the cached system block.
      • остальное: лежит в БД, грузится lazy через `lookup_for_text` в
        uncached хвост — только когда триггер из батча реально совпал.

    Это срезало cached system prompt с ~25k до ~5-7k tokens.
    """
    async with session_scope() as session:
        facts = await kb_repo.list_facts(
            session, min_confidence="inferred", only_kernel=True
        )
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
            analysis = await analyze_batch(batch, knowledge_items=kb, bot=bot)
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
            # Sticker reaction — fires whether or not chat_reply landed.
            # Attaches to the trigger message so the reply-thread stays
            # anchored on what the user said.
            try:
                await _maybe_send_sticker(
                    bot,
                    chat_id=batch.chat_id,
                    reply_to=(
                        batch.trigger.tg_message_id if batch.trigger else None
                    ),
                    emoji=analysis.sticker_emoji,
                    description_hint=analysis.sticker_description_hint,
                    pack_hint=analysis.sticker_pack_hint,
                    theme_hint=analysis.sticker_theme_hint,
                )
            except Exception:
                log.exception("sticker_emoji_handling_failed")
            return

        created_by = (
            batch.trigger.tg_user_id
            if batch.trigger
            else (batch.messages[-1].tg_user_id if batch.messages else 0)
        )

        for op in analysis.operations:
            # Dedup: if the same operation was already proposed in the
            # last 2 min and is still pending, skip creating a duplicate
            # card. Same user rephrasing the same thing → one card, not
            # three. The existing card's buttons still work.
            dup = await pending_ops.find_duplicate(
                chat_id=batch.chat_id,
                intent=op.intent.value,
                fields=op.fields,
            )
            if dup is not None:
                log.info(
                    "preview_deduped",
                    intent=op.intent.value,
                    existing_uid=dup.uid,
                )
                continue

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
