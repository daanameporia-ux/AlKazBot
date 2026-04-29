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
    # Material valuation rule (per owner, 2026-04-29):
    #   • с доверкой готов к работе → cost_rub at face value (28k typical)
    #   • без доверки на складе → average price from prepayment remainder
    #     (prepayment_rub − Σ(linked cabinets with worked_out / blocked / recovered) − Σ(linked cabinets in_stock with доверка))
    #     / count(linked cabinets in_stock without доверка)
    # All in-store statuses (in_stock + in_use + blocked + recovered) count
    # towards "live material" — only worked_out is consumed.
    LIVE_STATUSES = ("in_stock", "in_use", "blocked", "recovered")
    res = await session.execute(
        select(Cabinet).where(Cabinet.status.in_(LIVE_STATUSES))
        .order_by(Cabinet.id)
    )
    cabs = list(res.scalars().all())

    # Determine effective cost per cabinet (handle no-доверка via prepayment average)
    # Group cabinets without доверка by prepayment_id so each pack gets its own avg.
    no_doverka_by_prep: dict[int | None, list[Cabinet]] = {}
    with_doverka_by_prep: dict[int | None, list[Cabinet]] = {}
    for c_ in cabs:
        target = no_doverka_by_prep if not c_.has_doverka else with_doverka_by_prep
        target.setdefault(c_.prepayment_id, []).append(c_)

    # Look up linked prepayment info — for each prepayment id, what's been
    # consumed (worked_out) and what we still owe.
    prepayment_lookup: dict[int, dict[str, Decimal]] = {}
    if no_doverka_by_prep:
        prep_ids = [pid for pid in no_doverka_by_prep if pid is not None]
        if prep_ids:

            preps_res = await session.execute(
                select(Prepayment).where(Prepayment.id.in_(prep_ids))
            )
            for p in preps_res.scalars().all():
                # All cabinets linked to this prepayment regardless of status
                all_linked_res = await session.execute(
                    select(Cabinet).where(Cabinet.prepayment_id == p.id)
                )
                all_linked = list(all_linked_res.scalars().all())
                spent_rub = sum(
                    (Decimal(x.cost_rub) for x in all_linked if x.status == "worked_out"),
                    Decimal("0"),
                )
                with_doverka_rub = sum(
                    (
                        Decimal(x.cost_rub)
                        for x in all_linked
                        if x.has_doverka and x.status in LIVE_STATUSES
                    ),
                    Decimal("0"),
                )
                no_doverka_count = sum(
                    1
                    for x in all_linked
                    if (not x.has_doverka) and x.status in LIVE_STATUSES
                )
                remaining_rub = Decimal(p.amount_rub) - spent_rub - with_doverka_rub
                avg_no_doverka_rub = (
                    (remaining_rub / no_doverka_count) if no_doverka_count > 0 else Decimal("0")
                )
                prepayment_lookup[p.id] = {
                    "amount_rub": Decimal(p.amount_rub),
                    "amount_usdt": Decimal(p.amount_usdt),
                    "fx_rate": Decimal(p.fx_rate),
                    "spent_rub": spent_rub,
                    "with_doverka_rub": with_doverka_rub,
                    "remaining_rub": remaining_rub,
                    "avg_no_doverka_rub": avg_no_doverka_rub,
                    "supplier": p.supplier or "",
                    "status": p.status,
                }

    def _effective_cost_usdt(c_: Cabinet) -> Decimal:
        if c_.has_doverka:
            return Decimal(c_.cost_usdt)
        # No-доверка: use prepayment average if available
        info = prepayment_lookup.get(c_.prepayment_id) if c_.prepayment_id else None
        if info and info["avg_no_doverka_rub"] > 0:
            fx = info["fx_rate"] or Decimal(c_.fx_rate)
            return info["avg_no_doverka_rub"] / fx
        return Decimal(c_.cost_usdt)

    total_material = Decimal("0")
    cab_lines: list[str] = []
    for c_ in cabs:
        eff = _effective_cost_usdt(c_)
        total_material += eff
        marker = ""
        if not c_.has_doverka:
            marker = " (без доверки)"
        elif c_.status == "blocked":
            marker = " (blocked)"
        elif c_.status == "recovered":
            marker = " (recovered)"
        cab_lines.append(
            f"  {(c_.name or c_.auto_code):<24} {_fmt_usdt(eff)}{marker}"
        )

    # --- Prepayments — REFERENCE ONLY (NOT in assets total).
    # Owner instruction (2026-04-29): «нужно для справочной информации в
    # каждом отчёте держать сколько предоплаты уже внесено… когда партия
    # будет завершена, она будет закрыта». So prepayments don't double-
    # count vs cabinets they spawned — they show what we paid total and
    # what's still in transit.
    res = await session.execute(
        select(Prepayment).where(Prepayment.status.in_(("pending", "partial")))
    )
    preps = list(res.scalars().all())
    # NOT added to assets. We render lines only.
    total_prepayments = Decimal("0")
    prep_lines: list[str] = []
    for p in preps:
        info = prepayment_lookup.get(p.id) if p.id in prepayment_lookup else None
        if info:
            extra = (
                f" — внесено {_fmt_rub(info['amount_rub'])}, "
                f"отработано {_fmt_rub(info['spent_rub'])}, "
                f"в материале с доверкой {_fmt_rub(info['with_doverka_rub'])}, "
                f"остаток для no-доверки {_fmt_rub(info['remaining_rub'])}"
            )
        else:
            extra = f" — внесено {_fmt_rub(Decimal(p.amount_rub))}"
        prep_lines.append(
            f"  Партия {p.supplier or '?'}: {p.status}{extra}"
        )

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

    # --- Formula (pure arithmetic extracted to report_formula for testability) ---
    from src.core.report_formula import ReportInputs
    from src.core.report_formula import compute as compute_totals

    totals = compute_totals(
        ReportInputs(
            total_wallets=total_wallets,
            total_material=total_material,
            total_prepayments=total_prepayments,
            total_debts=total_debts,
            partner_initial_depo=total_partner_depo,
            partner_poa_share=total_partner_poa,
            partner_withdrawals=total_partner_withdrawals,
        )
    )
    total_assets = totals.total_assets
    total_liabilities = totals.total_liabilities
    net_profit = totals.net_profit

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
    # Worked-out cabinets since last report
    res = await session.execute(
        select(Cabinet).where(Cabinet.status == "worked_out").order_by(Cabinet.id)
    )
    if since_dt is not None:
        # Already filtered above for cabinets_worked_list; rebuild the
        # USDT line using the actual cost values.
        worked_total = Decimal("0")
        worked_lines: list[str] = []
        for n, code in worked_rows:
            row = await session.execute(
                select(Cabinet).where(
                    (Cabinet.name == n) | (Cabinet.auto_code == code)
                ).limit(1)
            )
            cab = row.scalar_one_or_none()
            if cab is not None:
                worked_total += Decimal(cab.cost_usdt)
                worked_lines.append(
                    f"  {(cab.name or cab.auto_code):<24} {_fmt_usdt(Decimal(cab.cost_usdt))}"
                )
        if worked_lines:
            parts.append(
                f"\n<b>Отработано с прошлого отчёта:</b> {_fmt_usdt(worked_total)}"
            )
            parts.extend(worked_lines)
    if prep_lines:
        parts.append(
            "\n<b>Предоплаты (справочно, не в активах):</b>"
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

    # --- Did the team log acquiring today? ---
    from src.db.models import Expense

    today = date.today()
    res = await session.execute(
        select(func.count(Expense.id)).where(
            Expense.category == "acquiring",
            Expense.expense_date == today,
        )
    )
    acquiring_today_flag = (res.scalar_one() or 0) > 0

    # --- Persist the report ---
    rep = Report(
        created_by=created_by_user_id,
        cabinets_worked=cabinets_worked_list,
        acquiring_today=acquiring_today_flag,
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
