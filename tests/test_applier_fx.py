"""FX guard tests — _positive_fx rejects zero/negative/None."""

from __future__ import annotations

from decimal import Decimal

import pytest
from src.core.applier import ApplyError, _positive_fx


def test_positive_fx_accepts_positive() -> None:
    assert _positive_fx(Decimal("80.37")) == Decimal("80.37")


def test_positive_fx_rejects_zero() -> None:
    with pytest.raises(ApplyError, match="Курс"):
        _positive_fx(Decimal("0"))


def test_positive_fx_rejects_negative() -> None:
    with pytest.raises(ApplyError):
        _positive_fx(Decimal("-10"))


def test_positive_fx_rejects_none() -> None:
    with pytest.raises(ApplyError):
        _positive_fx(None)
