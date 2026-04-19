"""Post-processing for common Whisper mishears on Russian speech."""

from __future__ import annotations

import pytest
from src.core.voice_transcribe import (
    _build_whisper_prompt,
    _postprocess_transcript,
)


@pytest.mark.parametrize(
    "raw,fixed_contains",
    [
        ("Вержан ты, длбойок, блять.", "ержан"),
        ("Верджан, не слушай его", "ержан"),
        ("alkaz выше, я дал тебе стикеры", "алказ"),
        ("Alkash, запиши", "алкаш"),
        ("erzhan, просыпайся", "ержан"),
        ("Алкаш нахуят и стал вытаскивать", "нахуя ты"),
    ],
)
def test_postprocess_fixes_common_mishears(raw, fixed_contains) -> None:
    out = _postprocess_transcript(raw)
    assert fixed_contains in out.lower()


def test_postprocess_idempotent() -> None:
    s = "алкаш, нахуя ты опять"
    assert _postprocess_transcript(_postprocess_transcript(s)) == _postprocess_transcript(s)


def test_empty_input() -> None:
    assert _postprocess_transcript("") == ""


def test_build_prompt_includes_core_vocab() -> None:
    prompt = _build_whisper_prompt(["бот"], entities=[])
    assert prompt is not None
    assert "никонов" in prompt.lower()
    assert "миша" in prompt.lower()
    assert "сбер" in prompt.lower()


def test_build_prompt_rejects_latin_entries() -> None:
    prompt = _build_whisper_prompt(
        ["al_kazbot", "alkaz", "алкаш"], entities=[]
    )
    assert prompt is not None
    # Latin tokens filtered; only cyrillic "алкаш" survives from the input.
    assert "al_kazbot" not in prompt
    assert "alkaz" not in prompt
    assert "алкаш" in prompt
