"""Bitbucket integration routes (Cloud OAuth + DC webhook-init)."""

from __future__ import annotations

import secrets
import uuid
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

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
from app.services.ci.bitbucket_client import BitbucketClient
from app.services.ci.feedback_policy import CHANNELS, resolve_feedback_policy
from app.services.oauth.bitbucket import BitbucketOAuthService, is_bitbucket_cloud
from app.services.selfhost_policy import (
    bitbucket_modes_allowed,
    effective_max_bitbucket_repos,
    get_plan_spec,
)

router = APIRouter(prefix="/api/integrations/bitbucket", tags=["integrations"])
log = get_logger(__name__)


class OAuthInitRequest(BaseModel):
    base_url: str = Field(default="https://bitbucket.org")
    client_id: str | None = None
    client_secret: str | None = None


class OAuthInitResponse(BaseModel):
    authorize_url: str


class RepoOut(BaseModel):
    id: str  # for Bitbucket: ``workspace/slug`` (rename-resilient is delegated
    # to the webhook resolver via UUID stored in ``extra``)
    name: str
    path_with_namespace: str  # workspace/slug — duplicated for parity with GitLab
    web_url: str
    default_branch: str | None = None
    last_activity_at: str | None = None


class WatchRequest(BaseModel):
    project_ids: list[str] = Field(
        min_length=1,
        description=(
            "Bitbucket Cloud: ``workspace/slug`` strings. Atlassian retired "
            "the ``GET /2.0/repositories/{uuid}`` lookup in 2026, so we no "
            "longer resolve the workspace from a bare UUID — the picker is "
            "expected to send the human-readable path instead."
        ),
    )
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
    """DC self-hosted: tenant pastes base URL + project key + repo slug."""

    base_url: str = Field(description="Bitbucket DC base URL, e.g. https://bitbucket.example.com")
    project_key: str = Field(description="DC project key, e.g. 'PROJ'")
    repo_slug: str = Field(description="Repository slug, e.g. 'my-repo'")
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
    legacy_dc_warning: bool = False


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
    bitbucket_user: dict | None = None
    oauth_app_editable: bool = False
    oauth_client_id: str | None = None
    feedback_override: dict | None = None
    feedback_effective: dict | None = None
    flavor: str = "cloud"


class UpdateOAuthAppRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = None


@router.post("/oauth/init", response_model=OAuthInitResponse)
async def oauth_init(
    body: OAuthInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> OAuthInitResponse:
    settings = get_settings()
    base_url = body.base_url.rstrip("/")
    if not is_bitbucket_cloud(base_url):
        raise HTTPException(
            status_code=400,
            detail=(
                "Bitbucket Data Center / self-hosted does not support OAuth in this "
                "phase. Use /api/integrations/bitbucket/webhook/init instead."
            ),
        )
    service = BitbucketOAuthService()
    client_id = body.client_id or settings.bitbucket_oauth_client_id
    client_secret = body.client_secret or settings.bitbucket_oauth_client_secret
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="OAuth client_id required (set BITBUCKET_OAUTH_CLIENT_ID or supply per-tenant)",
        )
    placeholder = await _get_or_create_placeholder_connection(
        session,
        principal,
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
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
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """OAuth callback — accepts both success (``code``+``state``) and error"""
    settings = get_settings()
    service = BitbucketOAuthService()
    web_base = settings.web_base_url.rstrip("/")

    if error or not code:
        log.warning(
            "bitbucket.oauth_callback_error",
            error=error,
            description=error_description,
            has_code=bool(code),
            has_state=bool(state),
        )
        if state:
            # Clean up the Redis state entry so the tenant can retry.
            await service.consume_state(state)
        msg = error_description or error or "Bitbucket did not return an authorization code"
        # Strip any newlines / control chars that might break the redirect.
        msg = " ".join(msg.split())[:500]
        return RedirectResponse(
            f"{web_base}/onboarding"
            f"?bitbucket=error&error={quote_plus(error or 'oauth_failed')}"
            f"&error_description={quote_plus(msg)}"
        )

    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state")
    state_data = await service.consume_state(state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    tenant_id = uuid.UUID(state_data["tenant_id"])
    base_url = (state_data.get("base_url") or settings.bitbucket_base_url).rstrip("/")
    redirect_uri = (
        state_data.get("redirect_uri") or settings.bitbucket_oauth_redirect_uri
    )
    connection_id = state_data.get("connection_id")
    client_id = state_data.get("client_id")
    client_secret = state_data.get("client_secret") or ""

    conn: CIConnection | None = None
    if connection_id:
        r = await session.execute(
            select(CIConnection).where(CIConnection.id == uuid.UUID(connection_id))
        )
        conn = r.scalar_one_or_none()
    if conn is None:
        conn = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.BITBUCKET,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
        )
        session.add(conn)
    conn.base_url = base_url
    conn.oauth_client_id = client_id
    conn.oauth_client_secret_enc = (
        encrypt_str(client_secret) if client_secret else None
    )
    conn.status = ConnectionStatus.ACTIVE

    token_payload = await service.exchange_code(
        code,
        base_url=base_url,
        client_id=client_id or settings.bitbucket_oauth_client_id,
        client_secret=client_secret or settings.bitbucket_oauth_client_secret,
        redirect_uri=redirect_uri,
    )
    service.apply_tokens_to_connection(conn, token_payload)
    uinfo = await service.fetch_user_info(connection=conn)
    if uinfo:
        ex = dict(conn.extra or {})
        ex["bitbucket_user"] = {
            "uuid": uinfo.get("uuid"),
            "username": uinfo.get("username"),
            "display_name": uinfo.get("display_name"),
            "account_id": uinfo.get("account_id"),
            "links": uinfo.get("links"),
        }
        conn.extra = ex
        flag_modified(conn, "extra")
        if uinfo.get("uuid") is not None:
            conn.oauth_user_id = str(uinfo.get("uuid"))

    await record_audit(
        session,
        tenant_id=tenant_id,
        action="bitbucket_oauth_connected",
        target=f"base_url:{base_url}",
        meta={"scope": token_payload.get("scopes") or token_payload.get("scope")},
    )
    await session.commit()
    web_url = (
        f"{settings.web_base_url.rstrip('/')}"
        f"/onboarding?bitbucket=connected&connection_id={conn.id}"
    )
    return RedirectResponse(web_url)


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(
    connection_id: uuid.UUID = Query(...),
    workspace: str | None = Query(default=None),
    search: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[RepoOut]:
    base = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if base.provider != CIProvider.BITBUCKET:
        raise HTTPException(status_code=400, detail="Not a Bitbucket connection")
    if not is_bitbucket_cloud(base.base_url):
        raise HTTPException(
            status_code=400,
            detail=(
                "Repo browsing is Cloud-only. For DC, register webhooks per-repository "
                "via /webhook/init."
            ),
        )
    out: list[RepoOut] = []
    try:
        async with BitbucketClient(base) as client:
            items = await client.list_repos(
                workspace=workspace, search=search, per_page=100
            )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        ) from exc
    for r in items:
        if not isinstance(r, dict):
            continue
        full_name = r.get("full_name") or ""
        if not full_name or "/" not in full_name:
            continue
        links = r.get("links") or {}
        html = (links.get("html") or {}).get("href") or f"https://bitbucket.org/{full_name}"
        out.append(
            RepoOut(
                id=full_name,  # frontend echoes this back as project_ids[i]
                name=r.get("name") or "",
                path_with_namespace=full_name,
                web_url=html,
                default_branch=(r.get("mainbranch") or {}).get("name")
                if isinstance(r.get("mainbranch"), dict)
                else None,
                last_activity_at=r.get("updated_on"),
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
    if base_conn.provider != CIProvider.BITBUCKET:
        raise HTTPException(status_code=400, detail="Not a Bitbucket base connection")
    if not is_bitbucket_cloud(base_conn.base_url):
        raise HTTPException(
            status_code=400,
            detail="Bitbucket DC connections must be created via /webhook/init",
        )
    try:
        mode = IntegrationMode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid mode") from exc
    spec = get_plan_spec(principal.tenant)
    if mode.value not in bitbucket_modes_allowed(principal.tenant, spec):
        raise HTTPException(
            status_code=400,
            detail="This integration mode is not available",
        )
    max_r = effective_max_bitbucket_repos(spec)
    if max_r is None:
        budget = None
    else:
        n_start = int(
            (
                await session.execute(
                    select(func.count(CIConnection.id)).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.BITBUCKET,
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
                    CIConnection.provider == CIProvider.BITBUCKET,
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
        if "/" not in pid:
            err = (
                "Bitbucket project_ids must be 'workspace/slug' paths "
                "(received UUID-only id)"
            )
            log.warning(
                "bitbucket.watch_invalid_project_id",
                tenant_id=str(principal.tenant.id),
                project_id=pid,
            )
            row.status = ConnectionStatus.ERROR if en else row.status
            await record_audit(
                session,
                tenant_id=principal.tenant.id,
                action="bitbucket_repo_watch",
                target=f"bitbucket:{pid}",
                meta={"mode": mode.value, "error": err, "enabled": row.enabled},
            )
            results.append(
                WatchResult(
                    connection_id=str(row.id),
                    project_id=pid,
                    project_path=row.external_project_name,
                    mode=mode.value,
                    status=row.status.value,
                    hook_registered=False,
                    enabled=row.enabled,
                    error=err,
                )
            )
            await session.commit()
            continue
        full_name = pid
        ws_str, slug_str = full_name.split("/", 1)
        try:
            async with BitbucketClient(row) as client:
                if not en:
                    await _try_revoke_hook_cloud(row, client)
                try:
                    repo = await client.get_repo(ws_str, slug_str)
                except httpx.HTTPStatusError as exc:
                    log.warning(
                        "bitbucket.watch_repo_lookup_failed",
                        full_name=full_name,
                        status=exc.response.status_code if exc.response else None,
                    )
                    repo = {}

                row.external_project_name = full_name
                links = repo.get("links") or {}
                html = (links.get("html") or {}).get("href") or f"https://bitbucket.org/{full_name}"
                row.external_project_url = html
                repo_uuid = (
                    str(repo.get("uuid")) if repo.get("uuid") else None
                )
                row.external_project_id = repo_uuid or full_name
                extra = dict(row.extra or {})
                extra["full_name"] = full_name
                extra["workspace"] = ws_str
                extra["repo_slug"] = slug_str
                if repo_uuid:
                    extra["uuid"] = repo_uuid
                row.extra = extra
                flag_modified(row, "extra")

                if not en:
                    row.enabled = False
                    row.status = ConnectionStatus.DISABLED
                elif mode in (IntegrationMode.WEBHOOK, IntegrationMode.HYBRID):
                    sec = secrets.token_urlsafe(32)
                    row.webhook_secret_enc = encrypt_str(sec)
                    wurl = _public_webhook_url()
                    try:
                        hook = await client.register_webhook(
                            ws_str,
                            slug_str,
                            url=wurl,
                            secret=sec,
                            repo_uuid=repo_uuid,
                        )
                    except httpx.HTTPStatusError as exc:
                        body = ""
                        try:
                            body = exc.response.text[:500] if exc.response else ""
                        except Exception:  # noqa: BLE001
                            body = ""
                        log.error(
                            "bitbucket.webhook_register_failed",
                            full_name=full_name,
                            status=exc.response.status_code if exc.response else None,
                            body=body,
                        )
                        row.status = ConnectionStatus.ERROR
                        row.webhook_secret_enc = None
                        raise RuntimeError(
                            f"webhook registration failed: HTTP "
                            f"{exc.response.status_code if exc.response else '???'} "
                            f"{body}"
                        ) from exc
                    row.webhook_id_remote = str(hook.get("uuid") or hook.get("id"))
                    reg = True
                    row.status = ConnectionStatus.ACTIVE
                else:
                    row.status = ConnectionStatus.ACTIVE
        except Exception as ex:  # noqa: BLE001
            err = str(ex)
            log.warning(
                "bitbucket.watch_failed",
                full_name=full_name,
                error=err,
            )
            row.status = ConnectionStatus.ERROR if en else row.status
        if max_r is not None and not en and not err:
            any_cap = True
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="bitbucket_repo_watch",
            target=f"bitbucket:{pid}",
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


@router.post("/webhook/init", response_model=WebhookInitResponse)
async def webhook_init(
    body: WebhookInitRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> WebhookInitResponse:
    """DC self-hosted: create a webhook-only CIConnection."""
    base_url = body.base_url.rstrip("/")
    if is_bitbucket_cloud(base_url):
        raise HTTPException(
            status_code=400,
            detail=(
                "/webhook/init is intended for Bitbucket Data Center / Server. "
                "For Cloud use /oauth/init."
            ),
        )
    project_key = body.project_key.strip()
    repo_slug = body.repo_slug.strip()
    if not project_key or not repo_slug:
        raise HTTPException(
            status_code=400, detail="project_key and repo_slug are required"
        )

    secret = secrets.token_urlsafe(32)
    webhook_url = _public_webhook_url()

    project_path = f"{project_key}/{repo_slug}"
    conn = CIConnection(
        tenant_id=principal.tenant.id,
        provider=CIProvider.BITBUCKET,
        mode=IntegrationMode.WEBHOOK,
        base_url=base_url,
        status=ConnectionStatus.PENDING_MANUAL,
        webhook_secret_enc=encrypt_str(secret),
        external_project_id=project_path,  # DC has no UUID; use KEY/slug as id
        external_project_name=project_path,
        external_project_url=f"{base_url}/projects/{project_key}/repos/{repo_slug}/browse",
        extra={
            "project_key": project_key,
            "repo_slug": repo_slug,
            "flavor": "server",
        },
    )
    if body.personal_access_token:
        conn.api_token_enc = encrypt_str(body.personal_access_token)
    session.add(conn)
    await session.flush()

    hook_registered = False
    instructions: list[str] = _dc_manual_webhook_instructions(
        webhook_url, secret, project_key=project_key, repo_slug=repo_slug
    )
    if body.personal_access_token:
        try:
            async with BitbucketClient(conn) as client:
                hook = await client.register_webhook(
                    project_key, repo_slug, url=webhook_url, secret=secret
                )
                conn.webhook_id_remote = str(hook.get("id") or "")
                hook_registered = bool(conn.webhook_id_remote)
                if hook_registered:
                    conn.status = ConnectionStatus.ACTIVE
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bitbucket.dc_webhook_register_failed",
                connection_id=str(conn.id),
                error=str(exc),
            )

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="bitbucket_webhook_connected",
        actor=principal.user.email,
        target=f"bitbucket-dc:{project_path}",
        meta={"hook_registered": hook_registered, "base_url": base_url},
    )
    await session.commit()
    return WebhookInitResponse(
        connection_id=str(conn.id),
        project_id=conn.external_project_id or "",
        project_path=conn.external_project_name,
        mode=conn.mode.value,
        status=conn.status.value,
        webhook_url=webhook_url,
        webhook_secret=secret,
        hook_registered=hook_registered,
        instructions=instructions,
        legacy_dc_warning=True,
    )


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    rows = (
        await session.execute(
            select(CIConnection).where(
                CIConnection.tenant_id == principal.tenant.id,
                CIConnection.provider == CIProvider.BITBUCKET,
            )
        )
    ).scalars().all()
    trow = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    out: list[ConnectionOut] = []
    for c in rows:
        bbu = (c.extra or {}).get("bitbucket_user") if isinstance(c.extra, dict) else None
        flavor = "cloud" if is_bitbucket_cloud(c.base_url) else "server"
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
                last_delivery_at=c.last_delivery_at.isoformat()
                if c.last_delivery_at
                else None,
                bitbucket_user=bbu,
                oauth_app_editable=_oauth_app_editable(c),
                oauth_client_id=c.oauth_client_id if _oauth_app_editable(c) else None,
                feedback_override=_extract_feedback(c),
                feedback_effective=resolve_feedback_policy(trow, c),
                flavor=flavor,
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
    if conn.provider != CIProvider.BITBUCKET:
        raise HTTPException(status_code=400, detail="Not a Bitbucket connection")

    is_base = purge and conn.external_project_id is None
    cascaded: list[CIConnection] = []
    if is_base:
        cascaded = (
            (
                await session.execute(
                    select(CIConnection).where(
                        CIConnection.tenant_id == principal.tenant.id,
                        CIConnection.provider == CIProvider.BITBUCKET,
                        CIConnection.base_url == conn.base_url,
                        CIConnection.id != conn.id,
                    )
                )
            )
            .scalars()
            .all()
        )

    revoked = await _revoke_hook(conn)
    cascaded_revoked = 0
    for child in cascaded:
        if await _revoke_hook(child):
            cascaded_revoked += 1

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
            action="bitbucket_connection_deleted",
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
        action="bitbucket_connection_revoked",
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
    if conn.provider != CIProvider.BITBUCKET or not conn.external_project_id:
        raise HTTPException(status_code=400, detail="Invalid connection")
    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.id == principal.tenant.id))
    ).scalar_one()
    if body.enabled is False:
        raise HTTPException(
            status_code=400,
            detail="Use DELETE on this endpoint to disable the connection",
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
    hook_revoked = False
    if body.mode is not None:
        try:
            new_mode = IntegrationMode(body.mode)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Unknown integration mode"
            ) from exc
        # DC connections are pinned to webhook mode regardless of the plan.
        if not is_bitbucket_cloud(conn.base_url) and new_mode != IntegrationMode.WEBHOOK:
            raise HTTPException(
                status_code=400,
                detail="Bitbucket Data Center connections only support webhook mode",
            )
        spec = get_plan_spec(principal.tenant)
        if new_mode.value not in bitbucket_modes_allowed(principal.tenant, spec):
            raise HTTPException(
                status_code=400,
                detail="This integration mode is not available",
            )
        conn.mode = new_mode
    if body.mode is None and not feedback_changed:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if conn.status == ConnectionStatus.DISABLED and conn.enabled:
        conn.status = ConnectionStatus.ACTIVE
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="bitbucket_connection_updated",
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


@router.patch("/oauth-app/{connection_id}")
async def update_bitbucket_oauth_app(
    connection_id: uuid.UUID,
    body: UpdateOAuthAppRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    conn = await _load_conn_for_tenant(session, connection_id, principal.tenant.id)
    if not _oauth_app_editable(conn):
        raise HTTPException(
            status_code=400,
            detail="OAuth app credentials are only editable for self-hosted host placeholders",
        )
    update_secret = body.client_secret is not None
    new_enc = (
        encrypt_str(body.client_secret.strip())
        if update_secret and body.client_secret
        else None
    )
    for c in (
        await session.execute(
            select(CIConnection).where(
                CIConnection.tenant_id == principal.tenant.id,
                CIConnection.provider == CIProvider.BITBUCKET,
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
        action="bitbucket_oauth_app_updated",
        actor=principal.user.email,
        target=str(conn.id),
    )
    await session.commit()
    return {"status": "updated"}


def _public_webhook_url() -> str:
    s = get_settings()
    return f"{s.public_base_url.rstrip('/')}/webhook/bitbucket"


def _dc_manual_webhook_instructions(
    webhook_url: str, secret: str, *, project_key: str, repo_slug: str
) -> list[str]:
    return [
        f"Open Bitbucket: Project {project_key} → Repository {repo_slug} → Repository settings → Webhooks",
        "Click 'Create webhook'",
        "Name: Exlogare RCA",
        f"URL: {webhook_url}",
        f"Secret: {secret}",
        "Events: Build status updated (under Repository events)",
        "Status: Active",
        "Click 'Create' to save",
        (
            "If your DC version is < 7.4 the 'Build status updated' event is unavailable. "
            "Either upgrade to 7.4+ or use POST /api/analyze for manual ingestion."
        ),
    ]


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
        CIConnection.provider == CIProvider.BITBUCKET,
        CIConnection.base_url == base_url,
        CIConnection.external_project_id.is_(None),
    )
    conn = (await session.execute(st)).scalars().first()
    if conn is None:
        conn = CIConnection(
            tenant_id=principal.tenant.id,
            provider=CIProvider.BITBUCKET,
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
        CIConnection.provider == CIProvider.BITBUCKET,
        CIConnection.external_project_id == project_id,
    )
    row = (await session.execute(st)).scalars().first()
    if row is None:
        row = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.BITBUCKET,
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
    return get_settings().bitbucket_base_url.rstrip("/") != c.base_url.rstrip("/")


def _extract_feedback(c: CIConnection) -> dict | None:
    extra = c.extra or {}
    raw = extra.get("feedback") if isinstance(extra, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    return {k: bool(v) for k, v in raw.items() if k in CHANNELS}


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
    flag_modified(conn, "extra")
    return applied


def _conn_repo_uuid(conn: CIConnection) -> str | None:
    """Return the repo UUID we cached during ``/watch`` (or ``None``)."""
    extra = conn.extra if isinstance(conn.extra, dict) else None
    if extra:
        cached = extra.get("uuid")
        if isinstance(cached, str) and cached.strip():
            return cached.strip()
    epid = (conn.external_project_id or "").strip()
    if epid.startswith("{") and epid.endswith("}"):
        return epid
    return None


async def _try_revoke_hook_cloud(conn: CIConnection, client: BitbucketClient) -> None:
    if not conn.webhook_id_remote or not conn.external_project_name:
        return
    fn = conn.external_project_name
    if "/" not in fn:
        return
    ws, slug = fn.split("/", 1)
    try:
        await client.delete_webhook(
            ws,
            slug,
            str(conn.webhook_id_remote),
            repo_uuid=_conn_repo_uuid(conn),
        )
    except httpx.HTTPStatusError:
        pass
    finally:
        conn.webhook_id_remote = None
        conn.webhook_secret_enc = None


async def _revoke_hook(conn: CIConnection) -> bool:
    """Best-effort delete the remote webhook for a connection."""
    if not conn.webhook_id_remote or not conn.external_project_name:
        return False
    fn = conn.external_project_name
    if "/" not in fn:
        return False
    ws, slug = fn.split("/", 1)
    try:
        async with BitbucketClient(conn) as client:
            return await client.delete_webhook(
                ws,
                slug,
                str(conn.webhook_id_remote),
                repo_uuid=_conn_repo_uuid(conn),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "bitbucket.hook_delete_failed",
            connection_id=str(conn.id),
            error=str(exc),
        )
        return False
