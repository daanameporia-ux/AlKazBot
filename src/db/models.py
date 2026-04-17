"""SQLAlchemy ORM models — mirrors the schema in sber26-bot-SPEC.md §"Модель данных".

All amounts are stored as NUMERIC(18,6) for money-safe arithmetic.
Timestamps are TIMESTAMPTZ (Postgres), dates are DATE.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# --------------------------------------------------------------------------- #
# People
# --------------------------------------------------------------------------- #


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="partner")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('partner','assistant','viewer')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    tg_username: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="viewer")
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    partner: Mapped[Partner | None] = relationship(back_populates="users")


# --------------------------------------------------------------------------- #
# Wallets & snapshots
# --------------------------------------------------------------------------- #


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("currency IN ('RUB','USDT')", name="ck_wallets_currency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WalletSnapshot(Base):
    __tablename__ = "wallet_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE")
    )
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    amount_native: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cabinets_worked: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    acquiring_today: Mapped[bool | None] = mapped_column(Boolean)
    total_wallets: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    raw_output: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Cabinets & prepayments
# --------------------------------------------------------------------------- #


class Prepayment(Base):
    __tablename__ = "prepayments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','fulfilled','partial','cancelled')",
            name="ck_prepayments_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    supplier: Mapped[str | None] = mapped_column(Text)
    given_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_cabinets: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Cabinet(Base):
    __tablename__ = "cabinets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_stock','in_use','worked_out','blocked','recovered','lost')",
            name="ck_cabinets_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    auto_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    cost_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cost_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    prepayment_id: Mapped[int | None] = mapped_column(ForeignKey("prepayments.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="in_stock")
    in_use_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worked_out_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Clients & POA withdrawals
# --------------------------------------------------------------------------- #


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PoAWithdrawal(Base):
    __tablename__ = "poa_withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    client_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    partner_shares: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    client_debt_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    client_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    client_paid_date: Mapped[date | None] = mapped_column(Date)
    withdrawal_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Partner money flows
# --------------------------------------------------------------------------- #


class PartnerContribution(Base):
    __tablename__ = "partner_contributions"
    __table_args__ = (
        CheckConstraint(
            "source IN ('initial_depo','poa_share','manual')",
            name="ck_partner_contributions_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref_id: Mapped[int | None] = mapped_column(Integer)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    contribution_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerWithdrawal(Base):
    __tablename__ = "partner_withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    withdrawal_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_wallet_id: Mapped[int | None] = mapped_column(ForeignKey("wallets.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Exchanges & FX
# --------------------------------------------------------------------------- #


class Exchange(Base):
    __tablename__ = "exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exchange_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_input: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class FxRateSnapshot(Base):
    __tablename__ = "fx_rates_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_ccy: Mapped[str] = mapped_column(Text, nullable=False)
    to_ccy: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    rate_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_exchange_id: Mapped[int | None] = mapped_column(ForeignKey("exchanges.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Learning: knowledge base + few-shot + feedback + message log
# --------------------------------------------------------------------------- #


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (
        CheckConstraint(
            "category IN ('entity','rule','pattern','preference','glossary','alias')",
            name="ck_knowledge_base_category",
        ),
        CheckConstraint(
            "confidence IN ('confirmed','inferred','tentative')",
            name="ck_knowledge_base_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str | None] = mapped_column(Text, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False, default="tentative")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class FewShotExample(Base):
    __tablename__ = "few_shot_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MessageLog(Base):
    __tablename__ = "message_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(Text)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_mention: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    intent_detected: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','noted','in_progress','done','wontdo')",
            name="ck_feedback_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    context: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int | None] = mapped_column(Integer)
    old_data: Mapped[Any | None] = mapped_column(JSONB)
    new_data: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PendingReminder(Base):
    __tablename__ = "pending_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reminder_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[Any | None] = mapped_column(JSONB)


__all__ = [
    "AuditLog",
    "Base",
    "Cabinet",
    "Client",
    "Exchange",
    "Expense",
    "Feedback",
    "FewShotExample",
    "FxRateSnapshot",
    "KnowledgeBase",
    "MessageLog",
    "Partner",
    "PartnerContribution",
    "PartnerWithdrawal",
    "PendingReminder",
    "PoAWithdrawal",
    "Prepayment",
    "Report",
    "User",
    "Wallet",
    "WalletSnapshot",
]
