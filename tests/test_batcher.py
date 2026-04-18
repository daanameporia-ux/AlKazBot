"""BatchBuffer behaviour: size flush + age flush + trigger flush."""

from __future__ import annotations

import asyncio

import pytest
from src.bot.batcher import MAX_BATCH_SIZE, BatchBuffer, BufferedMessage


@pytest.fixture
def flushes() -> list:
    return []


@pytest.fixture
def buffer(flushes):
    async def handler(batch):
        flushes.append(batch)

    return BatchBuffer(handler)


def _msg(i: int, text: str = "hi") -> BufferedMessage:
    return BufferedMessage(
        tg_message_id=i,
        tg_user_id=42,
        display_name="Казах",
        text=text,
        received_at=1.0 + i,
    )


async def test_flush_on_size(buffer: BatchBuffer, flushes: list) -> None:
    for i in range(MAX_BATCH_SIZE):
        await buffer.add(chat_id=1, msg=_msg(i))
    # fire-and-forget task needs a tick to run
    await asyncio.sleep(0.05)
    assert len(flushes) == 1
    assert len(flushes[0].messages) == MAX_BATCH_SIZE
    assert flushes[0].trigger is None
    assert flushes[0].trigger_kind == "size"


async def test_trigger_flush_drains_and_prepends(
    buffer: BatchBuffer, flushes: list
) -> None:
    # stash 3 passive messages
    for i in range(3):
        await buffer.add(chat_id=1, msg=_msg(i, f"passive {i}"))
    trigger = _msg(99, "@bot hi?")
    await buffer.flush_now(chat_id=1, trigger=trigger, trigger_kind="mention")
    await asyncio.sleep(0.05)
    assert len(flushes) == 1
    batch = flushes[0]
    assert batch.trigger is trigger
    assert batch.trigger_kind == "mention"
    assert [m.tg_message_id for m in batch.messages] == [0, 1, 2]


async def test_empty_trigger_flush_is_noop(
    buffer: BatchBuffer, flushes: list
) -> None:
    await buffer.flush_now(chat_id=1, trigger=None, trigger_kind="age")
    await asyncio.sleep(0.02)
    assert flushes == []
