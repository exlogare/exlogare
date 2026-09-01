"""Jenkins ingest: clients POST the full console log inline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_scope
from app.core.logging import get_logger
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.schemas.failure_event import FailureEvent
from app.services.pipeline import (
    persist_ingestion_event,
    run_external_ingest,
)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
log = get_logger(__name__)

MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MiB hard cap before we even parse

_JENKINS_STATUSES: frozenset[str] = frozenset(
    {"SUCCESS", "FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"}
)


class JenkinsIngestRequest(BaseModel):
    job: str = Field(min_length=1, max_length=512)
    build_number: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=32)
    log: str = Field(min_length=1)
    project: str | None = Field(default=None, max_length=512)
    build_url: HttpUrl | None = None


class JenkinsIngestResponse(BaseModel):
    status: str
    analysis_id: str


@router.post(
    "/jenkins",
    response_model=JenkinsIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_jenkins(
    body: JenkinsIngestRequest,
    principal: CurrentPrincipal = Depends(require_scope("ingest")),
    session: AsyncSession = Depends(get_db),
) -> JenkinsIngestResponse:
    normalized_status = body.status.strip().upper()
    if normalized_status not in _JENKINS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Jenkins status: {body.status!r}",
        )

    if len(body.log.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")

    try:
        await check_rate_limit(
            f"tenant:{principal.tenant.id}:ingest_jenkins",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ci_run_id = f"{body.job}#{body.build_number}"
    pipeline_url = str(body.build_url) if body.build_url is not None else None

    event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="jenkins",
        source="jenkins_ingest",
        ci_run_id=ci_run_id,
        project_id=body.project,
        project_path=body.project,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        status=normalized_status.lower(),
        raw={
            "job": body.job,
            "build_number": body.build_number,
            "status": normalized_status,
            "project": body.project,
            "build_url": pipeline_url,
        },
    )

    await persist_ingestion_event(session, event)

    analysis_row = await run_external_ingest(session, event, body.log)

    await session.commit()

    return JenkinsIngestResponse(
        status="accepted",
        analysis_id=str(analysis_row.id),
    )
