from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cookie import SESSION_COOKIE_NAME
from app.core.db import get_db
from app.models.api_token import ApiToken
from app.models.membership import Membership, MembershipRole
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth.api_token import looks_like_api_token, verify_token
from app.services.auth.jwt import decode_token


@dataclass
class CurrentPrincipal:
    user: User
    tenant: Tenant
    role: MembershipRole
    api_token: ApiToken | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        if self.api_token is not None:
            return False
        return self.role in (MembershipRole.OWNER, MembershipRole.ADMIN)

    @property
    def is_viewer(self) -> bool:
        return self.role == MembershipRole.VIEWER

    def has_scope(self, scope: str) -> bool:
        if self.api_token is None:
            return True
        return scope in self.scopes


def _extract_token(request: Request, authorization: str | None) -> str | None:
    """Return the raw credential from cookie or Bearer header."""
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


async def _principal_from_api_token(
    session: AsyncSession, raw_token: str
) -> CurrentPrincipal:
    token = await verify_token(session, raw_token)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
        )

    user_row = await session.execute(
        select(User).where(User.id == token.created_by_user_id)
    )
    user = user_row.scalar_one_or_none()
    if user is None:
        # The creator was deleted; the token should not grant access anymore.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token owner no longer exists",
        )

    tenant_row = await session.execute(
        select(Tenant).where(Tenant.id == token.tenant_id)
    )
    tenant = tenant_row.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token tenant no longer exists",
        )

    membership_row = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == tenant.id,
        )
    )
    membership = membership_row.scalars().first()
    role = membership.role if membership is not None else MembershipRole.MEMBER

    return CurrentPrincipal(
        user=user,
        tenant=tenant,
        role=role,
        api_token=token,
        scopes=frozenset(token.scopes or ()),
    )


async def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    session: AsyncSession = Depends(get_db),
) -> CurrentPrincipal:
    token = _extract_token(request, authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    if looks_like_api_token(token):
        return await _principal_from_api_token(session, token)

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user_id = uuid.UUID(payload["sub"])
    user_row = await session.execute(select(User).where(User.id == user_id))
    user = user_row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    requested_tenant = (
        uuid.UUID(x_tenant_id) if x_tenant_id else uuid.UUID(payload["tid"]) if payload.get("tid") else None
    )
    membership_stmt = select(Membership).where(Membership.user_id == user.id)
    if requested_tenant is not None:
        membership_stmt = membership_stmt.where(Membership.tenant_id == requested_tenant)
    row = await session.execute(membership_stmt)
    membership = row.scalars().first()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant membership")

    tenant_row = await session.execute(select(Tenant).where(Tenant.id == membership.tenant_id))
    tenant = tenant_row.scalar_one()
    return CurrentPrincipal(user=user, tenant=tenant, role=membership.role)


def require_admin(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> CurrentPrincipal:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return principal


def require_non_viewer(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> CurrentPrincipal:
    if principal.is_viewer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer role is read-only",
        )
    return principal


def require_scope(scope: str):
    """Build a dependency that enforces ``scope`` on API-token principals."""

    def _enforce(
        principal: CurrentPrincipal = Depends(get_current_principal),
    ) -> CurrentPrincipal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing scope: {scope}",
            )
        return principal

    return _enforce
