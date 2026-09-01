from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_event import UsageEvent


class UsageStatsService:
    async def record_run(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        ci_run_id: str,
        ci_job_id: str = "",
        kind: str = "analysis",
    ) -> UsageEvent:
        event = UsageEvent(
            tenant_id=tenant_id,
            provider=provider,
            ci_run_id=ci_run_id,
            ci_job_id=ci_job_id or "",
            kind=kind,
        )
        session.add(event)
        await session.flush()
        return event
