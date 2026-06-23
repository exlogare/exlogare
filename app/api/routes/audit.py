"""Tenant-scoped audit log read API."""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


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


class AuditEntry(BaseModel):
    id: str
    action: str
    actor: str | None = None
    target: str | None = None
    meta: dict = {}
    created_at: str


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: str | None = None
    limit: int


def _ensure_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_filters(
    *,
    tenant_id: uuid.UUID,
    action: str | None,
    actor: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list:
    conds = [AuditLog.tenant_id == tenant_id]
    if action:
        conds.append(AuditLog.action == action)
    if actor:
        conds.append(AuditLog.actor == actor)
    if since is not None:
        conds.append(AuditLog.created_at >= _ensure_utc(since))
    if until is not None:
        conds.append(AuditLog.created_at <= _ensure_utc(until))
    return conds


def _serialize(row: AuditLog) -> AuditEntry:
    return AuditEntry(
        id=str(row.id),
        action=row.action,
        actor=row.actor,
        target=row.target,
        meta=row.meta or {},
        created_at=row.created_at.isoformat(),
    )


@router.get("", response_model=AuditPage)
async def list_audit_entries(
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> AuditPage:
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="`since` must be <= `until`")

    stmt = select(AuditLog).where(
        *_build_filters(
            tenant_id=principal.tenant.id,
            action=action,
            actor=actor,
            since=since,
            until=until,
        )
    )
    if cursor:
        cur_at, cur_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(AuditLog.created_at, AuditLog.id)
            < tuple_(_ensure_utc(cur_at), cur_id)
        )

    stmt = stmt.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return AuditPage(
        items=[_serialize(r) for r in rows],
        next_cursor=next_cursor,
        limit=limit,
    )


@router.get("/actions", response_model=list[str])
async def list_actions(
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> list[str]:
    """Distinct ``action`` values seen for this tenant."""
    stmt = (
        select(AuditLog.action)
        .where(AuditLog.tenant_id == principal.tenant.id)
        .distinct()
        .order_by(AuditLog.action)
    )
    return [row for (row,) in (await session.execute(stmt)).all()]


@router.get("/export.csv")
async def export_audit_csv(
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    max_rows: int = Query(
        default=10_000,
        ge=1,
        le=100_000,
        description=(
            "Hard ceiling on rows in a single CSV. Defaults to 10k which "
            "matches what Excel comfortably opens; bump for offline forensics."
        ),
    ),
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream the same filters as the JSON list, but as CSV."""
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=400, detail="`since` must be <= `until`")

    stmt = select(AuditLog).where(
        *_build_filters(
            tenant_id=principal.tenant.id,
            action=action,
            actor=actor,
            since=since,
            until=until,
        )
    )
    stmt = stmt.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(max_rows)

    rows = list((await session.execute(stmt)).scalars().all())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["created_at", "action", "actor", "target", "meta_json"])
    for r in rows:
        writer.writerow(
            [
                r.created_at.isoformat(),
                r.action,
                r.actor or "",
                r.target or "",
                json.dumps(r.meta or {}, separators=(",", ":"), sort_keys=True),
            ]
        )

    payload = buf.getvalue().encode("utf-8")
    filename = (
        "exlogare-audit-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    )
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
