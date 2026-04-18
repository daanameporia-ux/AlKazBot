"""Callback-query handlers for confirm / cancel of pending operations."""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.core.applier import ApplyError, apply
from src.core.pending_ops import get_registry
from src.db.session import session_scope
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="callbacks")


@router.callback_query(F.data.startswith("confirm:"))
async def on_confirm(q: CallbackQuery) -> None:
    if q.data is None or q.from_user is None:
        await q.answer()
        return
    uid = q.data.split(":", 1)[1]
    reg = get_registry()
    op = await reg.pop(uid)
    if op is None:
        await q.answer("Эта операция уже обработана или истекла.", show_alert=True)
        return

    try:
        async with session_scope() as session:
            reply_text = await apply(
                session, op, created_by_tg_id=q.from_user.id
            )
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


@router.callback_query(F.data.startswith("cancel:"))
async def on_cancel(q: CallbackQuery) -> None:
    if q.data is None:
        await q.answer()
        return
    uid = q.data.split(":", 1)[1]
    reg = get_registry()
    op = await reg.pop(uid)
    if q.message:
        new_text = (
            (q.message.html_text or "") + "\n\n❌ <i>Отменено.</i>"
            if op is not None
            else q.message.html_text or ""
        )
        with contextlib.suppress(Exception):
            await q.message.edit_text(new_text, reply_markup=None)
    await q.answer("Ок, не записываю.")
