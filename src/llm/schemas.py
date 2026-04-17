"""Pydantic schemas for LLM-parsed operations. Extended on Stages 1-2."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    POA_WITHDRAWAL = "poa_withdrawal"
    EXCHANGE = "exchange"
    CABINET_PURCHASE = "cabinet_purchase"
    CABINET_WORKED_OUT = "cabinet_worked_out"
    CABINET_BLOCKED = "cabinet_blocked"
    CABINET_RECOVERED = "cabinet_recovered"
    PREPAYMENT_GIVEN = "prepayment_given"
    PREPAYMENT_FULFILLED = "prepayment_fulfilled"
    EXPENSE = "expense"
    PARTNER_WITHDRAWAL = "partner_withdrawal"
    PARTNER_DEPOSIT = "partner_deposit"
    CLIENT_PAYOUT = "client_payout"
    WALLET_SNAPSHOT = "wallet_snapshot"
    QUESTION = "question"
    FEEDBACK = "feedback"
    KNOWLEDGE_TEACH = "knowledge_teach"
    CHAT = "chat"
    UNCLEAR = "unclear"


class ClassifiedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class PartnerShare(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner: str
    pct: Decimal = Field(ge=0, le=100)


class PoAWithdrawalParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_name: str
    amount_rub: Decimal
    partner_shares: list[PartnerShare]
    client_share_pct: Decimal = Field(ge=0, le=100)
    notes: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)


class ExchangeParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_rub: Decimal
    amount_usdt: Decimal
    fx_rate: Decimal
    raw_input: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
