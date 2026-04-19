"""Claude-Haiku Vision describer for static WebP stickers.

Goal: give the batch analyzer's system prompt something richer than a
bare emoji label. When a static sticker is first captured (or during
the startup backfill worker), we:

  1. Pull the WebP bytes via bot.get_file + bot.download.
  2. Ship them to Claude Haiku Vision with a tight Russian prompt.
  3. Store the returned one-liner in `seen_stickers.description`.

Why only static WebP for now:
  * TGS (Lottie JSON) requires a rendering step we haven't set up.
  * WebM video stickers require ffmpeg for first-frame extraction and
    we don't ship it in the container yet.

The column is nullable — un-described stickers simply don't show up in
the per-sticker description hints Claude gets, but still count in the
emoji spectrum. So this is additive; nothing breaks if Vision is down.
"""

from __future__ import annotations

import asyncio
import base64
import io
from datetime import UTC, datetime

import anthropic
from aiogram import Bot
from sqlalchemy import select, update

from src.config import settings
from src.db.models import SeenSticker
from src.db.session import session_scope
from src.llm.client import get_client
from src.logging_setup import get_logger

log = get_logger(__name__)

DESCRIBE_PROMPT = (
    "Опиши что изображено на этом стикере в 1-2 коротких фразах на "
    "русском. Укажи объекты, эмоции, текст на стикере если есть. "
    "Без лирики и предисловий — просто факты, как caption. "
    "Максимум 120 символов."
)

MAX_DESCRIPTION_CHARS = 300


async def _download_sticker_bytes(bot: Bot, file_id: str) -> bytes | None:
    """Pull raw WebP bytes via Bot API. Returns None on any failure."""
    try:
        file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download(file, destination=buf)
        return buf.getvalue()
    except Exception:
        log.exception("sticker_download_failed", file_id=file_id[:20])
        return None


async def _describe_bytes_via_vision(
    img_bytes: bytes, media_type: str = "image/webp"
) -> str | None:
    """Send sticker image to Claude Haiku Vision, return description text.

    Claude Vision supports image/webp natively (no conversion needed for
    static stickers). Haiku is ~4x cheaper than Sonnet and plenty good
    for a one-line descriptor.
    """
    client: anthropic.AsyncAnthropic = get_client()
    b64 = base64.standard_b64encode(img_bytes).decode("ascii")
    try:
        resp = await client.messages.create(
            model=settings.anthropic_fallback_model,
            max_tokens=120,
            temperature=0.1,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": DESCRIBE_PROMPT},
                    ],
                }
            ],
        )
    except Exception:
        log.exception("vision_call_failed")
        return None

    # Collect text content blocks.
    chunks: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)  # type: ignore[attr-defined]
    text = " ".join(chunks).strip()
    if not text:
        return None
    return text[:MAX_DESCRIPTION_CHARS]


async def describe_one(bot: Bot, sticker_id: int) -> str | None:
    """Fetch a specific sticker row, download, describe, persist.

    Idempotent: if `description` is already set, returns it without a
    Vision call. Static WebP only; TGS and WebM are skipped (returns
    None, leaves DB unchanged).
    """
    async with session_scope() as session:
        row = await session.get(SeenSticker, sticker_id)
        if row is None:
            return None
        if row.description is not None:
            return row.description
        if row.is_animated or row.is_video:
            # Skip — no current pipeline for TGS / WebM.
            return None
        file_id = row.file_id

    img = await _download_sticker_bytes(bot, file_id)
    if img is None:
        return None

    desc = await _describe_bytes_via_vision(img)
    if desc is None:
        return None

    async with session_scope() as session:
        await session.execute(
            update(SeenSticker)
            .where(SeenSticker.id == sticker_id)
            .values(
                description=desc,
                description_model=settings.anthropic_fallback_model,
                described_at=datetime.now(UTC),
            )
        )
    log.info(
        "sticker_described",
        sticker_id=sticker_id,
        desc_preview=desc[:60],
    )
    return desc


async def describe_missing(
    bot: Bot,
    *,
    limit: int | None = None,
    sleep_between: float = 0.8,
) -> int:
    """Walk undescribed static stickers and describe each one.

    Runs as a long-lived background task on bot startup — by the time a
    user sends their first sticker, the library is already captioned
    (or close to). Safe to re-run; idempotent.

    Returns how many rows were described in this pass.
    """
    async with session_scope() as session:
        q = (
            select(SeenSticker.id)
            .where(
                SeenSticker.description.is_(None),
                SeenSticker.is_animated.is_(False),
                SeenSticker.is_video.is_(False),
            )
            .order_by(SeenSticker.id.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        rows = await session.execute(q)
        ids = [r[0] for r in rows.all()]

    log.info("sticker_describe_backfill_start", todo=len(ids))
    done = 0
    for sid in ids:
        try:
            res = await describe_one(bot, sid)
            if res is not None:
                done += 1
        except Exception:
            log.exception("sticker_describe_worker_item_failed", sticker_id=sid)
        # Gentle pacing so we don't hammer Anthropic or Telegram.
        await asyncio.sleep(sleep_between)
    log.info("sticker_describe_backfill_done", described=done, todo=len(ids))
    return done
