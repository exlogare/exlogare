"""CircleCI ingest: clients POST the failed job log inline."""
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


_CIRCLECI_STATUSES: frozenset[str] = frozenset(
    {
        "success",
        "failed",
        "failing",
        "error",
        "errored",
        "canceled",
        "cancelled",
        "timedout",
        "unauthorized",
        "blocked",
        "running",
    }
)


class CircleCIIngestRequest(BaseModel):
    project_slug: str = Field(
        min_length=3,
        max_length=512,
        description="CircleCI project slug, e.g. 'gh/myorg/myrepo'.",
    )
    workflow_id: str = Field(min_length=1, max_length=128)
    job_name: str = Field(min_length=1, max_length=256)
    job_number: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=32)
    log: str = Field(min_length=1)
    branch: str | None = Field(default=None, max_length=256)
    commit_sha: str | None = Field(default=None, max_length=64)
    pipeline_url: HttpUrl | None = None
    workflow_name: str | None = Field(default=None, max_length=128)


class CircleCIIngestResponse(BaseModel):
    status: str
    analysis_id: str


@router.post(
    "/circleci",
    response_model=CircleCIIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_circleci(
    body: CircleCIIngestRequest,
    principal: CurrentPrincipal = Depends(require_scope("ingest")),
    session: AsyncSession = Depends(get_db),
) -> CircleCIIngestResponse:
    normalized_status = body.status.strip().lower()
    if normalized_status not in _CIRCLECI_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown CircleCI status: {body.status!r}",
        )

    if len(body.log.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")

    try:
        await check_rate_limit(
            f"tenant:{principal.tenant.id}:ingest_circleci",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ci_run_id = f"{body.workflow_id}#{body.job_number}"
    pipeline_url = str(body.pipeline_url) if body.pipeline_url is not None else None

    event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="circleci",
        source="circleci_ingest",
        ci_run_id=ci_run_id,
        ci_job_id=str(body.job_number) if body.job_number else None,
        project_id=body.project_slug,
        project_path=body.project_slug,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        ref=body.branch,
        sha=body.commit_sha,
        status=normalized_status,
        # Routing metadata only — never the log. Verified by tests.
        raw={
            "project_slug": body.project_slug,
            "workflow_id": body.workflow_id,
            "workflow_name": body.workflow_name,
            "job_name": body.job_name,
            "job_number": body.job_number,
            "status": normalized_status,
            "branch": body.branch,
            "commit_sha": body.commit_sha,
            "pipeline_url": pipeline_url,
        },
    )

    await persist_ingestion_event(session, event)
    analysis_row = await run_external_ingest(session, event, body.log)
    await session.commit()

    return CircleCIIngestResponse(
        status="accepted",
        analysis_id=str(analysis_row.id),
    )
