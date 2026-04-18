"""Audit log writes — every confirmed bot-driven mutation records a row."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog


async def log(
    session: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    table_name: str,
    record_id: int | None,
    old_data: dict[str, Any] | None = None,
    new_data: dict[str, Any] | None = None,
) -> AuditLog:
    a = AuditLog(
        user_id=user_id,
        action=action,
        table_name=table_name,
        record_id=record_id,
        old_data=old_data,
        new_data=new_data,
    )
    session.add(a)
    await session.flush()
    return a
