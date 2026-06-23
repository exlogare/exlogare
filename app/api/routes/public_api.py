"""Versioned public Read API (``/api/v1/...``)."""
from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.analyses import AnalysisOut
from app.api.routes.stats import (
    OverviewResponse,
    TimeseriesPoint,
    TopProjectRow,
    TopRootCauseRow,
    overview as overview_handler,
    timeseries as timeseries_handler,
    top_projects as top_projects_handler,
    top_root_causes as top_root_causes_handler,
)
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_scope
from app.models.analysis_result import AnalysisResult, Severity

router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])

_REQUIRE_READ = require_scope("read")

_MAX_WINDOW_DAYS = 365


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * ((4 - len(cursor) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        iso, _, uid = raw.partition("|")
        return datetime.fromisoformat(iso), uuid.UUID(uid)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


class AnalysesPage(BaseModel):
    items: list[AnalysisOut]
    next_cursor: str | None = None
    limit: int


@router.get("/analyses", response_model=AnalysesPage)
async def list_analyses_v1(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    severity: str | None = Query(default=None),
    project: str | None = Query(
        default=None,
        description=(
            "Match against ``project_path`` (full path, e.g. ``acme/api``) "
            "or ``project_id``. Either column is OK; we OR them so callers "
            "don't need to know which provider populated which."
        ),
    ),
    source: str | None = Query(
        default=None,
        description=(
            "Filter by origin label, e.g. ``gitlab_webhook``, "
            "``circleci_ingest``, ``generic_ingest``."
        ),
    ),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> AnalysesPage:
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="`since` must be <= `until`")
    if since is not None and until is not None:
        delta_days = (until - since).total_seconds() / 86400.0
        if delta_days > _MAX_WINDOW_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"Window too large; max {_MAX_WINDOW_DAYS} days",
            )

    stmt = select(AnalysisResult).where(AnalysisResult.tenant_id == principal.tenant.id)
    if since is not None:
        stmt = stmt.where(AnalysisResult.created_at >= _ensure_utc(since))
    if until is not None:
        stmt = stmt.where(AnalysisResult.created_at <= _ensure_utc(until))
    if severity:
        try:
            stmt = stmt.where(AnalysisResult.severity == Severity(severity))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid severity") from exc
    if project:
        stmt = stmt.where(
            (AnalysisResult.project_path == project)
            | (AnalysisResult.project_id == project)
        )
    if source:
        stmt = stmt.where(AnalysisResult.source == source)
    if cursor:
        cur_at, cur_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(AnalysisResult.created_at, AnalysisResult.id)
            < tuple_(_ensure_utc(cur_at), cur_id)
        )

    stmt = stmt.order_by(
        desc(AnalysisResult.created_at), desc(AnalysisResult.id)
    ).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    items = [_to_out(r) for r in rows]
    return AnalysesPage(items=items, next_cursor=next_cursor, limit=limit)


@router.get("/analyses/{analysis_id}", response_model=AnalysisOut)
async def get_analysis_v1(
    analysis_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> AnalysisOut:
    row = (
        await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.id == analysis_id,
                AnalysisResult.tenant_id == principal.tenant.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _to_out(row)


@router.get("/stats/overview", response_model=OverviewResponse)
async def stats_overview_v1(
    days: int = Query(default=30, ge=1, le=365),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    return await overview_handler(days=days, principal=principal, session=session)


@router.get("/stats/timeseries", response_model=list[TimeseriesPoint])
async def stats_timeseries_v1(
    days: int = Query(default=30, ge=1, le=180),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> list[TimeseriesPoint]:
    return await timeseries_handler(days=days, principal=principal, session=session)


@router.get("/stats/top-projects", response_model=list[TopProjectRow])
async def stats_top_projects_v1(
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> list[TopProjectRow]:
    return await top_projects_handler(
        days=days, limit=limit, principal=principal, session=session
    )


@router.get("/stats/top-root-causes", response_model=list[TopRootCauseRow])
async def stats_top_root_causes_v1(
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> list[TopRootCauseRow]:
    return await top_root_causes_handler(
        days=days, limit=limit, principal=principal, session=session
    )


def _ensure_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _to_out(row: AnalysisResult) -> AnalysisOut:
    return AnalysisOut(
        id=str(row.id),
        provider=row.provider,
        source=row.source,
        ci_run_id=row.ci_run_id,
        ci_job_id=row.ci_job_id,
        project_id=row.project_id,
        project_path=row.project_path,
        project_web_url=row.project_web_url,
        pipeline_url=row.pipeline_url,
        job_url=row.job_url,
        mr_iid=row.mr_iid,
        root_cause=row.root_cause,
        explanation=row.explanation,
        fix_suggestion=row.fix_suggestion,
        severity=(
            row.severity.value if hasattr(row.severity, "value") else str(row.severity)
        ),
        confidence=row.confidence,
        created_at=row.created_at.isoformat(),
    )
