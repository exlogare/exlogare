from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt_str
from app.core.db import get_db
from app.core.logging import get_logger
from app.models.ci_connection import (
    CIConnection,
    CIProvider,
    ConnectionStatus,
    IntegrationMode,
)
from app.services.audit import record_audit
from app.services.oauth.gitlab import GitLabOAuthService

router = APIRouter(prefix="/auth/gitlab", tags=["auth"])
log = get_logger(__name__)


@router.get("/callback")
async def callback(
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
    web_url = (
        f"{settings.web_base_url.rstrip('/')}/onboarding?gitlab=connected"
        f"&connection_id={conn.id}"
    )
    return RedirectResponse(web_url)
