"""Retention cleanup: drop analysis/usage history older than RETENTION_DAYS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.models.analysis_result import AnalysisResult
from app.models.ingestion_event import IngestionEvent
from app.models.tenant import Tenant
from app.workers._async import run_async, worker_session_scope

configure_logging()
log = get_logger("retention")


@celery_app.task(name="app.workers.retention.enforce_retention", acks_late=True)
def enforce_retention() -> dict:
    return run_async(_enforce_retention)


async def _enforce_retention() -> dict:
    settings = get_settings()
    days = int(settings.retention_days)
    if days <= 0:
        return {"tenants": 0, "analysis_results": 0, "ingestion_events": 0}

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=days)
    cleaned: dict[str, int] = {"tenants": 0, "analysis_results": 0, "ingestion_events": 0}
    async with worker_session_scope() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
        for tenant in tenants:
            res1 = await session.execute(
                delete(AnalysisResult).where(
                    AnalysisResult.tenant_id == tenant.id,
                    AnalysisResult.created_at < cutoff,
                )
            )
            res2 = await session.execute(
                delete(IngestionEvent).where(
                    IngestionEvent.tenant_id == tenant.id,
                    IngestionEvent.received_at < cutoff,
                )
            )
            cleaned["tenants"] += 1
            cleaned["analysis_results"] += int(res1.rowcount or 0)
            cleaned["ingestion_events"] += int(res2.rowcount or 0)
    log.info("retention.cycle_done", **cleaned)
    return cleaned
