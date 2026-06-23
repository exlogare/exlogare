"""Failure clusters API — recurring-issues dashboard backend."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import (
    CurrentPrincipal,
    get_current_principal,
    require_non_viewer,
    require_scope,
)
from app.models.failure_cluster import ClusterStatus, FailureCluster

router = APIRouter(prefix="/api/clusters", tags=["clusters"])

public_router = APIRouter(prefix="/api/v1/clusters", tags=["public-api-v1"])

_REQUIRE_READ = require_scope("read")


class ClusterOut(BaseModel):
    """Wire shape for one cluster row."""

    id: str
    fingerprint_hash: str
    last_root_cause: str
    last_severity: str
    count: int
    first_seen_at: str
    last_seen_at: str
    status: Literal["active", "acknowledged", "resolved"]
    last_analysis_id: str | None = None
    acknowledged_at: str | None = None
    resolved_at: str | None = None


class ClustersResponse(BaseModel):
    items: list[ClusterOut]
    total: int
    limit: int
    offset: int


class ClustersStatsResponse(BaseModel):
    """At-a-glance counters for the dashboard tab."""

    active: int
    acknowledged: int
    resolved: int


class BadgeOut(BaseModel):
    """Recurring-issue badge for a single analysis row."""

    cluster_id: str
    count: int
    status: Literal["active", "acknowledged", "resolved"]


class BadgesResponse(BaseModel):
    badges: dict[str, BadgeOut]


def _to_out(row: FailureCluster) -> ClusterOut:
    return ClusterOut(
        id=str(row.id),
        fingerprint_hash=row.fingerprint_hash,
        last_root_cause=row.last_root_cause,
        last_severity=(
            row.last_severity.value
            if hasattr(row.last_severity, "value")
            else str(row.last_severity)
        ),
        count=row.count,
        first_seen_at=row.first_seen_at.isoformat(),
        last_seen_at=row.last_seen_at.isoformat(),
        status=(
            row.status.value
            if hasattr(row.status, "value")
            else str(row.status)
        ),
        last_analysis_id=str(row.last_analysis_id) if row.last_analysis_id else None,
        acknowledged_at=(
            row.acknowledged_at.isoformat() if row.acknowledged_at else None
        ),
        resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
    )


def _parse_status(value: str | None) -> ClusterStatus | None:
    if value is None:
        return None
    try:
        return ClusterStatus(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid status. Allowed: active | acknowledged | resolved."
            ),
        ) from exc


@router.get("", response_model=ClustersResponse)
async def list_clusters(
    status: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> ClustersResponse:
    """List clusters newest-first."""
    parsed_status = _parse_status(status)

    base = select(FailureCluster).where(
        FailureCluster.tenant_id == principal.tenant.id
    )
    if parsed_status is not None:
        base = base.where(FailureCluster.status == parsed_status)

    rows = (
        await session.execute(
            base.order_by(desc(FailureCluster.last_seen_at)).offset(offset).limit(limit)
        )
    ).scalars().all()

    count_stmt = select(func.count(FailureCluster.id)).where(
        FailureCluster.tenant_id == principal.tenant.id
    )
    if parsed_status is not None:
        count_stmt = count_stmt.where(FailureCluster.status == parsed_status)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    return ClustersResponse(
        items=[_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=ClustersStatsResponse)
async def cluster_stats(
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> ClustersStatsResponse:
    """Counts per status — three SQL counts, one per state."""
    base = select(func.count(FailureCluster.id)).where(
        FailureCluster.tenant_id == principal.tenant.id
    )
    active = int(
        (
            await session.execute(
                base.where(FailureCluster.status == ClusterStatus.ACTIVE)
            )
        ).scalar()
        or 0
    )
    acknowledged = int(
        (
            await session.execute(
                base.where(FailureCluster.status == ClusterStatus.ACKNOWLEDGED)
            )
        ).scalar()
        or 0
    )
    resolved = int(
        (
            await session.execute(
                base.where(FailureCluster.status == ClusterStatus.RESOLVED)
            )
        ).scalar()
        or 0
    )
    return ClustersStatsResponse(
        active=active, acknowledged=acknowledged, resolved=resolved
    )


@router.get("/badges", response_model=BadgesResponse)
async def badges_for_analyses(
    analysis_id: list[uuid.UUID] = Query(
        default_factory=list,
        description="Repeat the parameter for each analysis to look up.",
    ),
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> BadgesResponse:
    """Map ``analysis_id → cluster info`` for the dashboard's recurring badge."""
    if not analysis_id:
        return BadgesResponse(badges={})
    if len(analysis_id) > 200:
        raise HTTPException(
            status_code=400,
            detail="Up to 200 analysis ids per call.",
        )

    rows = (
        await session.execute(
            select(FailureCluster).where(
                FailureCluster.tenant_id == principal.tenant.id,
                FailureCluster.last_analysis_id.in_(analysis_id),
                FailureCluster.count > 1,
            )
        )
    ).scalars().all()

    badges: dict[str, BadgeOut] = {}
    for row in rows:
        if row.last_analysis_id is None:
            continue
        badges[str(row.last_analysis_id)] = BadgeOut(
            cluster_id=str(row.id),
            count=row.count,
            status=(
                row.status.value
                if hasattr(row.status, "value")
                else str(row.status)
            ),
        )
    return BadgesResponse(badges=badges)


@router.get("/{cluster_id}", response_model=ClusterOut)
async def get_cluster(
    cluster_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> ClusterOut:
    row = (
        await session.execute(
            select(FailureCluster).where(
                FailureCluster.id == cluster_id,
                FailureCluster.tenant_id == principal.tenant.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _to_out(row)


@router.post("/{cluster_id}/acknowledge", response_model=ClusterOut)
async def acknowledge_cluster(
    cluster_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> ClusterOut:
    """Mark a cluster as acknowledged."""
    row = await _load_cluster(session, principal.tenant.id, cluster_id)
    if row.status != ClusterStatus.ACKNOWLEDGED:
        row.status = ClusterStatus.ACKNOWLEDGED
        row.acknowledged_at = datetime.now(tz=timezone.utc)
        session.add(row)
        await session.flush()
    return _to_out(row)


@router.post("/{cluster_id}/resolve", response_model=ClusterOut)
async def resolve_cluster(
    cluster_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> ClusterOut:
    """Mark a cluster as resolved."""
    row = await _load_cluster(session, principal.tenant.id, cluster_id)
    if row.status != ClusterStatus.RESOLVED:
        row.status = ClusterStatus.RESOLVED
        row.resolved_at = datetime.now(tz=timezone.utc)
        session.add(row)
        await session.flush()
    return _to_out(row)


@router.post("/{cluster_id}/reopen", response_model=ClusterOut)
async def reopen_cluster(
    cluster_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> ClusterOut:
    """Move an acknowledged or resolved cluster back to ``active``."""
    row = await _load_cluster(session, principal.tenant.id, cluster_id)
    if row.status != ClusterStatus.ACTIVE:
        row.status = ClusterStatus.ACTIVE
        row.acknowledged_at = None
        row.resolved_at = None
        session.add(row)
        await session.flush()
    return _to_out(row)


@public_router.get("", response_model=ClustersResponse)
async def list_clusters_v1(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> ClustersResponse:
    """Token-authenticated mirror of :func:`list_clusters`."""
    parsed_status = _parse_status(status)

    base = select(FailureCluster).where(
        FailureCluster.tenant_id == principal.tenant.id
    )
    if parsed_status is not None:
        base = base.where(FailureCluster.status == parsed_status)

    rows = (
        await session.execute(
            base.order_by(desc(FailureCluster.last_seen_at)).offset(offset).limit(limit)
        )
    ).scalars().all()

    count_stmt = select(func.count(FailureCluster.id)).where(
        FailureCluster.tenant_id == principal.tenant.id
    )
    if parsed_status is not None:
        count_stmt = count_stmt.where(FailureCluster.status == parsed_status)
    total = int((await session.execute(count_stmt)).scalar() or 0)

    return ClustersResponse(
        items=[_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@public_router.get("/{cluster_id}", response_model=ClusterOut)
async def get_cluster_v1(
    cluster_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(_REQUIRE_READ),
    session: AsyncSession = Depends(get_db),
) -> ClusterOut:
    row = (
        await session.execute(
            select(FailureCluster).where(
                FailureCluster.id == cluster_id,
                FailureCluster.tenant_id == principal.tenant.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _to_out(row)


async def _load_cluster(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    cluster_id: uuid.UUID,
) -> FailureCluster:
    row = (
        await session.execute(
            select(FailureCluster).where(
                FailureCluster.id == cluster_id,
                FailureCluster.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row
