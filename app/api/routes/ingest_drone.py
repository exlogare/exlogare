"""Drone CI / Woodpecker CI ingest: clients POST the failed step log inline."""
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


_DRONE_STATUSES: frozenset[str] = frozenset(
    {
        "success",
        "failure",
        "failed",
        "error",
        "errored",
        "killed",
        "skipped",
        "blocked",
        "declined",
        "running",
        "pending",
    }
)


class DroneIngestRequest(BaseModel):
    repo: str = Field(
        min_length=3,
        max_length=512,
        description="Repository slug, e.g. 'myorg/myrepo' (DRONE_REPO).",
    )
    build_number: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=32)
    log: str = Field(min_length=1)
    pipeline: str | None = Field(default=None, max_length=128)
    step: str | None = Field(default=None, max_length=128)
    branch: str | None = Field(default=None, max_length=256)
    commit_sha: str | None = Field(default=None, max_length=64)
    build_url: HttpUrl | None = None


class DroneIngestResponse(BaseModel):
    status: str
    analysis_id: str


@router.post(
    "/drone",
    response_model=DroneIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_drone(
    body: DroneIngestRequest,
    principal: CurrentPrincipal = Depends(require_scope("ingest")),
    session: AsyncSession = Depends(get_db),
) -> DroneIngestResponse:
    normalized_status = body.status.strip().lower()
    if normalized_status not in _DRONE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Drone status: {body.status!r}",
        )

    if len(body.log.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")

    try:
        await check_rate_limit(
            f"tenant:{principal.tenant.id}:ingest_drone",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ci_run_id = f"{body.repo}#{body.build_number}"
    pipeline_url = str(body.build_url) if body.build_url is not None else None

    event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="drone",
        source="drone_ingest",
        ci_run_id=ci_run_id,
        ci_job_id=body.step or body.pipeline,
        project_id=body.repo,
        project_path=body.repo,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        ref=body.branch,
        sha=body.commit_sha,
        status=normalized_status,
        raw={
            "repo": body.repo,
            "build_number": body.build_number,
            "pipeline": body.pipeline,
            "step": body.step,
            "status": normalized_status,
            "branch": body.branch,
            "commit_sha": body.commit_sha,
            "build_url": pipeline_url,
        },
    )

    await persist_ingestion_event(session, event)
    analysis_row = await run_external_ingest(session, event, body.log)
    await session.commit()

    return DroneIngestResponse(
        status="accepted",
        analysis_id=str(analysis_row.id),
    )
