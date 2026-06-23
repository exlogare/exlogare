"""GitFlic integration routes."""

from __future__ import annotations

import json
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.redis import get_redis
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
from app.services.audit import record_audit
from app.services.ci.gitflic_client import GitFlicClient
from app.services.oauth.gitflic import GitFlicOAuthService
from app.services.selfhost_policy import (
    effective_max_gitflic_repos,
    get_plan_spec,
    gitflic_modes_allowed,
)

router = APIRouter(prefix="/api/integrations/gitflic", tags=["integrations"])
log = get_logger(__name__)


class OAuthInitRequest(BaseModel):
    base_url: str = Field(default="https://gitflic.ru")
    oauth_base_url: str | None = None
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


class WatchRequest(BaseModel):
    project_paths: list[str] = Field(
        min_length=1,
        description="``owner_alias/project_alias`` strings (cloud or self-hosted).",
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
    gitflic_user: dict | None = None
    flavor: str = "cloud"


def _is_cloud(base_url: str) -> bool:
    settings = get_settings()
    return base_url.rstrip("/") == settings.gitflic_base_url.rstrip("/")


def _webhook_url() -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/webhook/gitflic"


async def _get_or_create_placeholder_connection(
    session: AsyncSession,
    principal: CurrentPrincipal,
    *,
    base_url: str,
    client_id: str | None,
    client_secret: str | None,
) -> CIConnection:
    """A PENDING_MANUAL row that holds the OAuth client_id/secret until"""
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITFLIC,
        CIConnection.base_url == base_url,
        CIConnection.external_project_id.is_(None),
    )
    conn = (await session.execute(stmt)).scalars().first()
    if conn is None:
        conn = CIConnection(
            tenant_id=principal.tenant.id,
            provider=CIProvider.GITFLIC,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
            status=ConnectionStatus.PENDING_MANUAL,
        )
        session.add(conn)
        await session.flush()
    if client_id:
        conn.oauth_client_id = client_id
    if client_secret:
        conn.oauth_client_secret_enc = encrypt_str(client_secret)
    return conn


def _mode_enum(value: str) -> IntegrationMode:
    try:
        return IntegrationMode(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {value}") from exc


_OAUTH_STATE_COOKIE = "gitflic_oauth_state"


@router.post("/oauth/init")
async def oauth_init(
    body: OAuthInitRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    settings = get_settings()
    base_url = body.base_url.rstrip("/")
    client_id = body.client_id or (
        settings.gitflic_oauth_client_id if _is_cloud(base_url) else ""
    )
    client_secret = body.client_secret or (
        settings.gitflic_oauth_client_secret if _is_cloud(base_url) else ""
    )
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "OAuth client_id required. Set GITFLIC_OAUTH_CLIENT_ID for "
                "the cloud instance or provide one per-connection for "
                "self-hosted."
            ),
        )
    placeholder = await _get_or_create_placeholder_connection(
        session,
        principal,
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
    )
    await session.commit()
    service = GitFlicOAuthService()
    try:
        url = await service.build_authorize_url(
            principal.tenant.id,
            base_url=base_url,
            oauth_base_url=body.oauth_base_url,
            client_id=client_id,
            client_secret=client_secret,
            connection_id=placeholder.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed_state = parse_qs(urlparse(url).query).get("state", [""])[0]
    response = JSONResponse({"authorize_url": url})
    if parsed_state:
        cookie_domain: str | None = None
        host = (request.url.hostname or "").lower()
        if host and host.count(".") >= 1 and not _looks_like_ip(host):
            parts = host.split(".")
            cookie_domain = "." + ".".join(parts[-2:])
        response.set_cookie(
            _OAUTH_STATE_COOKIE,
            parsed_state,
            max_age=_OAUTH_RESULT_TTL,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
            domain=cookie_domain,
        )
    return response


def _looks_like_ip(host: str) -> bool:
    return all(p.isdigit() for p in host.split("."))


_OAUTH_RESULT_TTL = 300  # seconds — long enough for the browser GET to follow.


def _result_key(state: str) -> str:
    return f"oauth:gitflic:result:{state}"


async def _process_oauth_callback(
    session: AsyncSession,
    *,
    code: str,
    state_data: dict,
) -> str:
    """Shared OAuth-callback work — exchange code, upsert connection,"""
    settings = get_settings()
    service = GitFlicOAuthService()

    tenant_id = uuid.UUID(state_data["tenant_id"])
    base_url = (state_data.get("base_url") or settings.gitflic_base_url).rstrip("/")
    raw_oauth = state_data.get("oauth_base_url")
    if raw_oauth:
        oauth_base_url = raw_oauth.rstrip("/")
    elif base_url == settings.gitflic_base_url.rstrip("/"):
        oauth_base_url = settings.gitflic_oauth_base_url.rstrip("/")
    else:
        oauth_base_url = base_url
    connection_id = state_data.get("connection_id")
    client_id = state_data.get("client_id")
    client_secret = state_data.get("client_secret") or ""

    conn: CIConnection | None = None
    if connection_id:
        conn = (
            await session.execute(
                select(CIConnection).where(CIConnection.id == uuid.UUID(connection_id))
            )
        ).scalar_one_or_none()
    if conn is None:
        conn = CIConnection(
            tenant_id=tenant_id,
            provider=CIProvider.GITFLIC,
            mode=IntegrationMode.OAUTH_POLLING,
            base_url=base_url,
        )
        session.add(conn)
    conn.base_url = base_url
    conn.oauth_client_id = client_id
    if client_secret:
        conn.oauth_client_secret_enc = encrypt_str(client_secret)
    conn.status = ConnectionStatus.ACTIVE
    extra = dict(conn.extra or {})
    extra["oauth_base_url"] = oauth_base_url
    if not _is_cloud(base_url):
        extra["api_base_url"] = f"{base_url}/rest-api"
    conn.extra = extra
    flag_modified(conn, "extra")

    token_payload = await service.exchange_code(
        code,
        oauth_base_url=oauth_base_url,
        client_id=client_id or settings.gitflic_oauth_client_id,
        client_secret=client_secret or settings.gitflic_oauth_client_secret,
    )
    service.apply_tokens_to_connection(conn, token_payload)

    api_base = extra.get("api_base_url") or settings.gitflic_api_base_url
    access = token_payload.get("accessToken") or token_payload.get("access_token") or ""
    uinfo = await service.fetch_user_info(
        api_base_url=str(api_base), access_token=access
    )
    if uinfo:
        extra["gitflic_user"] = {
            "id": uinfo.get("id"),
            "username": uinfo.get("username"),
            "display_name": uinfo.get("displayName") or uinfo.get("display_name"),
        }
        conn.extra = extra
        flag_modified(conn, "extra")
        if uinfo.get("id") is not None:
            conn.oauth_user_id = str(uinfo.get("id"))

    await session.flush()  # ensure conn.id is populated for the audit row.
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="gitflic_oauth_connected",
        target=str(conn.id),
    )
    await session.commit()
    return str(conn.id)


async def _stash_callback_result(state: str, payload: dict) -> None:
    """Persist callback outcome so the user's follow-up browser GET"""
    redis_client = get_redis()
    await redis_client.setex(_result_key(state), _OAUTH_RESULT_TTL, json.dumps(payload))


async def _read_callback_result(state: str) -> dict | None:
    redis_client = get_redis()
    raw = await redis_client.get(_result_key(state))
    if raw is None:
        return None
    await redis_client.delete(_result_key(state))
    try:
        return json.loads(raw)
    except Exception:
        return None


@router.post("/oauth/callback")
async def oauth_callback_post(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Server-to-server callback from GitFlic."""
    service = GitFlicOAuthService()
    body: dict = {}
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = {k: form.get(k) for k in form.keys()}
    except Exception:
        body = {}

    code = body.get("code") or request.query_params.get("code")
    state = body.get("state") or request.query_params.get("state")
    error = body.get("error") or request.query_params.get("error")

    if not state:
        return JSONResponse({"ok": False, "error": "missing_state"}, status_code=400)

    state_data = await service.consume_state(str(state))
    if not state_data:
        await _stash_callback_result(str(state), {"ok": False, "error": "invalid_state"})
        return JSONResponse({"ok": False, "error": "invalid_state"}, status_code=400)

    if error or not code:
        await _stash_callback_result(
            str(state), {"ok": False, "error": str(error or "oauth_failed")}
        )
        return JSONResponse({"ok": False, "error": str(error or "oauth_failed")})

    try:
        connection_id = await _process_oauth_callback(
            session, code=str(code), state_data=state_data
        )
    except Exception as exc:  # noqa: BLE001 - cache and report any failure.
        log.warning("gitflic_oauth.callback_failed", error=str(exc))
        await _stash_callback_result(
            str(state), {"ok": False, "error": "exchange_failed"}
        )
        return JSONResponse({"ok": False, "error": "exchange_failed"}, status_code=500)

    await _stash_callback_result(
        str(state), {"ok": True, "connection_id": connection_id}
    )
    return JSONResponse({"ok": True, "connection_id": connection_id})


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    web_base = settings.web_base_url.rstrip("/")
    service = GitFlicOAuthService()

    def _redirect(url: str) -> RedirectResponse:
        resp = RedirectResponse(url)
        host = (request.url.hostname or "").lower()
        cookie_domain: str | None = None
        if host and host.count(".") >= 1 and not _looks_like_ip(host):
            parts = host.split(".")
            cookie_domain = "." + ".".join(parts[-2:])
        resp.delete_cookie(
            _OAUTH_STATE_COOKIE,
            path="/",
            domain=cookie_domain,
        )
        return resp

    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    effective_state = state or cookie_state

    if effective_state and not code and not error:
        cached = await _read_callback_result(effective_state)
        if cached:
            if cached.get("ok"):
                return _redirect(
                    f"{web_base}/onboarding?gitflic=connected"
                    + (f"&connection_id={cached['connection_id']}" if cached.get("connection_id") else "")
                )
            return _redirect(
                f"{web_base}/onboarding?gitflic=error&error={cached.get('error', 'oauth_failed')}"
            )

    if error or not code:
        if effective_state:
            await service.consume_state(effective_state)
        return _redirect(
            f"{web_base}/onboarding?gitflic=error&error={error or 'oauth_failed'}"
        )

    if not effective_state:
        raise HTTPException(status_code=400, detail="Missing OAuth state")
    state_data = await service.consume_state(effective_state)
    if not state_data:
        cached = await _read_callback_result(effective_state)
        if cached and cached.get("ok"):
            return _redirect(
                f"{web_base}/onboarding?gitflic=connected"
                + (f"&connection_id={cached['connection_id']}" if cached.get("connection_id") else "")
            )
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    connection_id = await _process_oauth_callback(
        session, code=code, state_data=state_data
    )
    return _redirect(
        f"{web_base}/onboarding?gitflic=connected&connection_id={connection_id}"
    )


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    base_url: str = Query(default="https://gitflic.ru"),
    search: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    base_url = base_url.rstrip("/")
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITFLIC,
        CIConnection.base_url == base_url,
        CIConnection.oauth_access_token_enc.is_not(None),
    )
    conn = (await session.execute(stmt)).scalars().first()
    if conn is None:
        raise HTTPException(
            status_code=400,
            detail="Connect GitFlic via OAuth before listing projects.",
        )
    async with GitFlicClient(conn) as client:
        items = await client.list_projects(search=search)
    out: list[ProjectOut] = []
    for p in items:
        owner_obj = p.get("owner") or {}
        owner = (
            (owner_obj.get("alias") if isinstance(owner_obj, dict) else None)
            or (owner_obj.get("username") if isinstance(owner_obj, dict) else None)
            or p.get("ownerAlias")
            or p.get("owner_alias")
            or ""
        )
        alias = p.get("alias") or p.get("name") or ""
        if not owner or not alias:
            log.warning(
                "gitflic.list_projects.skip_unaddressable",
                project_id=str(p.get("id") or ""),
                has_owner=bool(owner),
                has_alias=bool(alias),
            )
            continue
        path = f"{owner}/{alias}"
        web = p.get("url") or f"{base_url}/project/{path}"
        out.append(
            ProjectOut(
                id=path or str(p.get("id") or ""),
                name=p.get("title") or alias,
                path_with_namespace=path,
                web_url=web,
                default_branch=p.get("defaultBranch"),
            )
        )
    return out


@router.post("/watch", response_model=WatchProjectsResponse)
async def watch_projects(
    body: WatchRequest,
    base_url: str = Query(default="https://gitflic.ru"),
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> WatchProjectsResponse:
    base_url = base_url.rstrip("/")
    spec = get_plan_spec(principal.tenant)
    allowed_modes = gitflic_modes_allowed(principal.tenant, spec)
    if body.mode not in allowed_modes:
        raise HTTPException(
            status_code=403,
            detail=f"Mode '{body.mode}' is not available on your plan.",
        )
    cap = effective_max_gitflic_repos(spec)
    if cap is not None:
        existing = (
            await session.execute(
                select(func.count())
                .select_from(CIConnection)
                .where(
                    CIConnection.tenant_id == principal.tenant.id,
                    CIConnection.provider == CIProvider.GITFLIC,
                    CIConnection.external_project_id.is_not(None),
                )
            )
        ).scalar() or 0
    else:
        existing = 0

    # Find the OAuth-bearing connection for API calls.
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITFLIC,
        CIConnection.base_url == base_url,
        CIConnection.oauth_access_token_enc.is_not(None),
    )
    auth_conn = (await session.execute(stmt)).scalars().first()
    if auth_conn is None:
        raise HTTPException(
            status_code=400,
            detail="Connect GitFlic via OAuth before watching projects.",
        )

    results: list[WatchResult] = []
    partial = False
    for path in body.project_paths:
        if cap is not None and existing >= cap:
            partial = True
            break
        async with GitFlicClient(auth_conn) as client:
            try:
                project = await client.get_project(path)
            except Exception as exc:  # noqa: BLE001 - surface as per-row error.
                log.warning(
                    "gitflic.get_project.failed",
                    project_path=path,
                    error=str(exc),
                )
                project = None
            if project is None:
                results.append(
                    WatchResult(
                        connection_id="",
                        project_id="",
                        project_path=path,
                        mode=body.mode,
                        status="error",
                        hook_registered=False,
                        error=(
                            "Project not found or inaccessible. "
                            "Expected path: owner_alias/project_alias."
                        ),
                    )
                )
                continue
            pid = str(project.get("id") or project.get("projectId") or "")
            web_url = project.get("url") or f"{base_url}/project/{path}"
            # Upsert connection row keyed on external_project_id.
            existing_q = await session.execute(
                select(CIConnection).where(
                    CIConnection.tenant_id == principal.tenant.id,
                    CIConnection.provider == CIProvider.GITFLIC,
                    CIConnection.external_project_id == pid,
                )
            )
            conn = existing_q.scalars().first()
            if conn is None:
                conn = CIConnection(
                    tenant_id=principal.tenant.id,
                    provider=CIProvider.GITFLIC,
                    mode=_mode_enum(body.mode),
                    base_url=base_url,
                    external_project_id=pid,
                    external_project_name=path,
                    external_project_url=web_url,
                    status=ConnectionStatus.PENDING_MANUAL,
                )
                conn.oauth_access_token_enc = auth_conn.oauth_access_token_enc
                conn.oauth_refresh_token_enc = auth_conn.oauth_refresh_token_enc
                conn.oauth_token_expires_at = auth_conn.oauth_token_expires_at
                conn.oauth_client_id = auth_conn.oauth_client_id
                conn.oauth_client_secret_enc = auth_conn.oauth_client_secret_enc
                conn.extra = dict(auth_conn.extra or {})
                session.add(conn)
                await session.flush()
                existing += 1
            else:
                conn.mode = _mode_enum(body.mode)
                conn.enabled = True

            hook_registered = False
            if body.mode in ("webhook", "hybrid"):
                secret = secrets.token_urlsafe(24)
                try:
                    hook = await client.register_webhook(
                        path,
                        url=f"{_webhook_url()}?secret={secret}",
                        secret=secret,
                    )
                    conn.webhook_secret_enc = encrypt_str(secret)
                    hook_id = hook.get("id") or (hook.get("data") or {}).get("id")
                    if hook_id:
                        conn.webhook_id_remote = str(hook_id)
                    conn.status = ConnectionStatus.ACTIVE
                    hook_registered = True
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "gitflic.register_webhook.failed",
                        project_path=path,
                        error=str(exc),
                    )

        results.append(
            WatchResult(
                connection_id=str(conn.id),
                project_id=pid,
                project_path=path,
                mode=body.mode,
                status=conn.status.value,
                hook_registered=hook_registered,
                enabled=conn.enabled,
            )
        )

    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitflic_watch_projects",
        meta={"count": len(results), "partial": partial},
    )
    await session.commit()
    return WatchProjectsResponse(results=results, repo_limit_partial=partial)


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(
    principal: CurrentPrincipal = Depends(require_non_viewer),
    session: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == principal.tenant.id,
        CIConnection.provider == CIProvider.GITFLIC,
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[ConnectionOut] = []
    for c in rows:
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
                last_delivery_at=(
                    c.last_delivery_at.isoformat() if c.last_delivery_at else None
                ),
                gitflic_user=(c.extra or {}).get("gitflic_user"),
                flavor="cloud" if _is_cloud(c.base_url) else "selfhosted",
            )
        )
    return out


@router.patch("/connections/{connection_id}/mode")
async def change_mode(
    connection_id: uuid.UUID,
    body: ChangeModeRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> dict:
    conn = await session.get(CIConnection, connection_id)
    if conn is None or conn.tenant_id != principal.tenant.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if body.mode is not None:
        conn.mode = _mode_enum(body.mode)
    if body.enabled is not None:
        conn.enabled = body.enabled
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitflic_change_mode",
        target=str(conn.id),
        meta={"mode": conn.mode.value, "enabled": conn.enabled},
    )
    await session.commit()
    return {
        "connection_id": str(conn.id),
        "mode": conn.mode.value,
        "enabled": conn.enabled,
    }


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    connection_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> None:
    conn = await session.get(CIConnection, connection_id)
    if conn is None or conn.tenant_id != principal.tenant.id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if (
        conn.external_project_name
        and conn.webhook_id_remote
        and conn.oauth_access_token_enc
    ):
        try:
            async with GitFlicClient(conn) as client:
                await client.delete_webhook(
                    conn.external_project_name, conn.webhook_id_remote
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "gitflic.disconnect.webhook_delete_failed",
                connection_id=str(conn.id),
                error=str(exc),
            )
    await session.delete(conn)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="gitflic_disconnect",
        target=str(connection_id),
    )
    await session.commit()
