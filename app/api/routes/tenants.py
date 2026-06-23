from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, get_current_principal, require_admin
from app.models.membership import Membership, MembershipRole
from app.models.tenant import Tenant
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth.password import hash_password

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


class MemberOut(BaseModel):
    user_id: str
    email: str
    role: str


class FeedbackDefaultsModel(BaseModel):
    mr_comment: bool | None = None
    commit_comment: bool | None = None
    issue: bool | None = None
    status_check: bool | None = None


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=3, max_length=100)
    feedback_defaults: FeedbackDefaultsModel | None = None


class TenantCurrentOut(BaseModel):
    id: str
    name: str
    slug: str
    feedback_defaults: dict[str, bool]


class InviteRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = "member"


@router.get("/current/members", response_model=list[MemberOut])
async def list_members(
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.tenant_id == principal.tenant.id)
        )
    ).all()
    return [
        MemberOut(user_id=str(m.user_id), email=u.email, role=m.role.value)
        for m, u in rows
    ]


_FEEDBACK_KEYS = ("mr_comment", "commit_comment", "issue", "status_check")


def _normalize_feedback_defaults(raw: dict | None) -> dict[str, bool]:
    raw = raw or {}
    return {k: bool(raw.get(k, True)) for k in _FEEDBACK_KEYS}


async def _tenant_out(row: Tenant) -> TenantCurrentOut:
    return TenantCurrentOut(
        id=str(row.id),
        name=row.name,
        slug=row.slug,
        feedback_defaults=_normalize_feedback_defaults(row.feedback_defaults),
    )


@router.get("/current", response_model=TenantCurrentOut)
async def get_current_tenant(
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> TenantCurrentOut:
    row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    return await _tenant_out(row)


async def _update_tenant(
    body: TenantUpdateRequest,
    principal: CurrentPrincipal,
    session: AsyncSession,
) -> TenantCurrentOut:
    if body.name is None and body.slug is None and body.feedback_defaults is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    if body.name is not None:
        row.name = body.name.strip()
    if body.slug is not None:
        slug = body.slug.strip().lower()
        taken = (
            await session.execute(
                select(Tenant).where(Tenant.slug == slug, Tenant.id != principal.tenant.id)
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise HTTPException(status_code=400, detail="Slug already taken")
        row.slug = slug
    if body.feedback_defaults is not None:
        current = _normalize_feedback_defaults(row.feedback_defaults)
        incoming = body.feedback_defaults.model_dump(exclude_none=True)
        current.update({k: bool(v) for k, v in incoming.items()})
        row.feedback_defaults = current
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="tenant_updated",
        actor=principal.user.email,
        meta={
            "feedback_defaults": (
                body.feedback_defaults.model_dump(exclude_none=True)
                if body.feedback_defaults is not None
                else None
            ),
        },
    )
    await session.commit()
    await session.refresh(row)
    return await _tenant_out(row)


@router.post("/current", response_model=TenantCurrentOut)
async def update_tenant(
    body: TenantUpdateRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TenantCurrentOut:
    return await _update_tenant(body, principal, session)


@router.patch("/current", response_model=TenantCurrentOut)
async def patch_tenant(
    body: TenantUpdateRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> TenantCurrentOut:
    return await _update_tenant(body, principal, session)


@router.post("/current/invites", response_model=dict)
async def invite_member(
    body: InviteRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        role = MembershipRole(body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid role") from exc
    if role == MembershipRole.OWNER:
        raise HTTPException(status_code=400, detail="Cannot invite with owner role")

    email = body.email.strip().lower()
    user_row = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user_row is None:
        user_row = User(email=email, password_hash=hash_password(body.password))
        session.add(user_row)
        await session.flush()
    else:
        user_row.password_hash = hash_password(body.password)

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_row.id,
                Membership.tenant_id == principal.tenant.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        existing.role = role
    else:
        session.add(
            Membership(user_id=user_row.id, tenant_id=principal.tenant.id, role=role)
        )

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="tenant_member_invited",
        actor=principal.user.email,
        target=email,
        meta={"role": role.value},
    )
    await session.commit()
    return {"status": "invited", "email": email, "role": role.value}


@router.delete("/current/members/{user_id}", response_model=dict)
async def remove_member(
    user_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if user_id == principal.user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    row = (
        await session.execute(
            select(Membership).where(
                Membership.tenant_id == principal.tenant.id,
                Membership.user_id == user_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")
    await session.delete(row)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="tenant_member_removed",
        actor=principal.user.email,
        target=str(user_id),
    )
    await session.commit()
    return {"status": "removed"}
