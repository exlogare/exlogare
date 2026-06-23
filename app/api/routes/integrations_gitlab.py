from __future__ import annotations

import secrets
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin, require_non_viewer
from app.core.logging import get_logger
from app.models.ci_connection import (
    CIConnection,
    CIProvider,
    ConnectionStatus,
    IntegrationMode,
)
from app.models.tenant import Tenant
from app.services.audit import record_audit
from app.services.ci.feedback_policy import CHANNELS, resolve_feedback_policy
from app.services.ci.gitlab_client import GitLabClient
from app.services.oauth.gitlab import GitLabOAuthRefreshFailed, GitLabOAuthService
from app.services.oauth.gitlab_group import ensure_group_fresh_for_connection
from app.services.selfhost_policy import get_plan_spec, gitlab_modes_allowed

router = APIRouter(prefix="/api/integrations/gitlab", tags=["integrations"])
log = get_logger(__name__)


class OAuthInitRequest(BaseModel):
    base_url: str = Field(default="https://gitlab.com")
    client_id: str | None = None
    client_secret: str | None = None


class OAuthInitResponse(BaseModel):
    authorize_url: str


class ProjectOut(BaseModel):
    id: str
    name: str
    path_with_namespace: str
    web_url: str
    default_branch: str | None = None
    last_activity_at: str | None = None


class WatchRequest(BaseModel):
    project_ids: list[str] = Field(min_length=1)
    mode: str = Field(default="hybrid")


class WatchResult(BaseModel):
    connection_id: str
    project_id: str
    project_path: str | None = None
    mode: str
    status: str
    hook_registered: bool
    enabled: bool = True
    error: str | None = None


class WatchProjectsResponse(BaseModel):
    results: list[WatchResult]
    repo_limit_partial: bool = False


class ChangeModeRequest(BaseModel):
    """Partial update for a GitLab CI connection."""

    mode: str | None = None
    enabled: bool | None = None
    feedback_mr_comment: bool | str | None = None
    feedback_commit_comment: bool | str | None = None
    feedback_issue: bool | str | None = None
    feedback_status_check: bool | str | None = None


class ChangeModeResponse(BaseModel):
    connection_id: str
    mode: str
    status: str
    enabled: bool
    hook_registered: bool
    hook_revoked: bool
    feedback_override: dict | None = None
    feedback_effective: dict | None = None


class WebhookInitRequest(BaseModel):
    base_url: str = Field(default="https://gitlab.com")
    project: str = Field(description="Project ID, path (group/project), or full URL")
    personal_access_token: str | None = None


class WebhookInitResponse(BaseModel):
    connection_id: str
    project_id: str
    project_path: str | None
    mode: str
    status: str
    webhook_url: str
    webhook_secret: str
    hook_registered: bool
    instructions: list[str]


class ConnectionOut(BaseModel):
    id: str
    base_url: str
    mode: str
    status: str
    enabled: bool
    external_project_id: str | None
    external_project_name: str | None
    external_project_url: str | None
    last_delivery_at: str | None
    gitlab_user: dict | None
    oauth_app_editable: bool = False
    oauth_client_id: str | None = None
    feedback_override: dict | None = None
    feedback_effective: dict | None = None


class UpdateGitLabOAuthAppRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    # New secret; omit or null to keep existing encrypted secret.
    client_secret: str | None = None


@router.post("/oauth/init", response_model=OAuthInitResponse)
async def oauth_init(
    body: OAuthInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> OAuthInitResponse:
    service = GitLabOAuthService()
    settings = get_settings()
    base_url = body.base_url.rstrip("/")
    client_id = body.client_id or (
        settings.gitlab_oauth_client_id if base_url == settings.gitlab_base_url.rstrip("/") else None
    )
    client_secret = body.client_secret or (
        settings.gitlab_oauth_client_secret
        if base_url == settings.gitlab_base_url.rstrip("/")
        else None
    )
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="OAuth client_id required for this GitLab instance",
        )

    placeholder = await _get_or_create_placeholder_connection(
        session, principal, base_url=base_url, client_id=client_id, client_secret=client_secret
    )
    await session.commit()

    try:
        url = await service.build_authorize_url(
            principal.tenant.id,
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            connection_id=placeholder.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OAuthInitResponse(authorize_url=url)


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    service = GitLabOAuthService()
    state_data = await service.consume_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    tenant_id = uuid.UUID(state_data["tenant_id"])
    base_url = state_data.get("base_url") or settings.gitlab_base_url
    redirect_uri = state_data.get("redirect_uri") or settings.gitlab_oauth_redirect_uri
    connection_id = state_data.get("connection_id")
    client_id = state_data.get("client_id")
    client_secret = state_data.get("client_secret") or ""

    token_payload = await service.exchange_code(
        code,
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    conn: CIConnection | None = None
    if connection_id:
        result = await session.execute(
            select(CIConnection).where(CIConnection.id == uuid.UUID(connection_id))
        )
        conn = result.scalar_one_or_none()
    if conn is None:
        conn = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.GITLAB,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
        )
        session.add(conn)

    conn.base_url = base_url
    conn.oauth_client_id = client_id
    conn.oauth_client_secret_enc = encrypt_str(client_secret) if client_secret else None
    conn.status = ConnectionStatus.ACTIVE
    service.apply_tokens_to_connection(conn, token_payload)

    if token_payload.get("access_token"):
        user_info = await service.fetch_user_info(
            base_url=base_url, access_token=token_payload["access_token"]
        )
        if user_info:
            conn.gitlab_user_info = {
                "id": user_info.get("id"),
                "username": user_info.get("username"),
                "name": user_info.get("name"),
                "email": user_info.get("email"),
                "web_url": user_info.get("web_url"),
            }
            conn.oauth_user_id = str(user_info.get("id")) if user_info.get("id") else None

    await record_audit(
        session,
        tenant_id=tenant_id,
        action="gitlab_oauth_connected",
        target=f"base_url:{base_url}",
        meta={"scope": token_payload.get("scope")},
    )
    await session.commit()

    # Redirect back to the SPA onboarding
    web_url = f"{settings.web_base_url.rstrip('/')}/onboarding?gitlab=connected&connection_id={conn.id}"
    return RedirectResponse(web_url)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    connection_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    await ensure_group_fresh_for_connection(session, conn)
    async with GitLabClient(conn) as client:
        try:
            projects = await client.list_projects(membership=True, search=search)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    return [
        ProjectOut(
            id=str(p.get("id")),
            name=p.get("name") or "",
            path_with_namespace=p.get("path_with_namespace") or "",
            web_url=p.get("web_url") or "",
            default_branch=p.get("default_branch"),
            last_activity_at=p.get("last_activity_at"),
        )
        for p in projects
    ]


async def _confirm_webhook_absent(
    client: GitLabClient, project_id: str, hook_id: str
) -> bool:
    """Authoritative check: is ``hook_id`` actually gone from the project?"""
    try:
        hooks = await client.list_webhooks(project_id)
    except Exception as exc:
        log.info(
            "gitlab.webhook_list_for_confirm_failed",
            project_id=project_id,
            hook_id=hook_id,
            error=str(exc),
        )
        return False
    wanted = str(hook_id)
    return not any(str(h.get("id")) == wanted for h in hooks)


async def _remote_revoke_webhook_if_present(
    conn: CIConnection, client: GitLabClient
) -> bool:
    """Best-effort GitLab webhook revocation for a project connection."""
    if not conn.webhook_id_remote or not conn.external_project_id:
        return False
    project_id = conn.external_project_id
    hook_id = conn.webhook_id_remote
    revoked = False
    try:
        revoked = await client.delete_webhook(project_id, hook_id)
    except httpx.HTTPStatusError as exc:
        log.warning(
            "gitlab.webhook_revoke_failed_watch",
            connection_id=str(conn.id),
            status=exc.response.status_code,
        )
        revoked = await _confirm_webhook_absent(client, project_id, hook_id)
    except Exception as exc:
        log.warning(
            "gitlab.webhook_revoke_failed_watch",
            connection_id=str(conn.id),
            error=str(exc),
        )
        revoked = await _confirm_webhook_absent(client, project_id, hook_id)
    if revoked:
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None
    return revoked


@router.post("/watch", response_model=WatchProjectsResponse)
async def watch_projects(
    body: WatchRequest,
    connection_id: uuid.UUID = Query(...),
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> WatchProjectsResponse:
    base_conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    await ensure_group_fresh_for_connection(session, base_conn)

    mode = IntegrationMode(body.mode)
    spec_watch = get_plan_spec(principal.tenant)
    if mode.value not in gitlab_modes_allowed(principal.tenant, spec_watch):
        raise HTTPException(
            status_code=400,
            detail="This integration mode is not available on your plan",
        )

    ordered_unique: list[str] = []
    seen: set[str] = set()
    for raw in body.project_ids:
        if raw not in seen:
            seen.add(raw)
            ordered_unique.append(raw)

    budget: list[int] | None
    max_r = spec_watch.max_gitlab_repos
    if max_r is None:
        budget = None
    else:
        n_enabled_start = int(
            (
                await session.execute(
                    select(func.count(CIConnection.id)).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.GITLAB,
                        CIConnection.external_project_id.isnot(None),
                        CIConnection.enabled.is_(True),
                    )
                )
            ).scalar()
            or 0
        )
        budget = [max(0, max_r - n_enabled_start)]

    def _take_enabled_slot(existing: CIConnection | None) -> bool:
        if max_r is None:
            return True
        assert budget is not None
        if existing is not None and existing.enabled:
            return True
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    results: list[WatchResult] = []
    any_capped_without_error = False

    for pid in ordered_unique:
        project: dict | None = None
        existing_stmt = select(CIConnection).where(
            CIConnection.tenant_id == principal.tenant.id,
            CIConnection.provider == CIProvider.GITLAB,
            CIConnection.external_project_id == str(pid),
        )
        existing_row = (await session.execute(existing_stmt)).scalar_one_or_none()

        target_enabled = _take_enabled_slot(existing_row)
        project_conn = await _upsert_project_connection(
            session,
            tenant_id=principal.tenant.id,
            base_conn=base_conn,
            external_project_id=str(pid),
            mode=mode,
            enabled=target_enabled,
        )
        project_conn.oauth_access_token_enc = base_conn.oauth_access_token_enc
        project_conn.oauth_refresh_token_enc = base_conn.oauth_refresh_token_enc
        project_conn.oauth_token_expires_at = base_conn.oauth_token_expires_at
        project_conn.oauth_client_id = base_conn.oauth_client_id
        project_conn.oauth_client_secret_enc = base_conn.oauth_client_secret_enc

        hook_registered = False
        error: str | None = None

        async with GitLabClient(project_conn) as client:
            if not target_enabled:
                await _remote_revoke_webhook_if_present(project_conn, client)

            project = await client.get_project(str(pid))
            if project is None:
                error = "project not found"
                if not target_enabled:
                    project_conn.status = ConnectionStatus.DISABLED
                else:
                    project_conn.status = ConnectionStatus.ERROR
            else:
                project_conn.external_project_name = project.get("path_with_namespace")
                project_conn.external_project_url = project.get("web_url")

                if not target_enabled:
                    project_conn.enabled = False
                    project_conn.status = ConnectionStatus.DISABLED
                elif mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID):
                    secret = secrets.token_urlsafe(32)
                    project_conn.webhook_secret_enc = encrypt_str(secret)
                    webhook_url = _public_webhook_url()
                    try:
                        hook = await client.register_webhook(
                            str(pid), url=webhook_url, token=secret
                        )
                        project_conn.webhook_id_remote = str(hook.get("id"))
                        hook_registered = True
                        project_conn.status = ConnectionStatus.ACTIVE
                    except Exception as exc:
                        error = f"webhook registration failed: {exc}"
                        project_conn.status = ConnectionStatus.ERROR
                else:
                    project_conn.status = ConnectionStatus.ACTIVE

        if max_r is not None and not target_enabled and error is None:
            any_capped_without_error = True

        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="gitlab_project_watch",
            actor=principal.user.email,
            target=f"gitlab:{pid}",
            meta={
                "mode": mode.value,
                "error": error,
                "enabled": project_conn.enabled,
            },
        )
        results.append(
            WatchResult(
                connection_id=str(project_conn.id),
                project_id=str(pid),
                project_path=project_conn.external_project_name,
                mode=mode.value,
                status=project_conn.status.value,
                hook_registered=hook_registered,
                enabled=project_conn.enabled,
                error=error,
            )
        )
    await ensure_group_fresh_for_connection(session, base_conn)
    await session.commit()
    return WatchProjectsResponse(
        results=results,
        repo_limit_partial=any_capped_without_error,
    )


@router.post("/webhook/init", response_model=WebhookInitResponse)
async def webhook_init(
    body: WebhookInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> WebhookInitResponse:
    settings = get_settings()
    base_url, project_ref = _parse_project_input(body.project, fallback_base=body.base_url)

    secret = secrets.token_urlsafe(32)
    webhook_url = _public_webhook_url()

    conn = CIConnection(
        tenant_id=principal.tenant.id,
        provider=CIProvider.GITLAB,
        mode=IntegrationMode.WEBHOOK,
        base_url=base_url,
        status=ConnectionStatus.PENDING_MANUAL,
        webhook_secret_enc=encrypt_str(secret),
    )
    if body.personal_access_token:
        conn.api_token_enc = encrypt_str(body.personal_access_token)

    session.add(conn)
    await session.flush()

    hook_registered = False
    project_id: str | None = None
    project_path: str | None = None
    instructions: list[str] = _manual_webhook_instructions(webhook_url, secret)

    async with GitLabClient(conn) as client:
        project = await client.get_project(project_ref)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project {project_ref} not found")
        project_id = str(project.get("id"))
        project_path = project.get("path_with_namespace")
        conn.external_project_id = project_id
        conn.external_project_name = project_path
        conn.external_project_url = project.get("web_url")

        if body.personal_access_token:
            try:
                hook = await client.register_webhook(
                    project_id, url=webhook_url, token=secret
                )
                conn.webhook_id_remote = str(hook.get("id"))
                hook_registered = True
                conn.status = ConnectionStatus.ACTIVE
            except Exception as exc:
                log.warning("gitlab.webhook_register_failed", error=str(exc))

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitlab_webhook_connected",
        actor=principal.user.email,
        target=f"gitlab:{project_id}",
        meta={"hook_registered": hook_registered, "base_url": base_url},
    )
    await session.commit()

    _ = settings  # keep reference
    return WebhookInitResponse(
        connection_id=str(conn.id),
        project_id=project_id or "",
        project_path=project_path,
        mode=conn.mode.value,
        status=conn.status.value,
        webhook_url=webhook_url,
        webhook_secret=secret,
        hook_registered=hook_registered,
        instructions=instructions,
    )


def _gitlab_oauth_app_editable(conn: CIConnection) -> bool:
    """Placeholder row for a non-gitlab.com instance — tenant stores OAuth app credentials."""
    if conn.external_project_id is not None:
        return False
    settings = get_settings()
    return settings.gitlab_base_url.rstrip("/") != conn.base_url.rstrip("/")


async def _propagate_oauth_app_credentials(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    base_url: str,
    client_id: str,
    new_secret_plain: str | None,
    update_secret: bool,
) -> None:
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == tenant_id,
        CIConnection.provider == CIProvider.GITLAB,
        CIConnection.base_url == base_url,
    )
    rows = (await session.execute(stmt)).scalars().all()
    new_enc: str | None = None
    if update_secret:
        if not new_secret_plain or not new_secret_plain.strip():
            raise HTTPException(
                status_code=400,
                detail="client_secret cannot be empty when provided; omit the field to keep the current secret",
            )
        new_enc = encrypt_str(new_secret_plain.strip())
    for c in rows:
        c.oauth_client_id = client_id.strip()
        if update_secret:
            c.oauth_client_secret_enc = new_enc


@router.patch("/oauth-app/{connection_id}")
async def update_gitlab_oauth_app(
    connection_id: uuid.UUID,
    body: UpdateGitLabOAuthAppRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Update Application ID / Secret for a self-hosted GitLab OAuth app; propagates to all project rows."""
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if not _gitlab_oauth_app_editable(conn):
        raise HTTPException(
            status_code=400,
            detail="OAuth application credentials can only be edited for the self-hosted GitLab instance connection",
        )
    payload = body.model_dump(exclude_unset=True)
    update_secret = "client_secret" in payload
    secret_val = body.client_secret if update_secret else None

    await _propagate_oauth_app_credentials(
        session,
        tenant_id=principal.tenant.id,
        base_url=conn.base_url,
        client_id=body.client_id,
        new_secret_plain=secret_val,
        update_secret=update_secret,
    )
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitlab_oauth_app_updated",
        actor=principal.user.email,
        target=str(conn.id),
        meta={
            "base_url": conn.base_url,
            "client_id_changed": True,
            "secret_changed": update_secret,
        },
    )
    await session.commit()
    return {"status": "updated"}


@router.post("/webhook/verify/{connection_id}")
async def webhook_verify(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if conn.last_delivery_at is not None:
        conn.status = ConnectionStatus.ACTIVE
        await session.commit()
        return {"status": conn.status.value, "verified": True}
    return {"status": conn.status.value, "verified": False}


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITLAB,
    )
    rows = (await session.execute(stmt)).scalars().all()

    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()

    out: list[ConnectionOut] = []
    for c in rows:
        editable = _gitlab_oauth_app_editable(c)
        out.append(
            ConnectionOut(
                id=str(c.id),
                base_url=c.base_url,
                mode=c.mode.value,
                status=c.status.value,
                enabled=c.enabled,
                external_project_id=c.external_project_id,
                external_project_name=c.external_project_name,
                external_project_url=c.external_project_url,
                last_delivery_at=c.last_delivery_at.isoformat() if c.last_delivery_at else None,
                gitlab_user=c.gitlab_user_info or None,
                oauth_app_editable=editable,
                oauth_client_id=c.oauth_client_id if editable else None,
                feedback_override=_extract_feedback_override(c),
                feedback_effective=resolve_feedback_policy(tenant_row, c),
            )
        )
    return out


def _extract_feedback_override(conn: CIConnection) -> dict | None:
    """Return the raw per-connection feedback override, or None if unset."""
    extra = conn.extra or {}
    raw = extra.get("feedback") if isinstance(extra, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    return {k: bool(v) for k, v in raw.items() if k in CHANNELS}


async def _re_enable_gitlab_project_connection(
    session: AsyncSession,
    tenant: Tenant,
    conn: CIConnection,
) -> tuple[bool, bool]:
    """Re-activate a disabled GitLab project row: enforce repo limit, register webhook."""
    spec = get_plan_spec(tenant)
    max_r = spec.max_gitlab_repos
    if max_r is not None:
        n_enabled = int(
            (
                await session.execute(
                    select(func.count(CIConnection.id)).where(
                        CIConnection.tenant_id == tenant.id,
                        CIConnection.provider == CIProvider.GITLAB,
                        CIConnection.external_project_id.isnot(None),
                        CIConnection.enabled.is_(True),
                    )
                )
            ).scalar()
            or 0
        )
        if n_enabled >= max_r:
            raise HTTPException(
                status_code=400,
                detail="GitLab repository limit reached for this plan",
            )

    if conn.mode.value not in gitlab_modes_allowed(tenant, spec):
        raise HTTPException(
            status_code=400,
            detail="This integration mode is not available on your plan",
        )

    await ensure_group_fresh_for_connection(session, conn)

    hook_registered = False
    hook_revoked = False
    mode = conn.mode

    needs_polling = mode in (IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID)
    if needs_polling:
        grp_stmt = select(func.count(CIConnection.id)).where(
            CIConnection.tenant_id == tenant.id,
            CIConnection.provider == CIProvider.GITLAB,
            CIConnection.base_url == conn.base_url,
            CIConnection.oauth_refresh_token_enc.isnot(None),
        )
        has_oauth = int((await session.execute(grp_stmt)).scalar() or 0) > 0
        if not has_oauth:
            raise HTTPException(
                status_code=400,
                detail="Connect GitLab via OAuth before enabling polling mode",
            )

    needs_webhook = mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID)
    if needs_webhook:
        if not conn.webhook_id_remote:
            secret = secrets.token_urlsafe(32)
            webhook_url = _public_webhook_url()
            try:
                async with GitLabClient(conn) as client:
                    hook = await client.register_webhook(
                        conn.external_project_id, url=webhook_url, token=secret
                    )
                conn.webhook_secret_enc = encrypt_str(secret)
                conn.webhook_id_remote = str(hook.get("id"))
                hook_registered = True
            except Exception as exc:
                conn.status = ConnectionStatus.ERROR
                conn.enabled = False
                raise HTTPException(
                    status_code=502, detail=f"Webhook registration failed: {exc}"
                ) from exc
        conn.status = ConnectionStatus.ACTIVE
    else:
        conn.status = ConnectionStatus.ACTIVE

    conn.enabled = True
    return hook_registered, hook_revoked


@router.patch("/watch/{connection_id}", response_model=ChangeModeResponse)
async def change_connection_mode(
    connection_id: uuid.UUID,
    body: ChangeModeRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ChangeModeResponse:
    """Partial update for a GitLab CI connection."""
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if conn.external_project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify a placeholder connection",
        )

    feedback_payload = {
        "mr_comment": body.feedback_mr_comment,
        "commit_comment": body.feedback_commit_comment,
        "issue": body.feedback_issue,
        "status_check": body.feedback_status_check,
    }
    feedback_changed = any(v is not None for v in feedback_payload.values())
    feedback_changes: dict | None = None
    if feedback_changed:
        feedback_changes = _apply_feedback_override(conn, feedback_payload)

    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()

    if body.enabled is False:
        raise HTTPException(
            status_code=400,
            detail="Use DELETE on this endpoint to disable the connection",
        )

    if body.enabled is True:
        was_disabled = not conn.enabled
        hr = False
        hv = False
        if was_disabled:
            hr, hv = await _re_enable_gitlab_project_connection(
                session, principal.tenant, conn
            )
        if was_disabled or feedback_changed:
            action = (
                "gitlab_connection_reenabled"
                if was_disabled
                else "gitlab_connection_feedback_updated"
            )
            await record_audit(
                session,
                tenant_id=principal.tenant.id,
                action=action,
                actor=principal.user.email,
                target=str(conn.id),
                meta={
                    "hook_registered": hr,
                    "hook_revoked": hv,
                    "feedback_changes": feedback_changes,
                },
            )
            await session.commit()
        return ChangeModeResponse(
            connection_id=str(conn.id),
            mode=conn.mode.value,
            status=conn.status.value,
            enabled=conn.enabled,
            hook_registered=hr,
            hook_revoked=hv,
            feedback_override=_extract_feedback_override(conn),
            feedback_effective=resolve_feedback_policy(tenant_row, conn),
        )

    if body.mode is None:
        if not feedback_changed:
            raise HTTPException(status_code=400, detail="Nothing to update")
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="gitlab_connection_feedback_updated",
            actor=principal.user.email,
            target=str(conn.id),
            meta={"changes": feedback_changes},
        )
        await session.commit()
        return ChangeModeResponse(
            connection_id=str(conn.id),
            mode=conn.mode.value,
            status=conn.status.value,
            enabled=conn.enabled,
            hook_registered=False,
            hook_revoked=False,
            feedback_override=_extract_feedback_override(conn),
            feedback_effective=resolve_feedback_policy(tenant_row, conn),
        )

    try:
        new_mode = IntegrationMode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown integration mode") from exc

    spec = get_plan_spec(principal.tenant)
    if new_mode.value not in gitlab_modes_allowed(principal.tenant, spec):
        raise HTTPException(
            status_code=400,
            detail="This integration mode is not available on your plan",
        )

    old_mode = conn.mode
    if new_mode == old_mode:
        if feedback_changed:
            await record_audit(
                session,
                tenant_id=principal.tenant.id,
                action="gitlab_connection_feedback_updated",
                actor=principal.user.email,
                target=str(conn.id),
                meta={"changes": feedback_changes},
            )
            await session.commit()
        return ChangeModeResponse(
            connection_id=str(conn.id),
            mode=new_mode.value,
            status=conn.status.value,
            enabled=conn.enabled,
            hook_registered=False,
            hook_revoked=False,
            feedback_override=_extract_feedback_override(conn),
            feedback_effective=resolve_feedback_policy(tenant_row, conn),
        )

    needs_polling = new_mode in (IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID)
    if needs_polling:
        grp_stmt = select(func.count(CIConnection.id)).where(
            CIConnection.tenant_id == principal.tenant.id,
            CIConnection.provider == CIProvider.GITLAB,
            CIConnection.base_url == conn.base_url,
            CIConnection.oauth_refresh_token_enc.isnot(None),
        )
        has_oauth = int((await session.execute(grp_stmt)).scalar() or 0) > 0
        if not has_oauth:
            raise HTTPException(
                status_code=400,
                detail="Connect GitLab via OAuth before enabling polling mode",
            )

    hook_registered = False
    hook_revoked = False

    needs_webhook = new_mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID)
    if needs_webhook and not conn.webhook_id_remote:
        await ensure_group_fresh_for_connection(session, conn)
        secret = secrets.token_urlsafe(32)
        webhook_url = _public_webhook_url()
        try:
            async with GitLabClient(conn) as client:
                hook = await client.register_webhook(
                    conn.external_project_id, url=webhook_url, token=secret
                )
            conn.webhook_secret_enc = encrypt_str(secret)
            conn.webhook_id_remote = str(hook.get("id"))
            hook_registered = True
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Webhook registration failed: {exc}"
            ) from exc

    if not needs_webhook and conn.webhook_id_remote:
        project_id = conn.external_project_id
        hook_id = conn.webhook_id_remote
        try:
            await ensure_group_fresh_for_connection(session, conn)
            async with GitLabClient(conn) as client:
                try:
                    hook_revoked = await client.delete_webhook(
                        project_id, hook_id
                    )
                except httpx.HTTPStatusError as exc:
                    log.warning(
                        "gitlab.webhook_revoke_failed_on_mode_change",
                        connection_id=str(conn.id),
                        status=exc.response.status_code,
                    )
                    hook_revoked = await _confirm_webhook_absent(
                        client, project_id, hook_id
                    )
        except GitLabOAuthRefreshFailed:
            log.warning(
                "gitlab.webhook_revoke_skipped_oauth_on_mode_change",
                connection_id=str(conn.id),
            )
        except Exception as exc:
            log.warning(
                "gitlab.webhook_revoke_failed_on_mode_change",
                connection_id=str(conn.id),
                error=str(exc),
            )
        if hook_revoked:
            conn.webhook_id_remote = None
            conn.webhook_secret_enc = None

    conn.mode = new_mode
    if conn.status == ConnectionStatus.DISABLED:
        conn.status = ConnectionStatus.ACTIVE
    conn.enabled = True

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitlab_mode_changed",
        actor=principal.user.email,
        target=str(conn.id),
        meta={
            "from": old_mode.value,
            "to": new_mode.value,
            "hook_registered": hook_registered,
            "hook_revoked": hook_revoked,
            "feedback_changes": feedback_changes,
        },
    )
    await session.commit()

    return ChangeModeResponse(
        connection_id=str(conn.id),
        mode=new_mode.value,
        status=conn.status.value,
        enabled=conn.enabled,
        hook_registered=hook_registered,
        hook_revoked=hook_revoked,
        feedback_override=_extract_feedback_override(conn),
        feedback_effective=resolve_feedback_policy(tenant_row, conn),
    )


def _apply_feedback_override(
    conn: CIConnection, changes: dict[str, bool | str | None]
) -> dict[str, bool | None]:
    """Merge feedback override changes into ``conn.extra['feedback']``."""
    extra = dict(conn.extra or {})
    current_raw = extra.get("feedback")
    override: dict[str, bool] = {}
    if isinstance(current_raw, dict):
        for k in CHANNELS:
            if k in current_raw:
                override[k] = bool(current_raw[k])

    applied: dict[str, bool | None] = {}
    for key, value in changes.items():
        if value is None:
            continue
        if isinstance(value, str) and value == "inherit":
            override.pop(key, None)
            applied[key] = None
            continue
        override[key] = bool(value)
        applied[key] = bool(value)

    if override:
        extra["feedback"] = override
    else:
        extra.pop("feedback", None)
    conn.extra = extra
    flag_modified_extra(conn)
    return applied


def flag_modified_extra(conn: CIConnection) -> None:
    """Force SQLAlchemy to persist ``conn.extra`` reassignments."""
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(conn, "extra")


@router.delete("/watch/{connection_id}", status_code=status.HTTP_200_OK)
async def delete_connection(
    connection_id: uuid.UUID,
    purge: bool = False,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Disable a connection (default) or fully delete it (purge=true)."""

    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)

    is_base = purge and conn.external_project_id is None
    cascaded: list[CIConnection] = []
    if is_base:
        cascaded = (
            (
                await session.execute(
                    select(CIConnection).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.GITLAB,
                        CIConnection.base_url == conn.base_url,
                        CIConnection.id != conn.id,
                    )
                )
            )
            .scalars()
            .all()
        )

    revoked_remote = await _revoke_gitlab_hook(session, conn)

    cascaded_revoked = 0
    for child in cascaded:
        if await _revoke_gitlab_hook(session, child):
            cascaded_revoked += 1

    if revoked_remote:
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None

    if purge:
        conn_id = str(conn.id)
        cascaded_ids = [str(c.id) for c in cascaded]
        for child in cascaded:
            child.webhook_id_remote = None
            child.webhook_secret_enc = None
            await session.delete(child)
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None
        await session.delete(conn)
        meta: dict = {"revoked_remote": revoked_remote}
        if cascaded_ids:
            meta["cascaded"] = cascaded_ids
            meta["cascaded_revoked"] = cascaded_revoked
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="gitlab_connection_deleted",
            actor=principal.user.email,
            target=conn_id,
            meta=meta,
        )
        await session.commit()
        return {
            "status": "deleted",
            "revoked_remote": revoked_remote,
            "cascaded": len(cascaded_ids),
        }

    conn.enabled = False
    conn.status = ConnectionStatus.DISABLED
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitlab_connection_revoked",
        actor=principal.user.email,
        target=str(conn.id),
        meta={"revoked_remote": revoked_remote},
    )
    await session.commit()
    return {"status": "disabled", "revoked_remote": revoked_remote}


async def _revoke_gitlab_hook(
    session: AsyncSession, conn: CIConnection
) -> bool:
    """Best-effort revoke of a GitLab project webhook."""

    if not (conn.webhook_id_remote and conn.external_project_id):
        return False
    project_id = conn.external_project_id
    hook_id = conn.webhook_id_remote
    try:
        await ensure_group_fresh_for_connection(session, conn)
        async with GitLabClient(conn) as client:
            try:
                return await client.delete_webhook(project_id, hook_id)
            except httpx.HTTPStatusError as exc:
                log.warning(
                    "gitlab.webhook_delete_failed",
                    connection_id=str(conn.id),
                    status=exc.response.status_code,
                )
                return await _confirm_webhook_absent(
                    client, project_id, hook_id
                )
    except GitLabOAuthRefreshFailed:
        log.warning(
            "gitlab.webhook_revoke_skipped_oauth_on_disable",
            connection_id=str(conn.id),
        )
        return False
    except Exception as exc:
        log.warning(
            "gitlab.webhook_delete_failed",
            connection_id=str(conn.id),
            error=str(exc),
        )
        return False


def _public_webhook_url() -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/webhook/gitlab"


def _manual_webhook_instructions(webhook_url: str, secret: str) -> list[str]:
    return [
        "Open your GitLab project: Settings -> Webhooks",
        f"Paste the URL: {webhook_url}",
        f"Paste the Secret token: {secret}",
        "Enable triggers: Pipeline events, Job events",
        "Enable SSL verification",
        "Click 'Add webhook' to save",
    ]


def _parse_project_input(value: str, *, fallback_base: str) -> tuple[str, str]:
    """Return (base_url, project_ref) from a user input."""

    value = value.strip()
    if value.isdigit():
        return fallback_base.rstrip("/"), value
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.strip("/")
        return base, path
    return fallback_base.rstrip("/"), value


async def _get_or_create_placeholder_connection(
    session: AsyncSession,
    principal: CurrentPrincipal,
    *,
    base_url: str,
    client_id: str,
    client_secret: str | None,
) -> CIConnection:
    """Reuse an existing placeholder (no external_project_id) for this base_url, else create one."""
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITLAB,
        CIConnection.base_url == base_url,
        CIConnection.external_project_id.is_(None),
    )
    conn = (await session.execute(stmt)).scalars().first()
    if conn is None:
        conn = CIConnection(
            tenant_id=principal.tenant.id,
            provider=CIProvider.GITLAB,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
            status=ConnectionStatus.PENDING_MANUAL,
        )
        session.add(conn)
    conn.oauth_client_id = client_id
    if client_secret:
        conn.oauth_client_secret_enc = encrypt_str(client_secret)
    await session.flush()
    return conn


async def _upsert_project_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    base_conn: CIConnection,
    external_project_id: str,
    mode: IntegrationMode,
    enabled: bool = True,
) -> CIConnection:
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == tenant_id,
        CIConnection.provider == CIProvider.GITLAB,
        CIConnection.external_project_id == external_project_id,
    )
    conn = (await session.execute(stmt)).scalars().first()
    if conn is None:
        conn = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.GITLAB,
            mode=mode,
            base_url=base_conn.base_url,
            external_project_id=external_project_id,
            status=ConnectionStatus.PENDING_MANUAL,
            enabled=enabled,
        )
        session.add(conn)
    conn.mode = mode
    conn.base_url = base_conn.base_url
    conn.enabled = enabled
    await session.flush()
    return conn


async def _load_conn_for_tenant(
    session: AsyncSession, connection_id: uuid.UUID, tenant_id: uuid.UUID
) -> CIConnection:
    stmt = select(CIConnection).where(
        CIConnection.id == connection_id,
        CIConnection.tenant_id == tenant_id,
    )
    conn = (await session.execute(stmt)).scalars().first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn
