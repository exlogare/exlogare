from __future__ import annotations

import json
import secrets
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt_str
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
from app.services.ci.github_client import GitHubClient
from app.services.oauth.github import GitHubOAuthService
from app.services.selfhost_policy import (
    effective_max_github_repos,
    get_plan_spec,
    github_modes_allowed,
)

from sqlalchemy.orm.attributes import flag_modified

router = APIRouter(prefix="/api/integrations/github", tags=["integrations"])
log = get_logger(__name__)


class OAuthInitRequest(BaseModel):
    base_url: str = Field(default="https://github.com")
    client_id: str | None = None
    client_secret: str | None = None


class OAuthInitResponse(BaseModel):
    authorize_url: str


class RepoOut(BaseModel):
    id: str
    name: str
    path_with_namespace: str
    web_url: str
    default_branch: str | None = None
    last_activity_at: str | None = None


class WatchRequest(BaseModel):
    project_ids: list[str] = Field(min_length=1, description="Repository numeric ids (GitHub id)")
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
    base_url: str = Field(default="https://github.com")
    project: str = Field(description="Repo id, or owner/repo, or full URL")
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
    github_user: dict | None = None
    oauth_app_editable: bool = False
    oauth_client_id: str | None = None
    feedback_override: dict | None = None
    feedback_effective: dict | None = None


class UpdateGitHubOAuthAppRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = None


@router.post("/oauth/init", response_model=OAuthInitResponse)
async def oauth_init(
    body: OAuthInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> OAuthInitResponse:
    settings = get_settings()
    service = GitHubOAuthService()
    base_url = body.base_url.rstrip("/")
    pub = settings.github_base_url.rstrip("/")
    client_id = body.client_id
    client_secret = body.client_secret
    if not client_id and base_url == pub:
        client_id = settings.github_oauth_client_id
    if not client_secret and base_url == pub:
        client_secret = settings.github_oauth_client_secret
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="OAuth client_id required (platform env or per-tenant for GitHub Enterprise)",
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
    service = GitHubOAuthService()
    state_data = await service.consume_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    tenant_id = uuid.UUID(state_data["tenant_id"])
    base_url = (state_data.get("base_url") or settings.github_base_url).rstrip("/")
    redirect_uri = state_data.get("redirect_uri") or settings.github_oauth_redirect_uri
    connection_id = state_data.get("connection_id")
    client_id = state_data.get("client_id")
    client_secret = state_data.get("client_secret") or ""

    conn: CIConnection | None = None
    if connection_id:
        r = await session.execute(select(CIConnection).where(CIConnection.id == uuid.UUID(connection_id)))
        conn = r.scalar_one_or_none()
    if conn is None:
        conn = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.GITHUB,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
        )
        session.add(conn)
    conn.base_url = base_url
    conn.oauth_client_id = client_id
    conn.oauth_client_secret_enc = encrypt_str(client_secret) if client_secret else None
    conn.status = ConnectionStatus.ACTIVE

    token_payload = await service.exchange_code(
        code,
        base_url=base_url,
        client_id=client_id or settings.github_oauth_client_id,
        client_secret=client_secret or settings.github_oauth_client_secret,
        redirect_uri=redirect_uri,
    )
    service.apply_tokens_to_connection(conn, token_payload)
    uinfo = await service.fetch_user_info(connection=conn)
    if uinfo:
        ex = dict(conn.extra or {})
        ex["github_user"] = {
            "id": uinfo.get("id"),
            "login": uinfo.get("login"),
            "name": uinfo.get("name"),
            "email": uinfo.get("email"),
            "html_url": uinfo.get("html_url"),
        }
        conn.extra = ex
        flag_modified(conn, "extra")
        if uinfo.get("id") is not None:
            conn.oauth_user_id = str(uinfo.get("id"))

    await record_audit(
        session,
        tenant_id=tenant_id,
        action="github_oauth_connected",
        target=f"base_url:{base_url}",
        meta={"scope": token_payload.get("scope")},
    )
    await session.commit()
    web_url = f"{settings.web_base_url.rstrip('/')}/onboarding?github=connected&connection_id={conn.id}"
    return RedirectResponse(web_url)


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(
    connection_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[RepoOut]:
    base = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if base.provider != CIProvider.GITHUB:
        raise HTTPException(status_code=400, detail="Not a GitHub connection")
    out: list[RepoOut] = []
    try:
        async with GitHubClient(base) as client:
            items = await client.list_repos(search=search, per_page=100)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        ) from exc
    for r in items:
        if not isinstance(r, dict):
            continue
        fn = r.get("full_name") or ""
        out.append(
            RepoOut(
                id=str(r.get("id") or ""),
                name=r.get("name") or "",
                path_with_namespace=fn,
                web_url=r.get("html_url") or "",
                default_branch=r.get("default_branch"),
                last_activity_at=r.get("pushed_at") or r.get("updated_at"),
            )
        )
    return out


@router.post("/watch", response_model=WatchProjectsResponse)
async def watch_repos(
    body: WatchRequest,
    connection_id: uuid.UUID = Query(...),
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> WatchProjectsResponse:
    base_conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if base_conn.provider != CIProvider.GITHUB:
        raise HTTPException(status_code=400, detail="Not a GitHub base connection")
    try:
        mode = IntegrationMode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid mode") from exc
    spec = get_plan_spec(principal.tenant)
    if mode.value not in github_modes_allowed(principal.tenant, spec):
        raise HTTPException(status_code=400, detail="This integration mode is not available")
    max_r = effective_max_github_repos(spec)
    if max_r is None:
        budget = None
    else:
        n_start = int(
            (
                await session.execute(
                    select(func.count(CIConnection.id)).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.GITHUB,
                        CIConnection.external_project_id.isnot(None),
                        CIConnection.enabled.is_(True),
                    )
                )
            ).scalar()
            or 0
        )
        budget = [max(0, max_r - n_start)]

    def _slot(existing: CIConnection | None) -> bool:
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
    any_cap = False
    seen: set[str] = set()
    for raw in body.project_ids:
        pid = str(raw)
        if pid in seen:
            continue
        seen.add(pid)
        existing = (
            await session.execute(
                select(CIConnection).where(
                    CIConnection.tenant_id == principal.tenant.id,
                    CIConnection.provider == CIProvider.GITHUB,
                    CIConnection.external_project_id == pid,
                )
            )
        ).scalar_one_or_none()
        en = _slot(existing)
        row = await _upsert_project(
            session,
            tenant_id=principal.tenant.id,
            base=base_conn,
            project_id=pid,
            mode=mode,
            enabled=en,
        )
        row.oauth_access_token_enc = base_conn.oauth_access_token_enc
        row.oauth_refresh_token_enc = base_conn.oauth_refresh_token_enc
        row.oauth_token_expires_at = base_conn.oauth_token_expires_at
        row.oauth_client_id = base_conn.oauth_client_id
        row.oauth_client_secret_enc = base_conn.oauth_client_secret_enc

        err: str | None = None
        reg = False
        try:
            async with GitHubClient(row) as client:
                if not en:
                    await _try_revoke_hook(row, client)
                repo = await client.get_repository_by_id(pid)
                if repo is None:
                    err = "repository not found"
                    if not en:
                        row.status = ConnectionStatus.DISABLED
                    else:
                        row.status = ConnectionStatus.ERROR
                else:
                    row.external_project_name = repo.get("full_name")
                    row.external_project_url = repo.get("html_url")
                    extra = dict(row.extra or {})
                    extra["full_name"] = repo.get("full_name")
                    row.extra = extra
                    flag_modified(row, "extra")
                    if not en:
                        row.enabled = False
                        row.status = ConnectionStatus.DISABLED
                    elif mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID):
                        sec = secrets.token_urlsafe(32)
                        row.webhook_secret_enc = encrypt_str(sec)
                        wurl = _public_webhook_url()
                        fn = row.external_project_name
                        if fn:
                            hook = await client.register_webhook(
                                str(fn), url=wurl, token=sec
                            )
                            row.webhook_id_remote = str(hook.get("id"))
                            reg = True
                            row.status = ConnectionStatus.ACTIVE
                    else:
                        row.status = ConnectionStatus.ACTIVE
        except Exception as ex:
            err = str(ex)
            row.status = ConnectionStatus.ERROR if en else row.status
        if max_r is not None and not en and not err:
            any_cap = True
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="github_repo_watch",
            target=f"github:{pid}",
            meta={"mode": mode.value, "error": err, "enabled": row.enabled},
        )
        results.append(
            WatchResult(
                connection_id=str(row.id),
                project_id=pid,
                project_path=row.external_project_name,
                mode=mode.value,
                status=row.status.value,
                hook_registered=reg,
                enabled=row.enabled,
                error=err,
            )
        )
        await session.commit()
    return WatchProjectsResponse(results=results, repo_limit_partial=any_cap)


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    rows = (
        await session.execute(
            select(CIConnection).where(
                CIConnection.tenant_id == principal.tenant.id,
                CIConnection.provider == CIProvider.GITHUB,
            )
        )
    ).scalars().all()
    trow = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    out: list[ConnectionOut] = []
    for c in rows:
        ghu = (c.extra or {}).get("github_user") if isinstance(c.extra, dict) else None
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
                github_user=ghu,
                oauth_app_editable=_oauth_app_editable(c),
                oauth_client_id=c.oauth_client_id if _oauth_app_editable(c) else None,
                feedback_override=_extract_feedback(c),
                feedback_effective=resolve_feedback_policy(trow, c),
            )
        )
    return out


@router.delete("/watch/{connection_id}", status_code=status.HTTP_200_OK)
async def delete_connection(
    connection_id: uuid.UUID,
    purge: bool = False,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if conn.provider != CIProvider.GITHUB:
        raise HTTPException(status_code=400, detail="Not a GitHub connection")

    is_base = purge and conn.external_project_id is None
    cascaded: list[CIConnection] = []
    if is_base:
        cascaded = (
            (
                await session.execute(
                    select(CIConnection).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.GITHUB,
                        CIConnection.base_url == conn.base_url,
                        CIConnection.id != conn.id,
                    )
                )
            )
            .scalars()
            .all()
        )

    revoked = False
    # Try to revoke the parent's hook first (if any).
    if conn.webhook_id_remote and conn.external_project_name:
        try:
            async with GitHubClient(conn) as c:
                revoked = await c.delete_webhook(
                    str(conn.external_project_name), str(conn.webhook_id_remote)
                )
        except Exception as exc:
            log.warning(
                "github.hook_delete_failed",
                connection_id=str(conn.id),
                error=str(exc),
            )

    cascaded_revoked = 0
    for child in cascaded:
        if not (child.webhook_id_remote and child.external_project_name):
            continue
        try:
            async with GitHubClient(child) as cc:
                if await cc.delete_webhook(
                    str(child.external_project_name), str(child.webhook_id_remote)
                ):
                    cascaded_revoked += 1
        except Exception as exc:
            log.warning(
                "github.hook_delete_failed",
                connection_id=str(child.id),
                error=str(exc),
            )

    if revoked or purge:
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None
    if purge:
        cid = str(conn.id)
        cascaded_ids = [str(c.id) for c in cascaded]
        for child in cascaded:
            child.webhook_id_remote = None
            child.webhook_secret_enc = None
            await session.delete(child)
        await session.delete(conn)
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="github_connection_deleted",
            target=cid,
            meta={
                "cascaded": cascaded_ids,
                "cascaded_revoked": cascaded_revoked,
            }
            if cascaded_ids
            else None,
        )
        await session.commit()
        return {
            "status": "deleted",
            "revoked_remote": revoked,
            "cascaded": len(cascaded_ids),
        }
    conn.enabled = False
    conn.status = ConnectionStatus.DISABLED
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="github_connection_revoked",
        target=str(conn.id),
    )
    await session.commit()
    return {"status": "disabled", "revoked_remote": revoked}


@router.patch("/watch/{connection_id}", response_model=ChangeModeResponse)
async def change_connection(
    connection_id: uuid.UUID,
    body: ChangeModeRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ChangeModeResponse:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if conn.provider != CIProvider.GITHUB or not conn.external_project_id:
        raise HTTPException(status_code=400, detail="Invalid connection")
    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    if body.enabled is False:
        raise HTTPException(
            status_code=400, detail="Use DELETE on this endpoint to disable the connection"
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
    if body.enabled is True and not conn.enabled:
        spec = get_plan_spec(principal.tenant)
        max_r = effective_max_github_repos(spec)
        if max_r is not None:
            n_en = int(
                (
                    await session.execute(
                        select(func.count(CIConnection.id)).where(
                            CIConnection.tenant_id == principal.tenant.id,
                            CIConnection.provider == CIProvider.GITHUB,
                            CIConnection.external_project_id.isnot(None),
                            CIConnection.enabled.is_(True),
                        )
                    )
                ).scalar()
                or 0
            )
            if n_en >= max_r:
                raise HTTPException(
                    status_code=400,
                    detail="GitHub repository limit reached",
                )
        await _reenable_github_project(session, principal.tenant, conn)
    hook_revoked = False
    if body.mode is not None:
        try:
            new_mode = IntegrationMode(body.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown integration mode") from exc
        spec = get_plan_spec(principal.tenant)
        if new_mode.value not in github_modes_allowed(principal.tenant, spec):
            raise HTTPException(
                status_code=400, detail="This integration mode is not available"
            )
        if new_mode != conn.mode:
            old = conn.mode
            conn.mode = new_mode
            if new_mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID) and not conn.webhook_id_remote:
                sec = secrets.token_urlsafe(32)
                fn = conn.external_project_name
                if not fn:
                    raise HTTPException(
                        status_code=400, detail="Missing repository full name"
                    )
                async with GitHubClient(conn) as client:
                    hook = await client.register_webhook(
                        str(fn), url=_public_webhook_url(), token=sec
                    )
                conn.webhook_secret_enc = encrypt_str(sec)
                conn.webhook_id_remote = str(hook.get("id"))
            if old in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID) and new_mode == IntegrationMode.OAUTH_POLLING and conn.webhook_id_remote:
                try:
                    async with GitHubClient(conn) as client:
                        await client.delete_webhook(
                            str(conn.external_project_name or ""),
                            str(conn.webhook_id_remote),
                        )
                    hook_revoked = True
                except httpx.HTTPStatusError:
                    pass
                conn.webhook_id_remote = None
                conn.webhook_secret_enc = None
    if body.mode is None and not feedback_changed and body.enabled is not True:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if conn.status == ConnectionStatus.DISABLED and conn.enabled:
        conn.status = ConnectionStatus.ACTIVE
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="github_connection_updated",
        actor=principal.user.email,
        target=str(conn.id),
        meta={"feedback_changes": feedback_changes, "mode": body.mode},
    )
    await session.commit()
    return ChangeModeResponse(
        connection_id=str(conn.id),
        mode=conn.mode.value,
        status=conn.status.value,
        enabled=conn.enabled,
        hook_registered=bool(conn.webhook_id_remote),
        hook_revoked=hook_revoked,
        feedback_override=_extract_feedback(conn),
        feedback_effective=resolve_feedback_policy(tenant_row, conn),
    )


async def _reenable_github_project(
    session: AsyncSession, tenant: Tenant, conn: CIConnection
) -> None:
    if conn.mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID):
        sec = secrets.token_urlsafe(32)
        fn = conn.external_project_name
        if not fn:
            raise HTTPException(status_code=400, detail="Missing repository full name")
        async with GitHubClient(conn) as client:
            hook = await client.register_webhook(
                str(fn), url=_public_webhook_url(), token=sec
            )
        conn.webhook_secret_enc = encrypt_str(sec)
        conn.webhook_id_remote = str(hook.get("id"))
    conn.enabled = True
    conn.status = ConnectionStatus.ACTIVE


def _apply_feedback_override(
    conn: CIConnection, changes: dict[str, bool | str | None]
) -> dict[str, bool | None]:
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
        if key in CHANNELS:
            override[key] = bool(value)
            applied[key] = bool(value)
    if override:
        extra["feedback"] = override
    else:
        extra.pop("feedback", None)
    conn.extra = extra
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(conn, "extra")
    return applied


@router.patch("/oauth-app/{connection_id}")
async def update_github_oauth_app(
    connection_id: uuid.UUID,
    body: UpdateGitHubOAuthAppRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if not _oauth_app_editable(conn):
        raise HTTPException(
            status_code=400,
            detail="OAuth app credentials are only editable for the self-hosted host placeholder",
        )
    update_secret = body.client_secret is not None
    new_enc = encrypt_str(body.client_secret.strip()) if update_secret and body.client_secret else None
    for c in (
        await session.execute(
            select(CIConnection).where(
                CIConnection.tenant_id == principal.tenant.id,
                CIConnection.provider == CIProvider.GITHUB,
                CIConnection.base_url == conn.base_url,
            )
        )
    ).scalars().all():
        c.oauth_client_id = body.client_id.strip()
        if update_secret and new_enc:
            c.oauth_client_secret_enc = new_enc
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="github_oauth_app_updated",
        actor=principal.user.email,
        target=str(conn.id),
    )
    await session.commit()
    return {"status": "updated"}


@router.post("/webhook/verify/{connection_id}")
async def webhook_verify(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    c = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if c.last_delivery_at is not None:
        c.status = ConnectionStatus.ACTIVE
        await session.commit()
        return {"status": c.status.value, "verified": True}
    return {"status": c.status.value, "verified": False}


def _public_webhook_url() -> str:
    s = get_settings()
    return f"{s.public_base_url.rstrip('/')}/webhook/github"


async def _get_or_create_placeholder_connection(
    session: AsyncSession,
    principal: CurrentPrincipal,
    *,
    base_url: str,
    client_id: str,
    client_secret: str | None,
) -> CIConnection:
    st = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITHUB,
        CIConnection.base_url == base_url,
        CIConnection.external_project_id.is_(None),
    )
    conn = (await session.execute(st)).scalars().first()
    if conn is None:
        conn = CIConnection(
            tenant_id=principal.tenant.id,
            provider=CIProvider.GITHUB,
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


async def _upsert_project(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    base: CIConnection,
    project_id: str,
    mode: IntegrationMode,
    enabled: bool = True,
) -> CIConnection:
    st = select(CIConnection).where(
        CIConnection.tenant_id == tenant_id,
        CIConnection.provider == CIProvider.GITHUB,
        CIConnection.external_project_id == project_id,
    )
    row = (await session.execute(st)).scalars().first()
    if row is None:
        row = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.GITHUB,
            mode=mode,
            base_url=base.base_url,
            external_project_id=project_id,
            status=ConnectionStatus.PENDING_MANUAL,
            enabled=enabled,
        )
        session.add(row)
    row.mode = mode
    row.base_url = base.base_url
    row.enabled = enabled
    await session.flush()
    return row


async def _load_conn_for_tenant(
    session: AsyncSession, connection_id: uuid.UUID, tenant_id: uuid.UUID
) -> CIConnection:
    c = (
        await session.execute(
            select(CIConnection).where(
                CIConnection.id == connection_id,
                CIConnection.tenant_id == tenant_id,
            )
        )
    ).scalars().first()
    if c is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return c


def _oauth_app_editable(c: CIConnection) -> bool:
    if c.external_project_id is not None:
        return False
    return get_settings().github_base_url.rstrip("/") != c.base_url.rstrip("/")


def _extract_feedback(c: CIConnection) -> dict | None:
    extra = c.extra or {}
    raw = extra.get("feedback") if isinstance(extra, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    return {k: bool(v) for k, v in raw.items() if k in CHANNELS}


async def _try_revoke_hook(conn: CIConnection, client: GitHubClient) -> None:
    if not conn.webhook_id_remote or not conn.external_project_name:
        return
    try:
        await client.delete_webhook(
            str(conn.external_project_name), str(conn.webhook_id_remote)
        )
    except httpx.HTTPStatusError:
        pass
    finally:
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None
