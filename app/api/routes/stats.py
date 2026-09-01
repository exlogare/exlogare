from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, get_current_principal
from app.models.analysis_result import AnalysisResult
from app.models.ingestion_event import IngestionEvent

router = APIRouter(prefix="/api/stats", tags=["stats"])


class OverviewResponse(BaseModel):
    failures_detected: int
    analyses_completed: int
    rca_count: int  # alias of analyses_completed, kept for back-compat
    severity_counts: dict[str, int]
    avg_time_to_rca_seconds: float | None
    p50_time_to_rca_seconds: float | None
    p90_time_to_rca_seconds: float | None
    window_days: int


class TimeseriesPoint(BaseModel):
    date: str
    failures: int


class TopProjectRow(BaseModel):
    project_id: str | None
    project_path: str | None
    failures: int
    analyses: int


class TopRootCauseRow(BaseModel):
    root_cause: str
    severity: str
    count: int


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    days: int = Query(default=30, ge=1, le=365),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    tenant_id = principal.tenant.id

    failures_detected = int(
        (
            await session.execute(
                select(func.count(IngestionEvent.id)).where(
                    IngestionEvent.tenant_id == tenant_id,
                    IngestionEvent.received_at >= since,
                )
            )
        ).scalar()
        or 0
    )

    severity_rows = (
        await session.execute(
            select(AnalysisResult.severity, func.count(AnalysisResult.id))
            .where(
                AnalysisResult.tenant_id == tenant_id,
                AnalysisResult.created_at >= since,
            )
            .group_by(AnalysisResult.severity)
        )
    ).all()
    severity_counts = {sev.value if hasattr(sev, "value") else str(sev): int(count) for sev, count in severity_rows}

    deltas = (
        await session.execute(
            select(
                func.extract(
                    "epoch",
                    AnalysisResult.created_at - IngestionEvent.received_at,
                ).label("delta")
            )
            .select_from(AnalysisResult)
            .join(
                IngestionEvent,
                and_(
                    IngestionEvent.tenant_id == AnalysisResult.tenant_id,
                    IngestionEvent.provider == AnalysisResult.provider,
                    IngestionEvent.ci_run_id == AnalysisResult.ci_run_id,
                    IngestionEvent.ci_job_id == AnalysisResult.ci_job_id,
                ),
            )
            .where(
                AnalysisResult.tenant_id == tenant_id,
                AnalysisResult.created_at >= since,
            )
        )
    ).all()
    deltas_list = sorted(float(d.delta) for d in deltas if d.delta is not None)

    def _percentile(data: list[float], p: float) -> float | None:
        if not data:
            return None
        k = max(0, min(len(data) - 1, int(round(p * (len(data) - 1)))))
        return data[k]

    avg = sum(deltas_list) / len(deltas_list) if deltas_list else None
    p50 = _percentile(deltas_list, 0.5)
    p90 = _percentile(deltas_list, 0.9)

    analyses_completed = sum(severity_counts.values())

    return OverviewResponse(
        failures_detected=failures_detected,
        analyses_completed=analyses_completed,
        rca_count=analyses_completed,
        severity_counts=severity_counts,
        avg_time_to_rca_seconds=avg,
        p50_time_to_rca_seconds=p50,
        p90_time_to_rca_seconds=p90,
        window_days=days,
    )


@router.get("/timeseries", response_model=list[TimeseriesPoint])
async def timeseries(
    days: int = Query(default=30, ge=1, le=180),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> list[TimeseriesPoint]:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    tenant_id = principal.tenant.id
    day_col = func.date_trunc("day", IngestionEvent.received_at)
    stmt = (
        select(
            day_col.label("day"),
            func.count(IngestionEvent.id).label("failures"),
        )
        .where(
            IngestionEvent.tenant_id == tenant_id,
            IngestionEvent.received_at >= since,
        )
        .group_by(day_col)
        .order_by(day_col)
    )
    rows = (await session.execute(stmt)).all()
    return [
        TimeseriesPoint(
            date=row.day.date().isoformat() if hasattr(row.day, "date") else str(row.day),
            failures=int(row.failures or 0),
        )
        for row in rows
    ]


@router.get("/top-projects", response_model=list[TopProjectRow])
async def top_projects(
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> list[TopProjectRow]:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    tenant_id = principal.tenant.id
    stmt = (
        select(
            AnalysisResult.project_id,
            AnalysisResult.project_path,
            func.count(AnalysisResult.id).label("analyses"),
        )
        .where(
            AnalysisResult.tenant_id == tenant_id,
            AnalysisResult.created_at >= since,
        )
        .group_by(AnalysisResult.project_id, AnalysisResult.project_path)
        .order_by(func.count(AnalysisResult.id).desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    out: list[TopProjectRow] = []
    for row in rows:
        analyses = int(row.analyses or 0)
        out.append(
            TopProjectRow(
                project_id=row.project_id,
                project_path=row.project_path,
                failures=analyses,
                analyses=analyses,
            )
        )
    return out


@router.get("/top-root-causes", response_model=list[TopRootCauseRow])
async def top_root_causes(
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=50),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> list[TopRootCauseRow]:
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    tenant_id = principal.tenant.id
    stmt = select(AnalysisResult.root_cause, AnalysisResult.severity).where(
        AnalysisResult.tenant_id == tenant_id,
        AnalysisResult.created_at >= since,
    )
    rows = (await session.execute(stmt)).all()
    buckets: dict[tuple[str, str], tuple[str, int]] = {}
    for root_cause, severity in rows:
        sev = severity.value if hasattr(severity, "value") else str(severity)
        normalized = (root_cause or "").strip().lower()[:120]
        digest = hashlib.sha1(normalized.encode(), usedforsecurity=False).hexdigest()[:16]
        key = (digest, sev)
        display = (root_cause or "").strip() or "(unknown)"
        if key in buckets:
            _, count = buckets[key]
            buckets[key] = (display, count + 1)
        else:
            buckets[key] = (display, 1)
    top = sorted(buckets.items(), key=lambda item: item[1][1], reverse=True)[:limit]
    return [
        TopRootCauseRow(root_cause=display, severity=sev, count=count)
        for (_, sev), (display, count) in top
    ]
