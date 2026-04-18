"""Repository layer (data-access methods).

Repositories are plain async functions that take a session as their first
argument — no dependency-injection magic, no classes, no ORM-object mutation
outside the transaction scope.
"""

from src.db.repositories import fx, knowledge, users, wallets

__all__ = ["fx", "knowledge", "users", "wallets"]
