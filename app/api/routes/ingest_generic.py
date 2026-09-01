"""Generic CI ingest: catch-all for everything we don't have a dedicated route for."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, field_validator
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


_KNOWN_STATUSES: frozenset[str] = frozenset(
    {
        "success",
        "passed",
        "ok",
        "failed",
        "failure",
        "error",
        "errored",
        "cancelled",
        "canceled",
        "timeout",
        "timedout",
        "aborted",
        "killed",
        "running",
        "pending",
        "queued",
        "skipped",
        "blocked",
    }
)


_PROVIDER_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
_PROVIDER_BAD_CHARS_RE = re.compile(r"[^a-z0-9_.\-]+")
_PROVIDER_RUN_RE = re.compile(r"_{2,}")
_PROVIDER_MAX_LEN = 32


class GenericIngestRequest(BaseModel):
    provider: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "CI vendor label. Anything reasonable works: spaces, "
            "uppercase, slashes and parentheses are auto-normalised "
            "into a lowercase slug (max 32 chars). Examples: "
            "'buildkite', 'AppVeyor', 'GitHub Actions', 'My CI 1.0'."
        ),
    )
    project: str = Field(min_length=1, max_length=512)
    status: str = Field(min_length=1, max_length=32)
    log: str = Field(min_length=1)
    pipeline_id: str | None = Field(default=None, max_length=128)
    job_id: str | None = Field(default=None, max_length=128)
    job_name: str | None = Field(default=None, max_length=256)
    branch: str | None = Field(default=None, max_length=256)
    commit_sha: str | None = Field(default=None, max_length=64)
    pipeline_url: HttpUrl | None = None
    build_number: str | None = Field(default=None, max_length=64)

    @field_validator("provider")
    @classmethod
    def _normalise_provider(cls, v: str) -> str:
        normalised = _PROVIDER_BAD_CHARS_RE.sub("_", v.strip().lower())
        normalised = _PROVIDER_RUN_RE.sub("_", normalised)
        normalised = normalised.strip("_.-")[:_PROVIDER_MAX_LEN]
        if not normalised or not _PROVIDER_LABEL_RE.match(normalised):
            raise ValueError(
                "provider must contain at least one alphanumeric "
                "character (a-z, 0-9)"
            )
        return normalised


class GenericIngestResponse(BaseModel):
    status: str
    analysis_id: str


@router.post(
    "/log",
    response_model=GenericIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_generic(
    body: GenericIngestRequest,
    principal: CurrentPrincipal = Depends(require_scope("ingest")),
    session: AsyncSession = Depends(get_db),
) -> GenericIngestResponse:
    normalized_status = body.status.strip().lower()
    if normalized_status not in _KNOWN_STATUSES:
        log.info(
            "ingest_generic.unknown_status",
            provider=body.provider,
            status=normalized_status,
        )

    if len(body.log.encode("utf-8", errors="ignore")) > MAX_LOG_BYTES:
        raise HTTPException(status_code=413, detail="Log too large")

    try:
        await check_rate_limit(
            f"tenant:{principal.tenant.id}:ingest_generic",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    if body.pipeline_id and body.job_id:
        ci_run_id = f"{body.pipeline_id}#{body.job_id}"
    elif body.pipeline_id:
        ci_run_id = body.pipeline_id
    elif body.build_number:
        ci_run_id = body.build_number
    else:
        from datetime import datetime, timezone

        bucket = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        ci_run_id = f"{body.project}#{bucket}"

    pipeline_url = str(body.pipeline_url) if body.pipeline_url is not None else None

    event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="generic",
        source="generic_ingest",
        ci_run_id=ci_run_id,
        ci_job_id=body.job_id,
        project_id=body.project,
        project_path=body.project,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        ref=body.branch,
        sha=body.commit_sha,
        status=normalized_status,
        raw={
            "provider_label": body.provider,
            "project": body.project,
            "pipeline_id": body.pipeline_id,
            "job_id": body.job_id,
            "job_name": body.job_name,
            "build_number": body.build_number,
            "status": normalized_status,
            "branch": body.branch,
            "commit_sha": body.commit_sha,
            "pipeline_url": pipeline_url,
        },
    )

    await persist_ingestion_event(session, event)
    analysis_row = await run_external_ingest(session, event, body.log)
    await session.commit()

    return GenericIngestResponse(
        status="accepted",
        analysis_id=str(analysis_row.id),
    )
