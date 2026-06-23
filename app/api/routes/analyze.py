from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.models.ci_connection import CIConnection
from app.schemas.failure_event import FailureEvent
from app.services.pipeline import persist_ingestion_event

router = APIRouter(tags=["analyze"])


class AnalyzeRequest(BaseModel):
    tenant_id: uuid.UUID
    ci_connection_id: uuid.UUID
    provider: str = "gitlab"
    ci_run_id: str
    ci_job_id: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    pipeline_url: str | None = None
    job_url: str | None = None
    mr_iid: str | None = None
    sha: str | None = None
    source: str = Field(default="manual")


@router.post("/analyze", status_code=202)
async def analyze(
    req: AnalyzeRequest, session: AsyncSession = Depends(get_db)
) -> dict:
    """Manually enqueue an analysis request for a specific CI run."""
    try:
        await check_rate_limit(f"tenant:{req.tenant_id}:analyze", limit=60)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(
        select(CIConnection).where(
            CIConnection.id == req.ci_connection_id,
            CIConnection.tenant_id == req.tenant_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="CI connection not found for tenant")

    event = FailureEvent(
        tenant_id=req.tenant_id,
        ci_connection_id=connection.id,
        provider=req.provider,  # type: ignore[arg-type]
        source=req.source,  # type: ignore[arg-type]
        ci_run_id=req.ci_run_id,
        ci_job_id=req.ci_job_id,
        project_id=req.project_id or connection.external_project_id,
        project_path=req.project_path or connection.external_project_name,
        pipeline_url=req.pipeline_url,
        job_url=req.job_url,
        mr_iid=req.mr_iid,
        sha=req.sha,
    )
    created = await persist_ingestion_event(session, event)
    await session.commit()

    if created is not None:
        from app.workers.tasks import analyze_failure

        analyze_failure.delay(event.model_dump(mode="json"))

    return {
        "status": "accepted" if created else "deduped",
        "ci_run_id": event.ci_run_id,
    }
