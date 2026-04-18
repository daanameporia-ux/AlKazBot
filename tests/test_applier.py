"""Sanity tests for applier number-parse helpers.

The real DB-touching code has an integration story (not unit-testable
without a live Postgres); here we lock in the small pure helpers that
the applier relies on — they used to silently coerce "517 000,50" wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.applier import ApplyError, _dec, _req_dec


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1500", Decimal("1500")),
        ("1 500", Decimal("1500")),
        ("80.37", Decimal("80.37")),
        ("80,37", Decimal("80.37")),
        ("517 000,50", Decimal("517000.50")),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_dec(raw, expected) -> None:
    assert _dec(raw) == expected


def test_req_dec_raises_on_none() -> None:
    with pytest.raises(ApplyError):
        _req_dec(None, "amount_rub")


def test_req_dec_raises_on_garbage() -> None:
    with pytest.raises(ApplyError):
        _req_dec("N/A", "amount_rub")
