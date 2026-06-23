from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.ci_connection import CIConnection

log = get_logger(__name__)

_DEFAULT_SCOPE = "api read_user read_api"
_STATE_TTL_SECONDS = 600


class GitLabOAuthRefreshFailed(Exception):
    """GitLab rejected ``grant_type=refresh_token`` (expired/revoked token or bad client)."""

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or (
            "GitLab refused to refresh the OAuth token. "
            "Reconnect GitLab (Integrations or onboarding) to authorize again."
        )
        super().__init__(self.detail)


def _resolve_credentials(
    connection: CIConnection | None,
    *,
    base_url: str,
) -> tuple[str, str]:
    settings = get_settings()
    if connection is not None and connection.oauth_client_id:
        client_id = connection.oauth_client_id
        client_secret = decrypt_str(connection.oauth_client_secret_enc) if connection.oauth_client_secret_enc else ""
    elif base_url.rstrip("/") == settings.gitlab_base_url.rstrip("/"):
        client_id = settings.gitlab_oauth_client_id
        client_secret = settings.gitlab_oauth_client_secret
    else:
        client_id = ""
        client_secret = ""
    return client_id, client_secret


class GitLabOAuthService:
    """Implements the GitLab OAuth 2.0 authorization-code flow."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def build_authorize_url(
        self,
        tenant_id: uuid.UUID,
        *,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        connection_id: uuid.UUID | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        base_url = (base_url or self.settings.gitlab_base_url).rstrip("/")
        redirect_uri = redirect_uri or self.settings.gitlab_oauth_redirect_uri
        if not client_id:
            if base_url == self.settings.gitlab_base_url.rstrip("/"):
                client_id = self.settings.gitlab_oauth_client_id
        if not client_id:
            raise RuntimeError("GitLab OAuth client_id is required")

        state = secrets.token_urlsafe(24)
        redis_client = get_redis()
        state_payload = {
            "tenant_id": str(tenant_id),
            "base_url": base_url,
            "redirect_uri": redirect_uri,
            "connection_id": str(connection_id) if connection_id else None,
            "client_id": client_id,
            "client_secret": client_secret or "",
        }
        await redis_client.setex(
            f"oauth:gitlab:state:{state}",
            _STATE_TTL_SECONDS,
            json.dumps(state_payload),
        )
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": _DEFAULT_SCOPE,
        }
        return f"{base_url}/oauth/authorize?{urlencode(params)}"

    async def consume_state(self, state: str) -> dict | None:
        redis_client = get_redis()
        key = f"oauth:gitlab:state:{state}"
        payload = await redis_client.get(key)
        if payload is None:
            return None
        await redis_client.delete(key)
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def exchange_code(
        self,
        code: str,
        *,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict:
        base_url = (base_url or self.settings.gitlab_base_url).rstrip("/")
        redirect_uri = redirect_uri or self.settings.gitlab_oauth_redirect_uri
        client_id = client_id or self.settings.gitlab_oauth_client_id
        client_secret = client_secret or self.settings.gitlab_oauth_client_secret
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base_url}/oauth/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            resp.raise_for_status()
            return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def refresh(
        self,
        refresh_token: str,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
    ) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/oauth/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            return resp.json()

    def apply_tokens_to_connection(
        self, connection: CIConnection, token_payload: dict
    ) -> None:
        connection.oauth_access_token_enc = encrypt_str(token_payload.get("access_token"))
        connection.oauth_refresh_token_enc = encrypt_str(token_payload.get("refresh_token"))
        expires_in = int(token_payload.get("expires_in") or 0)
        if expires_in:
            connection.oauth_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                seconds=expires_in
            )
        connection.oauth_scope = token_payload.get("scope")

    async def ensure_fresh_access_token(self, connection: CIConnection) -> None:
        if not connection.oauth_access_token_enc:
            return
        if not connection.oauth_token_expires_at:
            return
        expires_at = connection.oauth_token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > datetime.now(tz=timezone.utc) + timedelta(seconds=30):
            return
        refresh = decrypt_str(connection.oauth_refresh_token_enc)
        if not refresh:
            log.warning("gitlab_oauth.no_refresh_token", connection_id=str(connection.id))
            return
        client_id, client_secret = _resolve_credentials(connection, base_url=connection.base_url)
        if not client_id:
            return
        try:
            payload = await self.refresh(
                refresh,
                base_url=connection.base_url,
                client_id=client_id,
                client_secret=client_secret,
            )
        except RetryError as err:
            root = err.__cause__
            log.warning(
                "gitlab_oauth.refresh_retry_exhausted",
                connection_id=str(connection.id),
                base_url=connection.base_url,
                error=str(root if root is not None else err),
            )
            raise GitLabOAuthRefreshFailed() from (root if isinstance(root, BaseException) else err)
        except httpx.HTTPStatusError as err:
            log.warning(
                "gitlab_oauth.refresh_http_error",
                connection_id=str(connection.id),
                status_code=err.response.status_code,
            )
            raise GitLabOAuthRefreshFailed() from err
        self.apply_tokens_to_connection(connection, payload)

    async def fetch_user_info(self, *, base_url: str, access_token: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/api/v4/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code >= 400:
                return {}
            return resp.json() or {}
