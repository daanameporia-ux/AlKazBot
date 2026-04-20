"""Photo handler fires Vision only on explicit trigger in caption."""

from __future__ import annotations

import pytest
from src.bot.handlers.photo import _caption_triggers_vision


@pytest.mark.parametrize(
    "caption,bot_username,expected",
    [
        ("@al_kazbot глянь эту картинку", "al_kazbot", True),
        ("@Al_Kazbot глянь", "al_kazbot", True),  # case-insensitive
        ("", "al_kazbot", False),
        ("вот скрин", "al_kazbot", False),  # no mention, no keyword
        ("внимание, что там?", "al_kazbot", False),
    ],
)
async def test_mention_triggers(caption, bot_username, expected) -> None:
    # Keyword path is tested separately (requires DB); here we just
    # check the @-mention branch.
    import unittest.mock as m

    with m.patch(
        "src.bot.handlers.photo.find_hits",
        new=m.AsyncMock(return_value=[]),
    ):
        got = await _caption_triggers_vision(caption, bot_username)
    assert got is expected


async def test_keyword_hit_triggers() -> None:
    import unittest.mock as m

    with m.patch(
        "src.bot.handlers.photo.find_hits",
        new=m.AsyncMock(return_value=["бот"]),
    ):
        got = await _caption_triggers_vision(
            "бот, глянь что-то странное", None
        )
    assert got is True


async def test_no_bot_username_no_keyword_is_silent() -> None:
    import unittest.mock as m

    with m.patch(
        "src.bot.handlers.photo.find_hits",
        new=m.AsyncMock(return_value=[]),
    ):
        got = await _caption_triggers_vision("просто скрин", None)
    assert got is False
