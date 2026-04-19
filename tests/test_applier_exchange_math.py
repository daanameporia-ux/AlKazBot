"""Exchange math guards in applier.

Catches the class of bugs where Claude swaps fields or garbles numbers:
  * `amount_usdt` and `fx_rate` transposed
  * `amount_rub / fx_rate` doesn't match `amount_usdt` within tolerance
  * Zero / negative amounts slipping through
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.core.applier import ApplyError, apply
from src.core.pending_ops import PendingOp
from src.llm.schemas import Intent


def _op(**fields) -> PendingOp:
    return PendingOp(
        uid="test",
        chat_id=-1,
        preview_message_id=None,
        intent=Intent.EXCHANGE.value,
        fields=fields,
        summary="test",
        source_message_ids=[],
        created_by_tg_id=1,
        created_at=datetime.now(),
        status="pending",
    )


async def _run(op: PendingOp):
    session = MagicMock()
    session.execute = AsyncMock()
    session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    await apply(session, op, created_by_tg_id=1)


async def test_negative_amount_rejected() -> None:
    op = _op(amount_rub=-100, amount_usdt=1.24, fx_rate=80.46)
    with pytest.raises(ApplyError, match="нулевой/отрицательной"):
        await _run(op)


async def test_zero_amount_rejected() -> None:
    op = _op(amount_rub=0, amount_usdt=0, fx_rate=80.46)
    with pytest.raises(ApplyError, match="нулевой/отрицательной"):
        await _run(op)


async def test_swapped_usdt_and_rate_rejected() -> None:
    # Classic: 280000/80.46=3480 — user transposed, calling amount_usdt=80.46
    # and fx_rate=3480. usdt (80.46) < rate (3480) means swapped.
    op = _op(amount_rub=280000, amount_usdt=80.46, fx_rate=3480)
    with pytest.raises(ApplyError, match="поменяли местами"):
        await _run(op)


async def test_math_mismatch_rejected() -> None:
    # 280000 / 80 = 3500 USDT — but amount_usdt says 5000 (42% off).
    op = _op(amount_rub=280000, amount_usdt=5000, fx_rate=80)
    with pytest.raises(ApplyError, match="Арифметика не сходится"):
        await _run(op)


async def test_math_within_tolerance_passes_validation() -> None:
    # 280000 / 80.46 = 3479.99 — amount_usdt 3480 is within 0.5%.
    op = _op(amount_rub=280000, amount_usdt=3480, fx_rate=80.46)
    # Will reach the repo.create call; our mock session raises at that point.
    # We just confirm we didn't get the "не сходится" error first.
    with pytest.raises(Exception) as exc_info:
        await _run(op)
    err = str(exc_info.value)
    assert "Арифметика не сходится" not in err
    assert "поменяли местами" not in err
    assert "нулевой/отрицательной" not in err
