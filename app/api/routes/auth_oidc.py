"""OIDC login routes for Community Edition (Keycloak-compatible)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cookie import generate_csrf_token, set_csrf_cookie, set_session_cookie
from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.models.membership import Membership, MembershipRole
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth.jwt import issue_access_token
from app.services.auth.oidc import OidcError, OidcService, oidc_is_configured

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])
log = get_logger(__name__)


class OidcStatusResponse(BaseModel):
    enabled: bool
    display_name: str


@router.get("/status", response_model=OidcStatusResponse)
async def oidc_status() -> OidcStatusResponse:
    settings = get_settings()
    return OidcStatusResponse(
        enabled=oidc_is_configured(),
        display_name=settings.oidc_display_name or "SSO",
    )


@router.get("/login")
async def oidc_login() -> RedirectResponse:
    if not oidc_is_configured():
        raise HTTPException(status_code=404, detail="OIDC is not enabled")
    try:
        url = await OidcService().build_authorize_url()
    except OidcError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("oidc.login_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to start OIDC login") from exc
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def oidc_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    web = settings.web_base_url.rstrip("/")

    def _fail(reason: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(
            url=f"{web}/login?oidc_error={quote(reason)}",
            status_code=302,
        )

    if not oidc_is_configured():
        return _fail("OIDC is not enabled")
    if error:
        return _fail(error_description or error)
    if not code or not state:
        return _fail("Missing authorization code")

    svc = OidcService()
    try:
        stored = await svc.consume_state(state)
        if stored is None:
            return _fail("Invalid or expired OIDC state")
        token_payload = await svc.exchange_code(code, code_verifier=stored["code_verifier"])
        id_token = token_payload.get("id_token")
        if not id_token:
            return _fail("OIDC response missing id_token")
        claims = await svc.validate_id_token(id_token)
    except OidcError as exc:
        return _fail(exc.detail)
    except Exception as exc:  # noqa: BLE001
        log.warning("oidc.callback_failed", error=str(exc))
        return _fail("OIDC login failed")

    user = (
        await session.execute(select(User).where(User.oidc_sub == claims.sub))
    ).scalar_one_or_none()
    if user is None:
        user = (
            await session.execute(select(User).where(User.email == claims.email))
        ).scalar_one_or_none()
        if user is not None:
            user.oidc_sub = claims.sub
        elif settings.oidc_auto_provision:
            tenant = (
                await session.execute(select(Tenant).order_by(Tenant.created_at.asc()).limit(1))
            ).scalar_one_or_none()
            if tenant is None:
                return _fail("No tenant available — bootstrap an admin first")
            user = User(
                email=claims.email,
                display_name=claims.name or claims.email.split("@", 1)[0],
                password_hash=None,
                oidc_sub=claims.sub,
                email_verified_at=(
                    datetime.now(tz=UTC) if claims.email_verified else None
                ),
            )
            session.add(user)
            await session.flush()
            session.add(
                Membership(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role=MembershipRole.MEMBER,
                )
            )
        else:
            return _fail("User is not provisioned — ask an admin to invite you")

    if claims.email_verified and user.email_verified_at is None:
        user.email_verified_at = datetime.now(tz=UTC)
    if claims.name and not user.display_name:
        user.display_name = claims.name

    membership = (
        await session.execute(select(Membership).where(Membership.user_id == user.id).limit(1))
    ).scalar_one_or_none()
    if membership is None:
        return _fail("No tenant membership")

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == membership.tenant_id))
    ).scalar_one()

    user.last_login_at = datetime.now(tz=UTC)
    await session.commit()

    access = issue_access_token(
        user_id=user.id,
        email=user.email,
        tenant_id=tenant.id,
        extra={"role": membership.role.value},
    )
    max_age = settings.jwt_expires_minutes * 60
    response = RedirectResponse(url=f"{web}/dashboard", status_code=302)
    set_session_cookie(response, access, max_age_seconds=max_age)
    set_csrf_cookie(response, generate_csrf_token(), max_age_seconds=max_age)
    log.info("oidc.login_ok", email=user.email, tenant_id=str(tenant.id))
    return response
