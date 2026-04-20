"""Apply a confirmed operation: dispatch to the right repository + audit.

Called from the confirm callback handler after the user presses ✅. Each
intent has its own sub-applier that translates the LLM-extracted `fields`
dict into concrete repo calls.

Applier functions MUST be idempotent per pending-op uid (the registry pops
on confirm, so double-press is a no-op), and MUST write a row to
`audit_log` describing what they did.
"""

from __future__ import annotations

import re as _re
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
    few_shot as few_shot_repo,
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


async def _record_verified(
    session: AsyncSession, op: PendingOp
) -> None:
    """After a successful apply, save the pairing as a verified few-shot
    example so future analyses can cite it.
    """
    # Skip meta-intents — only business ops accumulate training material.
    if op.intent in ("knowledge_teach", "chat", "question", "unclear", "feedback"):
        return
    try:
        await few_shot_repo.add_verified(
            session,
            intent=op.intent,
            input_text=op.summary or "",
            parsed_json=op.fields,
        )
    except Exception:
        log.exception("few_shot_save_failed", intent=op.intent)


async def apply(
    session: AsyncSession, op: PendingOp, *, created_by_tg_id: int
) -> str:
    """Return a short Russian confirmation line for the user."""
    me = await user_repo.get_user_by_tg_id(session, created_by_tg_id)
    user_id = me.id if me else None
    intent = op.intent
    f = op.fields

    if intent == Intent.EXCHANGE.value:
        amount_rub = _req_dec(f.get("amount_rub"), "amount_rub")
        amount_usdt = _req_dec(f.get("amount_usdt"), "amount_usdt")
        fx_rate = _positive_fx(_req_dec(f.get("fx_rate"), "fx_rate"))
        if amount_rub <= 0 or amount_usdt <= 0:
            raise ApplyError(
                f"Обмен с нулевой/отрицательной суммой: "
                f"{amount_rub}₽ / {amount_usdt} USDT. Не записываю."
            )
        # Classic mix-up: amount_usdt and fx_rate swapped. For RUB/USDT
        # the rate is always double-digit (60-120), amount_usdt is much
        # larger. Refuse before we write garbage.
        if amount_usdt < fx_rate:
            raise ApplyError(
                f"Похоже amount_usdt и fx_rate поменяли местами: "
                f"{amount_rub}/{amount_usdt}={fx_rate}. "
                "Проверь — USDT должен быть >> курса."
            )
        # Math check: amount_rub / fx_rate ≈ amount_usdt, ±0.5%.
        expected_usdt = amount_rub / fx_rate
        diff_pct = (
            abs(expected_usdt - amount_usdt) / amount_usdt * Decimal("100")
            if amount_usdt
            else Decimal("0")
        )
        if diff_pct > Decimal("0.5"):
            raise ApplyError(
                f"Арифметика не сходится: {amount_rub}₽ / {fx_rate} = "
                f"{expected_usdt:.2f} USDT, в операции {amount_usdt} "
                f"(расхождение {diff_pct:.2f}%). "
                "Перепроверь числа и пришли заново."
            )
        ex = await exchange_repo.create(
            session,
            amount_rub=amount_rub,
            amount_usdt=amount_usdt,
            fx_rate=fx_rate,
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
        if (amount_rub is not None and amount_rub < 0) or amount_usdt < 0:
            raise ApplyError(
                "Расход с отрицательной суммой. "
                "Если это возврат — сообщи иначе, не как expense."
            )
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
        client_share_pct = _req_dec(f.get("client_share_pct"), "client_share_pct")
        if client_share_pct < 0 or client_share_pct > 100:
            raise ApplyError(
                f"Доля клиента {client_share_pct}% вне диапазона 0-100. Проверь."
            )
        amount_rub = _req_dec(f.get("amount_rub"), "amount_rub")
        if amount_rub <= 0:
            raise ApplyError(
                f"Сумма снятия не положительная: {amount_rub}. Не записываю."
            )
        partner_shares_raw = list(f.get("partner_shares") or [])

        # Pass 1: shape validation (name non-empty, pct > 0) and sum check.
        # Done before partner-existence lookup so the sum error message —
        # which is the most common user mistake — shows up first.
        clean_shares: list[dict[str, Any]] = []
        total_share = Decimal("0")
        for s in partner_shares_raw:
            pname = str(s.get("partner") or "").strip()
            pct = _dec(s.get("pct"))
            if not pname or pct is None or pct <= 0:
                raise ApplyError(
                    f"Некорректная доля партнёра: {s!r}. "
                    "Пример: {'partner': 'Казах', 'pct': 20}."
                )
            clean_shares.append({"partner": pname, "pct": str(pct)})
            total_share += pct
        expected_sum = Decimal("100") - client_share_pct
        # Allow 0.5% tolerance to not choke on rounding.
        if abs(total_share - expected_sum) > Decimal("0.5"):
            raise ApplyError(
                f"Доли партнёров {total_share}% + клиент {client_share_pct}% "
                f"= {total_share + client_share_pct}%, должно быть 100%. "
                "Уточни и повтори."
            )

        # Pass 2: partner-in-DB check. If a share references a partner
        # that doesn't exist, attach_exchange would silently drop their
        # contribution later — fail loudly here instead.
        for sh in clean_shares:
            p = await partner_repo.resolve_partner(session, sh["partner"])
            if p is None:
                raise ApplyError(
                    f"Партнёр «{sh['partner']}» не в базе. Добавь через "
                    "`/knowledge add Партнёр ...` или уточни имя."
                )
            sh["partner"] = p.name  # canonical form

        c = await client_repo.get_or_create(session, client_name)
        poa = await poa_repo.create_pending(
            session,
            client_id=c.id,
            amount_rub=amount_rub,
            partner_shares=clean_shares,
            client_share_pct=client_share_pct,
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

    if intent == Intent.CABINET_RECOVERED.value:
        key = str(f.get("name_or_code") or "").strip()
        cab = await cabinet_repo.find_by_name_or_code(session, key)
        if cab is None:
            raise ApplyError(f"Не нашёл кабинет: {key}")
        await cabinet_repo.set_status(session, cab.id, "recovered")
        await _audit(
            session, user_id, "status_change", "cabinets", cab.id,
            old={"status": cab.status}, new={"status": "recovered"},
        )
        return f"✅ Кабинет {cab.name or cab.auto_code} восстановлен."

    if intent == Intent.PREPAYMENT_FULFILLED.value:
        # Matches "Миша отдал 4 кабинета: Аляс 25k, Боб 20k..." — creates
        # cabinets and closes the referenced prepayment if sums match.
        supplier = str(f.get("supplier") or "").strip() or None
        cabinets_in = f.get("cabinets") or []
        if not cabinets_in:
            raise ApplyError("Список кабинетов пустой.")

        fx = await _resolve_fx(session, _dec(f.get("fx_rate")))

        prep = None
        if supplier:
            prep = await prepayment_repo.find_open_by_supplier(session, supplier)

        total_rub = Decimal("0")
        created_ids: list[int] = []
        for c in cabinets_in:
            cost_rub = _req_dec(c.get("cost_rub"), "cabinet.cost_rub")
            cost_usdt = cost_rub / fx
            cab = await cabinet_repo.create(
                session,
                name=c.get("name"),
                cost_rub=cost_rub,
                cost_usdt=cost_usdt,
                fx_rate=fx,
                prepayment_id=prep.id if prep else None,
            )
            total_rub += cost_rub
            created_ids.append(cab.id)

        summary = f"✅ Приняты {len(cabinets_in)} кабинета(ов) от {supplier or '?'}."
        if prep is not None:
            prep_amount = Decimal(prep.amount_rub)
            diff = total_rub - prep_amount
            if abs(diff) < Decimal("1"):
                await prepayment_repo.set_status(session, prep.id, "fulfilled")
                summary += f" Предоплата #{prep.id} закрыта ровно."
            else:
                await prepayment_repo.set_status(
                    session, prep.id, "partial" if total_rub < prep_amount else "fulfilled"
                )
                summary += (
                    f" ⚠️ Сумма кабинетов {total_rub:.0f}₽ ≠ предоплате "
                    f"{prep_amount:.0f}₽ (разница {diff:+.0f}₽)."
                )
        await _audit(
            session, user_id, "create", "cabinets", created_ids[0] if created_ids else 0,
            new={"supplier": supplier, "count": len(cabinets_in), "total_rub": str(total_rub)},
        )
        return summary

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
        # Fallback: if the preference obviously describes "respond to word X"
        # and we didn't route through wakeword_add for some reason, mirror
        # any extracted words into trigger_keywords so the wake-up isn't
        # silently a no-op.
        mirrored = await _mirror_wakewords_from_preference(session, fact)
        key_part = f" [{fact.key}]" if fact.key else ""
        mirrored_part = (
            f"  (+trigger-слова: {', '.join(mirrored)})" if mirrored else ""
        )
        return (
            f"✅ Запомнил #{fact.id} ({fact.category}){key_part}: "
            f"{fact.content[:140]}{mirrored_part}"
        )

    if intent == Intent.WAKEWORD_ADD.value:
        word = str(f.get("word") or "").strip().lower()
        if len(word) < 3:
            raise ApplyError(
                f"Триггер-слово «{word}» слишком короткое (минимум 3 символа). "
                "Давай развёрнутее — напиши какое слово добавить."
            )
        from src.core.keyword_match import invalidate as invalidate_kw_cache
        from src.db.repositories import keywords as keyword_repo

        kw_row = await keyword_repo.add(
            session,
            keyword=word,
            created_by_user_id=user_id,
            notes="добавлено через wakeword_add intent",
        )
        # Also persist as a preference so it shows up in `/knowledge`.
        kb_fact = await kb_repo.add_fact(
            session,
            category="preference",
            content=f"Откликаться на «{word}» наравне с остальными триггер-словами",
            confidence="confirmed",
            created_by_user_id=user_id,
        )
        await _audit(
            session,
            user_id,
            "create",
            "trigger_keywords",
            kw_row.id,
            new={"keyword": word, "source": "wakeword_add", "kb_fact_id": kb_fact.id},
        )
        await invalidate_kw_cache()
        return f"✅ Буду откликаться на «{word}». Попробуй, скажи в чате."

    raise ApplyError(f"Intent {intent} пока не реализован на запись.")


# --------------------------------------------------------------------------- #
# Fallback: extract wake-words from a freshly-written preference fact.
# Matches phrasings like "Откликаться на 'пёс' ...", "отвечать на слово шавка",
# etc. Works off the normalised content so Claude-phrasing variance doesn't
# break us.
# --------------------------------------------------------------------------- #

_WAKEWORD_TRIGGER = _re.compile(
    r"(?:отклик[а-я]*|отзыв[а-я]*|реагир[а-я]*|зови[а-я]*|отвеч[а-я]*|обращ[а-я]*)"
    r"\s+(?:на|меня|мне|когда)\s+"
    r"[«\"'‘”]?([а-яёА-ЯЁ][а-яёА-ЯЁ\-]{2,24})[»\"'’”]?",
    flags=_re.IGNORECASE,
)


async def _mirror_wakewords_from_preference(
    session: AsyncSession, fact: Any
) -> list[str]:
    """If the preference fact reads like 'откликаться на X', push X into
    trigger_keywords so the keyword-matcher actually picks it up. Returns
    the list of words mirrored (may be empty)."""
    if getattr(fact, "category", None) != "preference":
        return []
    content = getattr(fact, "content", "") or ""
    matches = _WAKEWORD_TRIGGER.findall(content)
    if not matches:
        return []
    from src.core.keyword_match import invalidate as invalidate_kw_cache
    from src.db.repositories import keywords as keyword_repo

    pushed: list[str] = []
    for word in matches:
        w = word.strip().lower()
        if len(w) < 3:
            continue
        try:
            await keyword_repo.add(
                session,
                keyword=w,
                notes=f"mirrored from KB preference #{fact.id}",
            )
            pushed.append(w)
        except ValueError:
            # Too short or similar — skip silently.
            continue
    if pushed:
        await invalidate_kw_cache()
    return pushed


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
