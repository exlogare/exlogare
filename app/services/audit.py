from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    action: str,
    actor: str | None = None,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    log = AuditLog(
        tenant_id=tenant_id,
        action=action,
        actor=actor,
        target=target,
        meta=meta or {},
    )
    session.add(log)
    return log
