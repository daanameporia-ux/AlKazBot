"""Inline keyboards used across handlers."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_keyboard(op_id: str) -> InlineKeyboardMarkup:
    """Two-button confirm/cancel for operation previews (Stage 1+)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно", callback_data=f"confirm:{op_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel:{op_id}"),
            ]
        ]
    )
