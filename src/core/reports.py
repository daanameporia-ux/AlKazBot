"""End-of-day report generation.

Pulls the latest state (wallet snapshots, partner balances, cabinet stock,
prepayments, client debts) and renders the classical report format from
sber26-bot-SPEC.md §"Отчёт". Also persists a `reports` row so we have
history + `cabinets_worked` for the reminder worker.

Net-profit formula (from spec):

    Net Profit = Total Wallets + Total Assets (material + prepayments)
               − Total Liabilities (client debts)
               − Σ partner_deposits + Σ partner_poa_share
               + Σ partner_withdrawals
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Cabinet,
    Client,
    PartnerContribution,
    PartnerWithdrawal,
    PoAWithdrawal,
    Prepayment,
    Report,
)
from src.db.repositories import balances as balances_repo


@dataclass(slots=True)
class ReportRenderResult:
    text: str
    report_id: int | None
    net_profit_usdt: Decimal
    total_wallets_usdt: Decimal
    total_assets_usdt: Decimal
    total_liabilities_usdt: Decimal


def _fmt_usdt(x: Decimal) -> str:
    return f"{x:,.0f}$".replace(",", " ")


def _fmt_usdt_2(x: Decimal) -> str:
    return f"{x:,.2f}$".replace(",", " ")


def _fmt_rub(x: Decimal) -> str:
    return f"{x:,.0f}₽".replace(",", " ")


async def generate(session: AsyncSession, *, created_by_user_id: int | None = None) -> ReportRenderResult:
    # --- Wallets ---
    wallet_items = await balances_repo.latest_wallet_balances(session)
    total_wallets = Decimal("0")
    wallet_lines: list[str] = []
    for w in wallet_items:
        if w.amount_usdt is None:
            continue
        total_wallets += Decimal(w.amount_usdt)
        native = ""
        if w.currency == "RUB" and w.amount_native is not None:
            native = f"{_fmt_rub(Decimal(w.amount_native))} / {w.fx_rate or '?'} = "
        wallet_lines.append(
            f"  {w.wallet_name:<14} {native}{_fmt_usdt(Decimal(w.amount_usdt))}"
        )

    # --- Partners ---
    partner_shares = await balances_repo.partner_shares(session)
    partner_lines: list[str] = []
    total_partner_depo = Decimal("0")
    total_partner_poa = Decimal("0")
    total_partner_withdrawals = Decimal("0")
    for s in partner_shares:
        total_partner_depo += s.deposits_usdt
        total_partner_poa += s.contributions_usdt
        total_partner_withdrawals += s.withdrawals_usdt
        poa_part = ""
        if s.contributions_usdt > 0:
            poa_part = f" / +{_fmt_usdt(s.contributions_usdt)} (от снятий)"
        partner_lines.append(
            f"  {s.partner_name} {_fmt_usdt(s.deposits_usdt)}{poa_part}"
        )

    # --- Cabinets on the shelf ---
    res = await session.execute(
        select(Cabinet).where(Cabinet.status.in_(("in_stock", "in_use", "blocked")))
        .order_by(Cabinet.id)
    )
    cabs = list(res.scalars().all())
    total_material = sum((Decimal(c.cost_usdt) for c in cabs), Decimal("0"))
    cab_lines = [
        f"  {(c.name or c.auto_code):<14} {_fmt_usdt(Decimal(c.cost_usdt))}"
        for c in cabs
    ]

    # --- Prepayments (open) ---
    res = await session.execute(
        select(Prepayment).where(Prepayment.status.in_(("pending", "partial")))
    )
    preps = list(res.scalars().all())
    total_prepayments = sum(
        (Decimal(p.amount_usdt) for p in preps), Decimal("0")
    )
    prep_lines = [
        f"  Предоплата {_fmt_rub(Decimal(p.amount_rub))} = {_fmt_usdt(Decimal(p.amount_usdt))}"
        + (f" ({p.supplier})" if p.supplier else "")
        for p in preps
    ]

    # --- Client debts (unpaid POA shares) ---
    res = await session.execute(
        select(PoAWithdrawal, Client.name)
        .join(Client, Client.id == PoAWithdrawal.client_id)
        .where(
            PoAWithdrawal.client_paid.is_(False),
            PoAWithdrawal.client_debt_usdt.isnot(None),
        )
    )
    debts = res.all()
    total_debts = sum(
        (Decimal(p.client_debt_usdt) for p, _ in debts), Decimal("0")
    )
    debt_lines = [
        f"  {name}: {_fmt_usdt(Decimal(poa.client_debt_usdt))}"
        for poa, name in debts
    ]

    # --- Cabinets worked since last report ---
    last_rep_res = await session.execute(
        select(Report).order_by(Report.id.desc()).limit(1)
    )
    last_report = last_rep_res.scalar_one_or_none()
    since_dt = last_report.created_at if last_report else None
    worked_cabs_cte = select(Cabinet.name, Cabinet.auto_code).where(
        Cabinet.status == "worked_out"
    )
    if since_dt is not None:
        worked_cabs_cte = worked_cabs_cte.where(
            Cabinet.worked_out_date >= since_dt.date()
        )
    worked_rows = (await session.execute(worked_cabs_cte)).all()
    cabinets_worked_list = [
        {"name": (n or code), "code": code} for n, code in worked_rows
    ]

    # --- Formula ---
    total_assets = total_material + total_prepayments
    total_liabilities = total_debts
    net_profit = (
        total_wallets
        + total_assets
        - total_liabilities
        - total_partner_depo
        - total_partner_poa
        + total_partner_withdrawals
    )

    # --- Render ---
    today = date.today()
    parts: list[str] = [f"<b>Отчёт на {today.strftime('%d.%m.%Y')}:</b>\n"]
    parts.append("<b>Депозиты (вложения партнёров):</b>")
    parts.extend(partner_lines or ["  (ещё никто не вкладывался)"])
    parts.append(f"\n<b>Оборотка:</b> {_fmt_usdt(total_wallets)}")
    if wallet_lines:
        parts.extend(wallet_lines)
    else:
        parts.append("  (снапшотов ещё нет — напиши балансы в чат)")
    parts.append(f"\n<b>Материал (склад):</b> {_fmt_usdt(total_material)}")
    if cab_lines:
        parts.extend(cab_lines)
    else:
        parts.append("  (склад пустой)")
    if prep_lines:
        parts.append(
            f"\n<b>Предоплаты:</b> {_fmt_usdt(total_prepayments)}"
        )
        parts.extend(prep_lines)
    if debt_lines:
        parts.append(f"\n<b>Долги клиентам:</b> {_fmt_usdt(total_debts)}")
        parts.extend(debt_lines)
    parts.append(
        f"\n<b>Чистая прибыль:</b> "
        f"{_fmt_usdt(total_wallets)} + {_fmt_usdt(total_assets)}"
        f" − {_fmt_usdt(total_liabilities)}"
        f" − {_fmt_usdt(total_partner_depo + total_partner_poa)}"
        f" + {_fmt_usdt(total_partner_withdrawals)}"
        f" = <b>{_fmt_usdt_2(net_profit)}</b>"
    )

    text = "\n".join(parts)

    # --- Persist the report ---
    rep = Report(
        created_by=created_by_user_id,
        cabinets_worked=cabinets_worked_list,
        acquiring_today=None,  # TODO: populated from daily acquiring expense
        total_wallets=total_wallets,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        net_profit=net_profit,
        raw_output=text,
    )
    session.add(rep)
    await session.flush()

    return ReportRenderResult(
        text=text,
        report_id=rep.id,
        net_profit_usdt=net_profit,
        total_wallets_usdt=total_wallets,
        total_assets_usdt=total_assets,
        total_liabilities_usdt=total_liabilities,
    )


async def acquiring_days_ago(session: AsyncSession) -> int | None:
    from src.db.models import Expense

    res = await session.execute(
        select(func.max(Expense.expense_date)).where(Expense.category == "acquiring")
    )
    d = res.scalar_one_or_none()
    if d is None:
        return None
    return (date.today() - d).days
