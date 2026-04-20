"""Signature-based dedup inside the pending_ops registry."""

from __future__ import annotations

from src.core.pending_ops import _signature_fields


def test_prepayment_signature_canonicalises_case() -> None:
    a = _signature_fields(
        "prepayment_given",
        {"supplier": "Карен", "amount_rub": 25000},
    )
    b = _signature_fields(
        "prepayment_given",
        {"supplier": "карен  ", "amount_rub": 25000},
    )
    assert a == b


def test_prepayment_signature_differs_on_amount() -> None:
    a = _signature_fields(
        "prepayment_given", {"supplier": "Карен", "amount_rub": 25000}
    )
    b = _signature_fields(
        "prepayment_given", {"supplier": "Карен", "amount_rub": 50000}
    )
    assert a != b


def test_cabinet_in_use_signature() -> None:
    a = _signature_fields("cabinet_in_use", {"name_or_code": "Даут"})
    b = _signature_fields("cabinet_in_use", {"name_or_code": "  ДАУТ"})
    assert a == b


def test_cabinet_in_use_differs_by_name() -> None:
    a = _signature_fields("cabinet_in_use", {"name_or_code": "Даут"})
    b = _signature_fields("cabinet_in_use", {"name_or_code": "Анатолий"})
    assert a != b


def test_unlisted_intent_returns_none() -> None:
    # unknown / meta intents don't get deduped (safer default).
    assert _signature_fields("mystery_intent", {"x": 1}) is None


def test_knowledge_teach_signature_by_content() -> None:
    a = _signature_fields(
        "knowledge_teach",
        {"category": "rule", "content": "эквайринг 5к ежедневно"},
    )
    b = _signature_fields(
        "knowledge_teach",
        {"category": "rule", "content": "эквайринг 5к ежедневно"},
    )
    assert a == b
