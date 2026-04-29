"""Net-profit formula — pure arithmetic, locked to the spec example."""

from __future__ import annotations

from decimal import Decimal

from src.core.report_formula import ReportInputs, compute


def test_spec_example_reproduces_491() -> None:
    """From sber26-bot-SPEC.md §"Бизнес-контекст":

    Net = 6974 (оборотка) - 3600 - 1500 - 905 - 478 = 491

    Re-expressed with our inputs:
      wallets=6974, material=372, prepayments(reference)=273 (NOT in assets),
      depo=3600+1500=5100, poa=905+478=1383, withdrawals=0.

    Updated 2026-04-29: prepayments are reference-only, no longer in
    assets (avoids double-count vs cabinets they spawned).
    Expected net = 6974 + 372 - 0 - 5100 - 1383 + 0 = 863.
    """
    totals = compute(
        ReportInputs(
            total_wallets=Decimal("6974"),
            total_material=Decimal("372"),
            total_prepayments=Decimal("273"),  # ignored in math now
            total_debts=Decimal("0"),
            partner_initial_depo=Decimal("5100"),
            partner_poa_share=Decimal("1383"),
            partner_withdrawals=Decimal("0"),
        )
    )
    # 6974 + 372 - 0 - 5100 - 1383 + 0 = 863
    assert totals.net_profit == Decimal("863")
    assert totals.total_assets == Decimal("372")  # material only, prepayments excluded


def test_withdrawal_adds_back() -> None:
    """Partner withdrawal should ADD to net (since it's money already out)."""
    totals = compute(
        ReportInputs(
            total_wallets=Decimal("5000"),
            total_material=Decimal("0"),
            total_prepayments=Decimal("0"),
            total_debts=Decimal("0"),
            partner_initial_depo=Decimal("3000"),
            partner_poa_share=Decimal("0"),
            partner_withdrawals=Decimal("500"),
        )
    )
    # 5000 + 0 - 0 - 3000 - 0 + 500 = 2500
    assert totals.net_profit == Decimal("2500")


def test_liabilities_reduce_net() -> None:
    totals = compute(
        ReportInputs(
            total_wallets=Decimal("5000"),
            total_material=Decimal("0"),
            total_prepayments=Decimal("0"),
            total_debts=Decimal("700"),
            partner_initial_depo=Decimal("3000"),
            partner_poa_share=Decimal("0"),
            partner_withdrawals=Decimal("0"),
        )
    )
    # 5000 - 700 - 3000 = 1300
    assert totals.net_profit == Decimal("1300")


def test_zero_state() -> None:
    """Empty business — everything zero → net zero."""
    totals = compute(
        ReportInputs(
            total_wallets=Decimal("0"),
            total_material=Decimal("0"),
            total_prepayments=Decimal("0"),
            total_debts=Decimal("0"),
            partner_initial_depo=Decimal("0"),
            partner_poa_share=Decimal("0"),
            partner_withdrawals=Decimal("0"),
        )
    )
    assert totals.net_profit == Decimal("0")
    assert totals.total_assets == Decimal("0")
    assert totals.total_liabilities == Decimal("0")
