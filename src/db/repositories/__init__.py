"""Repository layer (data-access methods).

Repositories are plain async functions that take a session as their first
argument — no dependency-injection magic, no classes, no ORM-object mutation
outside the transaction scope.
"""

from src.db.repositories import (
    audit,
    balances,
    cabinets,
    clients,
    exchanges,
    expenses,
    feedback,
    fx,
    knowledge,
    partner_ops,
    poa,
    prepayments,
    snapshots,
    stickers,
    users,
    wallets,
)

__all__ = [
    "audit",
    "balances",
    "cabinets",
    "clients",
    "exchanges",
    "expenses",
    "feedback",
    "fx",
    "knowledge",
    "partner_ops",
    "poa",
    "prepayments",
    "snapshots",
    "stickers",
    "users",
    "wallets",
]
