from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, get_current_principal
from app.models.analysis_result import AnalysisResult, Severity

router = APIRouter(prefix="/api/analyses", tags=["analyses"])


class AnalysisOut(BaseModel):
    id: str
    provider: str
    source: str | None = None
    ci_run_id: str
    ci_job_id: str | None
    project_id: str | None
    project_path: str | None = None
    project_web_url: str | None = None
    pipeline_url: str | None
    job_url: str | None = None
    mr_iid: str | None
    root_cause: str
    explanation: str
    fix_suggestion: str
    severity: str
    confidence: float
    created_at: str


class AnalysesResponse(BaseModel):
    items: list[AnalysisOut]
    total: int
    limit: int
    offset: int


@router.get("", response_model=AnalysesResponse)
async def list_analyses(
    severity: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> AnalysesResponse:
    stmt = select(AnalysisResult).where(AnalysisResult.tenant_id == principal.tenant.id)
    if severity:
        try:
            stmt = stmt.where(AnalysisResult.severity == Severity(severity))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid severity") from exc
    if project_id:
        stmt = stmt.where(AnalysisResult.project_id == project_id)
    stmt = stmt.order_by(desc(AnalysisResult.created_at)).offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    from sqlalchemy import func as _func

    count_stmt = select(_func.count(AnalysisResult.id)).where(
        AnalysisResult.tenant_id == principal.tenant.id
    )
    if severity:
        count_stmt = count_stmt.where(AnalysisResult.severity == Severity(severity))
    if project_id:
        count_stmt = count_stmt.where(AnalysisResult.project_id == project_id)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    return AnalysesResponse(
        items=[
            AnalysisOut(
                id=str(r.id),
                provider=r.provider,
                source=r.source,
                ci_run_id=r.ci_run_id,
                ci_job_id=r.ci_job_id,
                project_id=r.project_id,
                project_path=r.project_path,
                project_web_url=r.project_web_url,
                pipeline_url=r.pipeline_url,
                job_url=r.job_url,
                mr_iid=r.mr_iid,
                root_cause=r.root_cause,
                explanation=r.explanation,
                fix_suggestion=r.fix_suggestion,
                severity=r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                confidence=r.confidence,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{analysis_id}", response_model=AnalysisOut)
async def get_analysis(
    analysis_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(get_current_principal),
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
        severity=row.severity.value if hasattr(row.severity, "value") else str(row.severity),
        confidence=row.confidence,
        created_at=row.created_at.isoformat(),
    )
