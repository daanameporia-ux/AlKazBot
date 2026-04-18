"""In-memory registry of operations awaiting user confirmation.

When the batch analyzer produces candidates, we render a preview card with
✅ / ❌ inline buttons. The operation is parked here under a short uuid and
tied to callback_data strings of the form `confirm:<uuid>` / `cancel:<uuid>`.

Entries TTL out after 30 minutes so stale previews don't pile up.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from src.logging_setup import get_logger

log = get_logger(__name__)


ENTRY_TTL_SEC = 30 * 60  # 30 minutes


@dataclass(slots=True)
class PendingOp:
    uid: str
    chat_id: int
    preview_message_id: int | None
    intent: str
    fields: dict[str, Any]
    summary: str
    source_message_ids: list[int]
    created_by_tg_id: int
    created_at: float


class PendingOpsRegistry:
    def __init__(self) -> None:
        self._store: dict[str, PendingOp] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        chat_id: int,
        intent: str,
        fields: dict[str, Any],
        summary: str,
        source_message_ids: list[int],
        created_by_tg_id: int,
    ) -> PendingOp:
        uid = uuid.uuid4().hex[:10]
        entry = PendingOp(
            uid=uid,
            chat_id=chat_id,
            preview_message_id=None,
            intent=intent,
            fields=fields,
            summary=summary,
            source_message_ids=source_message_ids,
            created_by_tg_id=created_by_tg_id,
            created_at=time.time(),
        )
        async with self._lock:
            self._store[uid] = entry
            self._evict_stale_locked()
        return entry

    async def attach_preview(self, uid: str, preview_message_id: int) -> None:
        async with self._lock:
            entry = self._store.get(uid)
            if entry is not None:
                entry.preview_message_id = preview_message_id

    async def pop(self, uid: str) -> PendingOp | None:
        async with self._lock:
            return self._store.pop(uid, None)

    async def peek(self, uid: str) -> PendingOp | None:
        async with self._lock:
            return self._store.get(uid)

    def _evict_stale_locked(self) -> None:
        now = time.time()
        stale = [
            k for k, v in self._store.items() if now - v.created_at > ENTRY_TTL_SEC
        ]
        for k in stale:
            log.info("pending_op_expired", uid=k)
            self._store.pop(k, None)


_registry: PendingOpsRegistry | None = None


def get_registry() -> PendingOpsRegistry:
    global _registry
    if _registry is None:
        _registry = PendingOpsRegistry()
    return _registry
