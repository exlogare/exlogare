"""CRUD for tenant API tokens."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin
from app.models.api_token import ApiToken
from app.services.audit import record_audit
from app.services.auth.api_token import (
    ALLOWED_SCOPES,
    generate_token,
    normalize_scopes,
)
from app.services.selfhost_policy import (
    api_keys_allowed,
    get_plan_spec,
    max_api_keys_for,
)

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class ApiTokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    created_at: str


class ApiTokenCreated(ApiTokenOut):
    token: str  # raw secret, shown once


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=lambda: ["ingest"])
    expires_at: datetime | None = None


def _serialize(row: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=str(row.id),
        name=row.name,
        prefix=row.token_prefix,
        scopes=list(row.scopes or []),
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
        created_at=row.created_at.isoformat(),
    )


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> list[ApiTokenOut]:
    rows = (
        await session.execute(
            select(ApiToken)
            .where(ApiToken.tenant_id == principal.tenant.id)
            .order_by(desc(ApiToken.created_at))
        )
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ApiTokenCreated:
    try:
        scopes = normalize_scopes(body.scopes)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc}. Allowed scopes: {sorted(ALLOWED_SCOPES)}",
        ) from exc
    if not scopes:
        raise HTTPException(status_code=400, detail="At least one scope is required")

    spec = get_plan_spec(principal.tenant)
    if not api_keys_allowed(principal.tenant, spec):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current plan does not allow API keys",
        )

    cap = max_api_keys_for(spec)
    if cap is not None:
        active_count = (
            await session.execute(
                select(func.count(ApiToken.id)).where(
                    ApiToken.tenant_id == principal.tenant.id,
                    ApiToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        if int(active_count or 0) >= cap:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your workspace allows up to {cap} active API tokens. "
                    "Revoke an existing one to create a new token."
                ),
            )

    if body.expires_at is not None:
        expiry = body.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="expires_at must be in the future")
    else:
        expiry = None

    generated = generate_token()
    row = ApiToken(
        tenant_id=principal.tenant.id,
        created_by_user_id=principal.user.id,
        name=body.name.strip(),
        token_hash=generated.token_hash,
        token_prefix=generated.token_prefix,
        scopes=scopes,
        expires_at=expiry,
    )
    session.add(row)
    await session.flush()

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="api_token_created",
        actor=principal.user.email,
        target=str(row.id),
        meta={"name": row.name, "scopes": scopes, "prefix": row.token_prefix},
    )
    await session.commit()
    await session.refresh(row)

    out = _serialize(row)
    return ApiTokenCreated(**out.model_dump(), token=generated.raw)


@router.post("/{token_id}/revoke", response_model=ApiTokenOut)
async def revoke_token(
    token_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ApiTokenOut:
    row = (
        await session.execute(
            select(ApiToken).where(
                ApiToken.id == token_id,
                ApiToken.tenant_id == principal.tenant.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="api_token_revoked",
        actor=principal.user.email,
        target=str(row.id),
        meta={"name": row.name, "prefix": row.token_prefix},
    )
    await session.commit()
    await session.refresh(row)
    return _serialize(row)
