"""Callback-query handlers for confirm / cancel of pending operations."""

from __future__ import annotations

import contextlib
import random

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.core import pending_ops
from src.core.applier import ApplyError, apply
from src.db.repositories import stickers as sticker_repo
from src.db.session import session_scope
from src.logging_setup import get_logger

# Emoji pools for different bot reactions.
POSITIVE_EMOJIS = ["✅", "👍", "🔥", "😎", "💪", "🫡", "🎯", "💯"]
NEGATIVE_EMOJIS = ["❌", "🙅", "😒", "🙄", "🤷"]


async def _maybe_sticker(
    q: CallbackQuery, emojis: list[str], chance: float = 0.35
) -> None:
    """With `chance` probability, reply with a sticker whose emoji matches."""
    if random.random() >= chance:
        return
    async with session_scope() as session:
        st = await sticker_repo.pick_by_emoji(session, emojis)
        if st is None:
            return
        await sticker_repo.bump_usage(session, st.id)
    with contextlib.suppress(Exception):
        await q.message.bot.send_sticker(chat_id=q.message.chat.id, sticker=st.file_id)

log = get_logger(__name__)
router = Router(name="callbacks")


def _can_act_on(op, tg_user_id: int) -> bool:
    """Only the operation's creator or the owner can confirm/cancel.

    Why: callback_data travels openly in UI. Without this, anyone who sees
    a card (or guesses a uid) could fire someone else's pending op. The
    owner can always step in — useful when Казах reviews Арбуз's ops.
    """
    from src.config import settings

    return (
        tg_user_id == op.created_by_tg_id
        or tg_user_id == settings.owner_tg_user_id
    )


@router.callback_query(F.data.startswith("confirm:"))
async def on_confirm(q: CallbackQuery) -> None:
    if q.data is None or q.from_user is None:
        await q.answer()
        return
    uid = q.data.split(":", 1)[1]
    # Peek first so we can reject impostors without flipping status.
    existing = await pending_ops.peek(uid)
    if existing is None:
        await q.answer("Эта операция уже обработана или истекла.", show_alert=True)
        return
    if not _can_act_on(existing, q.from_user.id):
        await q.answer(
            "Не твоя операция — пусть подтвердит тот, кто создал, или owner.",
            show_alert=True,
        )
        return
    op = await pending_ops.pop_for_confirm(uid)
    if op is None:
        await q.answer("Эта операция уже обработана или истекла.", show_alert=True)
        return

    try:
        async with session_scope() as session:
            reply_text = await apply(
                session, op, created_by_tg_id=q.from_user.id
            )
            # Save as a verified few-shot example for future LLM runs.
            from src.core.applier import _record_verified

            await _record_verified(session, op)
    except ApplyError as e:
        log.warning("apply_error", uid=uid, error=str(e))
        # Put the op back for correction — user can press cancel or we can
        # re-prompt; for now tell the user what failed.
        await q.answer(f"Не смог записать: {e}", show_alert=True)
        if q.message:
            await q.message.edit_reply_markup(reply_markup=None)
        return
    except Exception:
        log.exception("apply_unexpected_error", uid=uid)
        await q.answer("Ошибка при записи. Смотри логи.", show_alert=True)
        return

    # Success — edit the preview message to strike the buttons and append
    # the confirmation line.
    if q.message and q.message.text:
        new_text = f"{q.message.html_text}\n\n{reply_text}"
        with contextlib.suppress(Exception):
            await q.message.edit_text(new_text, reply_markup=None)
    await q.answer("Записал.")
    # Sprinkle a fitting sticker now and then so the chat feels alive.
    if q.message:
        await _maybe_sticker(q, POSITIVE_EMOJIS, chance=0.3)


@router.callback_query(F.data.startswith("cancel:"))
async def on_cancel(q: CallbackQuery) -> None:
    if q.data is None or q.from_user is None:
        await q.answer()
        return
    uid = q.data.split(":", 1)[1]
    existing = await pending_ops.peek(uid)
    if existing is not None and not _can_act_on(existing, q.from_user.id):
        await q.answer(
            "Не твоя операция — пусть отменит тот, кто создал, или owner.",
            show_alert=True,
        )
        return
    op = await pending_ops.pop_for_cancel(uid)
    if q.message:
        new_text = (
            (q.message.html_text or "") + "\n\n❌ <i>Отменено.</i>"
            if op is not None
            else q.message.html_text or ""
        )
        with contextlib.suppress(Exception):
            await q.message.edit_text(new_text, reply_markup=None)
    await q.answer("Ок, не записываю.")
