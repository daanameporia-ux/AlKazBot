"""Pure net-profit calculation — split out for unit-testability.

The full /report generator in `src/core/reports.py` talks to the DB;
this file owns just the arithmetic so we can unit-test the exact
formula from sber26-bot-SPEC.md §"Отчёт/Формула прибыли":

    Net Profit = Total Wallets
               + Total Assets (material + prepayments)
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
    total_prepayments: Decimal
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
    total_assets = inputs.total_material + inputs.total_prepayments
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
