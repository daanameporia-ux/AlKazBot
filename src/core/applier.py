"""Apply a confirmed operation: dispatch to the right repository + audit.

Called from the confirm callback handler after the user presses ✅. Each
intent has its own sub-applier that translates the LLM-extracted `fields`
dict into concrete repo calls.

Applier functions MUST be idempotent per pending-op uid (the registry pops
on confirm, so double-press is a no-op), and MUST write a row to
`audit_log` describing what they did.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.pending_ops import PendingOp
from src.db.repositories import (
    audit as audit_repo,
)
from src.db.repositories import (
    balances as balances_repo,
)
from src.db.repositories import (
    cabinets as cabinet_repo,
)
from src.db.repositories import (
    clients as client_repo,
)
from src.db.repositories import (
    exchanges as exchange_repo,
)
from src.db.repositories import (
    expenses as expense_repo,
)
from src.db.repositories import (
    knowledge as kb_repo,
)
from src.db.repositories import (
    partner_ops as partner_repo,
)
from src.db.repositories import (
    poa as poa_repo,
)
from src.db.repositories import (
    prepayments as prepayment_repo,
)
from src.db.repositories import (
    users as user_repo,
)
from src.db.repositories import (
    wallets as wallet_repo,
)
from src.llm.schemas import Intent
from src.logging_setup import get_logger

log = get_logger(__name__)


class ApplyError(RuntimeError):
    pass


def _dec(x) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x).replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None


def _req_dec(x, field: str) -> Decimal:
    d = _dec(x)
    if d is None:
        raise ApplyError(f"Поле `{field}` не разобралось как число: {x!r}")
    return d


def _positive_fx(fx: Decimal | None, *, field: str = "fx_rate") -> Decimal:
    """Ensure the FX rate is strictly positive — zero/negative would corrupt
    every downstream amount calculation.
    """
    if fx is None or fx <= 0:
        raise ApplyError(
            f"Курс {field} невалидный ({fx!r}). Нужен положительный. "
            "Запиши курс сначала (строка вида '80000/1000=80')."
        )
    return fx


async def _resolve_fx(
    session: AsyncSession, provided: Decimal | None
) -> Decimal:
    """Pick the fx rate: user-provided wins, otherwise latest snapshot,
    otherwise raise. No more silent fallback to 1.
    """
    if provided is not None:
        return _positive_fx(provided, field="fx_rate (из операции)")
    snap = await balances_repo.current_fx_rate(session)
    if snap is None:
        raise ApplyError(
            "Курса нет ни в операции, ни в базе. "
            "Сначала запиши курс (строка вида '80000/1000=80'), потом эту операцию."
        )
    return _positive_fx(snap.rate, field="fx_rate (из базы)")


async def apply(
    session: AsyncSession, op: PendingOp, *, created_by_tg_id: int
) -> str:
    """Return a short Russian confirmation line for the user."""
    me = await user_repo.get_user_by_tg_id(session, created_by_tg_id)
    user_id = me.id if me else None
    intent = op.intent
    f = op.fields

    if intent == Intent.EXCHANGE.value:
        ex = await exchange_repo.create(
            session,
            amount_rub=_req_dec(f.get("amount_rub"), "amount_rub"),
            amount_usdt=_req_dec(f.get("amount_usdt"), "amount_usdt"),
            fx_rate=_positive_fx(_req_dec(f.get("fx_rate"), "fx_rate")),
            raw_input=op.summary,
            created_by_user_id=user_id,
        )
        await _audit(session, user_id, "create", "exchanges", ex.id, new=f)
        return f"✅ Обмен #{ex.id} записан. Курс {ex.fx_rate} зафиксирован."

    if intent == Intent.EXPENSE.value:
        amount_rub = _dec(f.get("amount_rub"))
        amount_usdt = _dec(f.get("amount_usdt"))
        fx = _dec(f.get("fx_rate"))
        if amount_usdt is None:
            if amount_rub is None:
                raise ApplyError(
                    "Нужна сумма расхода — хотя бы в рублях или в USDT."
                )
            fx = await _resolve_fx(session, fx)
            amount_usdt = amount_rub / fx
        ex = await expense_repo.create(
            session,
            category=str(f.get("category") or "other"),
            amount_rub=amount_rub,
            amount_usdt=amount_usdt,
            fx_rate=fx,
            description=f.get("description"),
            created_by_user_id=user_id,
        )
        await _audit(session, user_id, "create", "expenses", ex.id, new=f)
        return f"✅ Расход #{ex.id} записан ({ex.category})."

    if intent == Intent.PARTNER_DEPOSIT.value:
        partner = await partner_repo.resolve_partner(session, str(f.get("partner", "")))
        if partner is None:
            raise ApplyError(f"Не нашёл партнёра: {f.get('partner')}")
        c = await partner_repo.record_contribution(
            session,
            partner_id=partner.id,
            amount_usdt=_req_dec(f.get("amount_usdt"), "amount_usdt"),
            source="manual",
            notes=op.summary,
        )
        await _audit(session, user_id, "create", "partner_contributions", c.id, new=f)
        return f"✅ Взнос {partner.name} записан ({c.amount_usdt} USDT)."

    if intent == Intent.PARTNER_WITHDRAWAL.value:
        partner = await partner_repo.resolve_partner(session, str(f.get("partner", "")))
        if partner is None:
            raise ApplyError(f"Не нашёл партнёра: {f.get('partner')}")
        wallet_code = f.get("from_wallet")
        wallet_id = None
        if wallet_code:
            w = await wallet_repo.get_by_code(session, wallet_code)
            wallet_id = w.id if w else None
        wd = await partner_repo.record_withdrawal(
            session,
            partner_id=partner.id,
            amount_usdt=_req_dec(f.get("amount_usdt"), "amount_usdt"),
            from_wallet_id=wallet_id,
            notes=op.summary,
        )
        await _audit(session, user_id, "create", "partner_withdrawals", wd.id, new=f)
        return f"✅ Вывод {partner.name}: {wd.amount_usdt} USDT."

    if intent == Intent.POA_WITHDRAWAL.value:
        client_name = str(f.get("client_name") or "").strip()
        if not client_name:
            raise ApplyError("Не указано имя клиента.")
        c = await client_repo.get_or_create(session, client_name)
        poa = await poa_repo.create_pending(
            session,
            client_id=c.id,
            amount_rub=_req_dec(f.get("amount_rub"), "amount_rub"),
            partner_shares=list(f.get("partner_shares") or []),
            client_share_pct=_req_dec(f.get("client_share_pct"), "client_share_pct"),
            notes=op.summary,
            created_by_user_id=user_id,
        )
        await _audit(session, user_id, "create", "poa_withdrawals", poa.id, new=f)
        return (
            f"✅ Снятие #{poa.id} по {client_name} записано. "
            f"Жду обмен — после него посчитаю доли."
        )

    if intent == Intent.CABINET_PURCHASE.value:
        cost_rub = _req_dec(f.get("cost_rub"), "cost_rub")
        fx = await _resolve_fx(session, _dec(f.get("fx_rate")))
        cost_usdt = cost_rub / fx
        cab = await cabinet_repo.create(
            session,
            name=f.get("name"),
            cost_rub=cost_rub,
            cost_usdt=cost_usdt,
            fx_rate=fx,
        )
        await _audit(session, user_id, "create", "cabinets", cab.id, new=f)
        return f"✅ Кабинет {cab.name or cab.auto_code} на склад ({cost_usdt:.2f}$)."

    if intent == Intent.CABINET_WORKED_OUT.value:
        key = str(f.get("name_or_code") or "").strip()
        cab = await cabinet_repo.find_by_name_or_code(session, key)
        if cab is None:
            raise ApplyError(f"Не нашёл кабинет: {key}")
        await cabinet_repo.set_status(
            session, cab.id, "worked_out", worked_out_date=date.today()
        )
        await _audit(
            session, user_id, "status_change", "cabinets", cab.id,
            old={"status": cab.status}, new={"status": "worked_out"},
        )
        return f"✅ Кабинет {cab.name or cab.auto_code} списан со склада."

    if intent == Intent.CABINET_BLOCKED.value:
        key = str(f.get("name_or_code") or "").strip()
        cab = await cabinet_repo.find_by_name_or_code(session, key)
        if cab is None:
            raise ApplyError(f"Не нашёл кабинет: {key}")
        await cabinet_repo.set_status(session, cab.id, "blocked")
        await _audit(
            session, user_id, "status_change", "cabinets", cab.id,
            old={"status": cab.status}, new={"status": "blocked"},
        )
        return f"⚠️ Кабинет {cab.name or cab.auto_code} помечен заблокированным."

    if intent == Intent.PREPAYMENT_GIVEN.value:
        amount_rub = _req_dec(f.get("amount_rub"), "amount_rub")
        fx = await _resolve_fx(session, _dec(f.get("fx_rate")))
        amount_usdt = amount_rub / fx
        p = await prepayment_repo.create_pending(
            session,
            supplier=f.get("supplier"),
            amount_rub=amount_rub,
            amount_usdt=amount_usdt,
            fx_rate=fx,
            expected_cabinets=f.get("expected_cabinets"),
            notes=op.summary,
        )
        await _audit(session, user_id, "create", "prepayments", p.id, new=f)
        return f"✅ Предоплата #{p.id} {f.get('supplier') or '?'} записана."

    if intent == Intent.CLIENT_PAYOUT.value:
        client_name = str(f.get("client_name") or "").strip()
        client = await client_repo.get_by_name(session, client_name)
        if client is None:
            raise ApplyError(f"Нет такого клиента: {client_name}")
        # Mark unpaid POA for this client as paid (latest one)
        from sqlalchemy import select

        from src.db.models import PoAWithdrawal

        res = await session.execute(
            select(PoAWithdrawal)
            .where(
                PoAWithdrawal.client_id == client.id,
                PoAWithdrawal.client_paid.is_(False),
            )
            .order_by(PoAWithdrawal.id.desc())
            .limit(1)
        )
        poa = res.scalar_one_or_none()
        if poa is None:
            raise ApplyError(f"У {client_name} нет открытых долгов.")
        await poa_repo.mark_client_paid(session, poa.id)
        await _audit(
            session, user_id, "client_paid", "poa_withdrawals", poa.id,
            new={"amount_usdt": str(f.get("amount_usdt"))},
        )
        return f"✅ Долг перед {client_name} закрыт."

    if intent == Intent.KNOWLEDGE_TEACH.value:
        category = str(f.get("category") or "rule").lower()
        allowed = ("alias", "glossary", "entity", "rule", "pattern", "preference")
        if category not in allowed:
            category = "rule"
        content = str(f.get("content") or "").strip()
        key = f.get("key")
        if len(content) < 2:
            raise ApplyError("Пустой факт, записывать нечего.")
        fact = await kb_repo.add_fact(
            session,
            category=category,
            key=str(key).strip() if key else None,
            content=content,
            confidence="confirmed",
            created_by_user_id=user_id,
        )
        await _audit(session, user_id, "create", "knowledge_base", fact.id, new=f)
        key_part = f" [{fact.key}]" if fact.key else ""
        return f"✅ Запомнил #{fact.id} ({fact.category}){key_part}: {fact.content[:140]}"

    raise ApplyError(f"Intent {intent} пока не реализован на запись.")


async def _audit(
    session: AsyncSession,
    user_id: int | None,
    action: str,
    table: str,
    record_id: int,
    *,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
) -> None:
    await audit_repo.log(
        session,
        user_id=user_id,
        action=action,
        table_name=table,
        record_id=record_id,
        old_data=old,
        new_data=new,
    )
