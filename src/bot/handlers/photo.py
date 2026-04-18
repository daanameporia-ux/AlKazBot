"""Photo (and photo-as-document) intake via Claude Vision.

User sends a photo — likely a screenshot of TapBank / Mercurio, a receipt,
or a Sber ATM slip. We download the largest size, base64-encode it, feed
it to Claude as a multimodal message alongside the regular batch-analyze
prompt, and show preview cards like we do for text.
"""

from __future__ import annotations

import base64
import io

from aiogram import F, Router
from aiogram.types import Message

from src.config import settings
from src.core import pending_ops
from src.core.batch_processor import (
    make_flush_handler as _mk,  # noqa: F401 — warm up the import chain
)
from src.core.preview import render_op_card
from src.db.repositories import knowledge as kb_repo
from src.db.session import session_scope
from src.llm.batch_analyzer import ANALYZE_TOOL, BATCH_INSTRUCTION, BatchAnalysis
from src.llm.client import complete
from src.llm.system_prompt import build_system_blocks
from src.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="photo")


def _is_whitelisted(user_id: int) -> bool:
    return user_id in settings.allowed_tg_user_ids


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


_IMAGE_INSTRUCTION = """\
# Image intake

Пользователь прислал фото. Это может быть:
  * скриншот банковского приложения / ATM-чека — извлеки операции (сумма,
    время, назначение);
  * чек из магазина — expense;
  * скрин обменника с курсом — exchange;
  * скрин личного кабинета сервиса TapBank/Mercurio/Rapira — возможно
    wallet_snapshot балансов.

Верни `operations[]` как обычно. Если это нерелевантная картинка (мем,
фото котика, селфи, и т.п.) — `chat_only=true`, `chat_reply` с лёгкой
подъёбкой.
"""


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not _is_whitelisted(message.from_user.id):
        return
    photos = message.photo or []
    if not photos:
        return

    # Largest size — last element.
    largest = photos[-1]

    await message.reply("Смотрю картинку…")

    try:
        buf = io.BytesIO()
        await message.bot.download(largest, destination=buf)
    except Exception:
        log.exception("photo_download_failed")
        await message.reply("Не смог скачать фото. Попробуй ещё раз.")
        return

    img_bytes = buf.getvalue()
    img_b64 = base64.standard_b64encode(img_bytes).decode("ascii")

    caption = message.caption or ""
    user_text_block = BATCH_INSTRUCTION + "\n\n" + _IMAGE_INSTRUCTION
    if caption:
        user_text_block += f"\n\nПодпись к фото: {caption}"

    kb = await _load_kb_items()
    system_blocks = build_system_blocks(knowledge_items=kb)

    try:
        resp = await complete(
            system_blocks=system_blocks,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": user_text_block},
                    ],
                }
            ],
            tools=[ANALYZE_TOOL],
            tool_choice={"type": "tool", "name": "analyze_batch"},
            max_tokens=2500,
            temperature=0.2,
        )
    except Exception:
        log.exception("vision_call_failed")
        await message.reply("LLM не ответил. Попробуй ещё раз.")
        return

    payload: dict | None = None
    for block in resp.raw.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "analyze_batch":
            payload = block.input  # type: ignore[assignment]
            break
    if payload is None:
        await message.reply("Claude ничего не распознал на фото.")
        return

    try:
        analysis = BatchAnalysis.model_validate(payload)
    except Exception:
        log.exception("vision_validation_failed", payload=payload)
        await message.reply("Claude ответил кривым форматом. Попробуй другое фото.")
        return

    if analysis.chat_only or not analysis.operations:
        if analysis.chat_reply:
            await message.reply(analysis.chat_reply)
        else:
            await message.reply("Ничего полезного для учёта не увидел.")
        return

    for op in analysis.operations:
        entry = await pending_ops.register(
            chat_id=message.chat.id,
            intent=op.intent.value,
            fields=op.fields,
            summary=op.summary,
            source_message_ids=[message.message_id],
            created_by_tg_id=message.from_user.id,
        )
        from src.core.batch_processor import _confirm_kb

        text = render_op_card(
            intent=op.intent.value,
            fields=op.fields,
            summary=op.summary,
            confidence=op.confidence,
            ambiguities=op.ambiguities,
        )
        sent = await message.answer(text, reply_markup=_confirm_kb(entry.uid))
        await pending_ops.attach_preview(entry.uid, sent.message_id)
