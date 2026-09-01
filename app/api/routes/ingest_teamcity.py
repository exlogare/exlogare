"""TeamCity ingest: clients POST the failed build log inline."""
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

MAX_LOG_BYTES = 10 * 1024 * 1024


_TEAMCITY_STATUSES: frozenset[str] = frozenset(
    {"success", "failure", "failed", "error", "canceled", "cancelled", "running"}
)


class TeamCityIngestRequest(BaseModel):
    build_type_id: str = Field(min_length=1, max_length=256)
    build_id: int = Field(ge=0)
    build_number: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    log: str = Field(min_length=1)
    branch: str | None = Field(default=None, max_length=256)
    commit_sha: str | None = Field(default=None, max_length=64)
    build_url: HttpUrl | None = None
    project_name: str | None = Field(default=None, max_length=256)


class TeamCityIngestResponse(BaseModel):
    status: str
    analysis_id: str


@router.post(
    "/teamcity",
    response_model=TeamCityIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_teamcity(
    body: TeamCityIngestRequest,
    principal: CurrentPrincipal = Depends(require_scope("ingest")),
    session: AsyncSession = Depends(get_db),
) -> TeamCityIngestResponse:
    normalized_status = body.status.strip().lower()
    if normalized_status not in _TEAMCITY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown TeamCity status: {body.status!r}",
        )

    if len(body.log.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")

    try:
        await check_rate_limit(
            f"tenant:{principal.tenant.id}:ingest_teamcity",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ci_run_id = f"{body.build_type_id}#{body.build_id}"
    pipeline_url = str(body.build_url) if body.build_url is not None else None

    event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="teamcity",
        source="teamcity_ingest",
        ci_run_id=ci_run_id,
        ci_job_id=str(body.build_id),
        project_id=body.project_name or body.build_type_id,
        project_path=body.project_name or body.build_type_id,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        ref=body.branch,
        sha=body.commit_sha,
        status=normalized_status,
        raw={
            "build_type_id": body.build_type_id,
            "build_id": body.build_id,
            "build_number": body.build_number,
            "project_name": body.project_name,
            "status": normalized_status,
            "branch": body.branch,
            "commit_sha": body.commit_sha,
            "build_url": pipeline_url,
        },
    )

    await persist_ingestion_event(session, event)
    analysis_row = await run_external_ingest(session, event, body.log)
    await session.commit()

    return TeamCityIngestResponse(
        status="accepted",
        analysis_id=str(analysis_row.id),
    )
