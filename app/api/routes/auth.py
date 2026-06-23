from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cookie import (
    CSRF_COOKIE_NAME,
    clear_auth_cookies,
    generate_csrf_token,
    set_csrf_cookie,
    set_session_cookie,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, get_current_principal
from app.core.logging import get_logger
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.models.ci_connection import CIConnection
from app.models.membership import Membership, MembershipRole
from app.models.user import User
from app.services.auth.jwt import issue_access_token
from app.services.auth.password import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = get_logger(__name__)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict
    tenant: dict
    role: str


class MeResponse(BaseModel):
    user: dict
    tenant: dict
    role: str
    onboarded: bool


@router.post("/login", response_model=SessionResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    try:
        await check_rate_limit(
            f"login:{client_ip}",
            limit=settings.login_rate_limit_per_hour,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    email = body.email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    membership = (
        await session.execute(select(Membership).where(Membership.user_id == user.id).limit(1))
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=403, detail="No tenant membership")

    from app.models.tenant import Tenant

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == membership.tenant_id))
    ).scalar_one()

    user.last_login_at = datetime.now(tz=timezone.utc)
    await session.commit()

    access = issue_access_token(
        user_id=user.id,
        email=user.email,
        tenant_id=tenant.id,
        extra={"role": membership.role.value},
    )
    max_age = settings.jwt_expires_minutes * 60
    set_session_cookie(response, access, max_age_seconds=max_age)
    set_csrf_cookie(response, generate_csrf_token(), max_age_seconds=max_age)
    return SessionResponse(
        access_token=access,
        user={"id": str(user.id), "email": user.email, "display_name": user.display_name},
        tenant={"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug},
        role=membership.role.value,
    )


class CsrfResponse(BaseModel):
    csrf: str


@router.get("/csrf", response_model=CsrfResponse)
async def issue_csrf_token(request: Request, response: Response) -> CsrfResponse:
    settings = get_settings()
    existing = request.cookies.get(CSRF_COOKIE_NAME)
    token = existing or generate_csrf_token()
    set_csrf_cookie(response, token, max_age_seconds=settings.jwt_expires_minutes * 60)
    return CsrfResponse(csrf=token)


class LogoutResponse(BaseModel):
    status: str = "ok"


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    clear_auth_cookies(response)
    return LogoutResponse(status="ok")


@router.get("/me", response_model=MeResponse)
async def me(
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    tenant = principal.tenant
    has_conn = (
        await session.execute(
            select(CIConnection.id).where(CIConnection.tenant_id == tenant.id).limit(1)
        )
    ).first() is not None
    return MeResponse(
        user={
            "id": str(principal.user.id),
            "email": principal.user.email,
            "display_name": principal.user.display_name,
        },
        tenant={
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
        },
        role=principal.role.value,
        onboarded=has_conn,
    )
