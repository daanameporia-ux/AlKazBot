"""Pure net-profit calculation — split out for unit-testability.

Updated 2026-04-29 per owner instruction:
  • Prepayments are NO LONGER added to assets — they were double-counting
    against cabinets spawned by the same prepayment. Now they're a
    reference line only, rendered by reports.py separately.
  • Material valuation handles доверка-presence: cabinets without доверка
    are valued at the prepayment-remainder average; that calc happens in
    reports.py and arrives here as a ready `total_material`.

Net-profit identity (assets-liabilities-equity = profit):

    Net Profit = Total Wallets
               + Total Material (cabinets at effective cost)
               − Total Liabilities (client debts)
               − Σ partner_initial_deposits
               − Σ partner_poa_contributions
               + Σ partner_withdrawals
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True)
class ReportInputs:
    total_wallets: Decimal
    total_material: Decimal
    total_prepayments: Decimal  # reference only — not in profit math
    total_debts: Decimal
    partner_initial_depo: Decimal
    partner_poa_share: Decimal
    partner_withdrawals: Decimal


@dataclass(slots=True)
class ReportTotals:
    total_assets: Decimal
    total_liabilities: Decimal
    net_profit: Decimal


def compute(inputs: ReportInputs) -> ReportTotals:
    # Assets = wallets + material. Prepayments excluded (they're already
    # represented by the cabinets they bought; double-count avoided).
    total_assets = inputs.total_material
    total_liabilities = inputs.total_debts
    net_profit = (
        inputs.total_wallets
        + total_assets
        - total_liabilities
        - inputs.partner_initial_depo
        - inputs.partner_poa_share
        + inputs.partner_withdrawals
    )
    return ReportTotals(
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        net_profit=net_profit,
    )
