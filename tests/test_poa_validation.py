"""POA partner_shares validation — must sum to 100 − client_share_pct."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.core.applier import ApplyError, apply
from src.core.pending_ops import PendingOp
from src.llm.schemas import Intent


def _poa_op(
    *,
    shares: list[dict] | None = None,
    client_pct: float = 65.0,
    amount_rub: float = 150000,
    client_name: str = "Никонов",
) -> PendingOp:
    return PendingOp(
        uid="test",
        chat_id=-1,
        preview_message_id=None,
        intent=Intent.POA_WITHDRAWAL.value,
        fields={
            "client_name": client_name,
            "amount_rub": amount_rub,
            "client_share_pct": client_pct,
            "partner_shares": shares or [],
        },
        summary="test poa",
        source_message_ids=[],
        created_by_tg_id=1,
        created_at=__import__("datetime").datetime.now(),
        status="pending",
    )


async def _call(op: PendingOp):
    # Build a minimal fake session. We stop at the first real DB call inside
    # apply() — for the validation branch that's before clients.get_or_create.
    session = MagicMock()
    session.execute = AsyncMock()
    # users repo first call
    session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    await apply(session, op, created_by_tg_id=1)


async def test_shares_sum_less_than_expected_raises() -> None:
    op = _poa_op(
        shares=[
            {"partner": "Казах", "pct": "15"},
            {"partner": "Арбуз", "pct": "10"},
        ],
        client_pct=65,
    )
    # 15 + 10 + 65 = 90, not 100
    with pytest.raises(ApplyError, match="Доли партнёров"):
        await _call(op)


async def test_shares_sum_more_than_expected_raises() -> None:
    op = _poa_op(
        shares=[
            {"partner": "Казах", "pct": "30"},
            {"partner": "Арбуз", "pct": "20"},
        ],
        client_pct=65,
    )
    # 30 + 20 + 65 = 115
    with pytest.raises(ApplyError, match="Доли партнёров"):
        await _call(op)


async def test_zero_share_rejected() -> None:
    op = _poa_op(
        shares=[
            {"partner": "Казах", "pct": "35"},
            {"partner": "Арбуз", "pct": "0"},
        ],
        client_pct=65,
    )
    with pytest.raises(ApplyError, match="Некорректная доля"):
        await _call(op)


async def test_empty_partner_name_rejected() -> None:
    op = _poa_op(
        shares=[
            {"partner": "", "pct": "35"},
        ],
        client_pct=65,
    )
    with pytest.raises(ApplyError, match="Некорректная доля"):
        await _call(op)


async def test_shares_exact_100_tolerance_allowed() -> None:
    # 25 + 10 + 65 = 100 — valid. We can't actually reach the DB here
    # without a live session, but we can verify validation doesn't raise.
    op = _poa_op(
        shares=[
            {"partner": "Казах", "pct": "25"},
            {"partner": "Арбуз", "pct": "10"},
        ],
        client_pct=65,
    )
    # Expect a DIFFERENT error (from client_repo/get_or_create on the mock)
    # — NOT the share-validation ApplyError.
    with pytest.raises(Exception) as exc_info:
        await _call(op)
    assert "Доли партнёров" not in str(exc_info.value)
