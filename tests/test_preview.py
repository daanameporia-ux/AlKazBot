"""Preview card formatting — the user sees these strings in chat."""

from __future__ import annotations

from src.core.preview import render
from src.llm.schemas import Intent


def test_exchange_preview_has_all_fields() -> None:
    t = render(
        Intent.EXCHANGE.value,
        {"amount_rub": 517000, "amount_usdt": 6433, "fx_rate": "80.37"},
        "обмен 517к",
    )
    assert "517 000 ₽" in t
    assert "USDT" in t
    assert "80.37" in t


def test_poa_preview_lists_shares() -> None:
    t = render(
        Intent.POA_WITHDRAWAL.value,
        {
            "client_name": "Никонов",
            "amount_rub": 150000,
            "client_share_pct": 65,
            "partner_shares": [
                {"partner": "Казах", "pct": 25},
                {"partner": "Арбуз", "pct": 10},
            ],
        },
        "снятие Никонов 150к",
    )
    assert "Никонов" in t
    assert "150 000 ₽" in t
    assert "Казах" in t and "25" in t
    assert "Арбуз" in t and "10" in t


def test_cabinet_worked_out_preview() -> None:
    t = render(
        Intent.CABINET_WORKED_OUT.value,
        {"name_or_code": "Аляс"},
        "Аляс отработан",
    )
    assert "Аляс" in t
