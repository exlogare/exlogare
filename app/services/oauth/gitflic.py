"""GitFlic OAuth 2.0 authorization-code flow."""

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

_DEFAULT_SCOPE = "PROJECT_READ,PROJECT_WRITE,PROJECT_EDIT,USER_READ"
_STATE_TTL_SECONDS = 600


class GitFlicOAuthRefreshFailed(Exception):
    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or (
            "GitFlic refused to refresh the OAuth token. "
            "Reconnect GitFlic from Integrations to authorize again."
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
        client_secret = (
            decrypt_str(connection.oauth_client_secret_enc)
            if connection.oauth_client_secret_enc
            else ""
        )
    elif base_url.rstrip("/") == settings.gitflic_base_url.rstrip("/"):
        client_id = settings.gitflic_oauth_client_id
        client_secret = settings.gitflic_oauth_client_secret
    else:
        client_id = ""
        client_secret = ""
    return client_id, client_secret


def _oauth_base(connection: CIConnection | None = None) -> str:
    """OAuth host for a connection."""
    settings = get_settings()
    if connection is not None:
        override = (connection.extra or {}).get("oauth_base_url")
        if override:
            return str(override).rstrip("/")
        conn_base = (connection.base_url or "").rstrip("/")
        if conn_base and conn_base != settings.gitflic_base_url.rstrip("/"):
            return conn_base
    return settings.gitflic_oauth_base_url.rstrip("/")


class GitFlicOAuthService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def build_authorize_url(
        self,
        tenant_id: uuid.UUID,
        *,
        base_url: str | None = None,
        oauth_base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        connection_id: uuid.UUID | None = None,
        redirect_uri: str | None = None,
    ) -> str:
        base_url = (base_url or self.settings.gitflic_base_url).rstrip("/")
        # Resolve OAuth host:
        if oauth_base_url:
            oauth_base = oauth_base_url.rstrip("/")
        elif base_url == self.settings.gitflic_base_url.rstrip("/"):
            oauth_base = self.settings.gitflic_oauth_base_url.rstrip("/")
        else:
            oauth_base = base_url
        redirect_uri = redirect_uri or self.settings.gitflic_oauth_redirect_uri
        if not client_id:
            if base_url == self.settings.gitflic_base_url.rstrip("/"):
                client_id = self.settings.gitflic_oauth_client_id
        if not client_id:
            raise RuntimeError("GitFlic OAuth client_id is required")

        state = secrets.token_urlsafe(24)
        redis_client = get_redis()
        state_payload = {
            "tenant_id": str(tenant_id),
            "base_url": base_url,
            "oauth_base_url": oauth_base,
            "redirect_uri": redirect_uri,
            "connection_id": str(connection_id) if connection_id else None,
            "client_id": client_id,
            "client_secret": client_secret or "",
        }
        await redis_client.setex(
            f"oauth:gitflic:state:{state}",
            _STATE_TTL_SECONDS,
            json.dumps(state_payload),
        )
        params = {
            "client_id": client_id,
            "redirect_url": redirect_uri,
            "scope": _DEFAULT_SCOPE,
            "state": state,
        }
        return f"{oauth_base}/oauth/authorize?{urlencode(params)}"

    async def consume_state(self, state: str) -> dict | None:
        redis_client = get_redis()
        key = f"oauth:gitflic:state:{state}"
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
        oauth_base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> dict:
        oauth_base = (oauth_base_url or self.settings.gitflic_oauth_base_url).rstrip("/")
        client_id = client_id or self.settings.gitflic_oauth_client_id
        client_secret = client_secret or self.settings.gitflic_oauth_client_secret
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{oauth_base}/api/token/access",
                params={"code": code},
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def refresh(
        self,
        refresh_token: str,
        *,
        oauth_base_url: str,
        client_id: str,
        client_secret: str,
    ) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{oauth_base_url.rstrip('/')}/api/token/refresh",
                json={"refreshToken": refresh_token},
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            return resp.json()

    def apply_tokens_to_connection(
        self, connection: CIConnection, token_payload: dict
    ) -> None:
        access = token_payload.get("accessToken") or token_payload.get("access_token")
        refresh = token_payload.get("refreshToken") or token_payload.get("refresh_token")
        connection.oauth_access_token_enc = encrypt_str(access)
        if refresh:
            connection.oauth_refresh_token_enc = encrypt_str(refresh)
        expires_at = self._parse_expires(token_payload)
        if expires_at is not None:
            connection.oauth_token_expires_at = expires_at
        scope = token_payload.get("scope") or token_payload.get("scopes")
        if isinstance(scope, list):
            scope = ",".join(scope)
        if scope:
            connection.oauth_scope = scope

    _NAIVE_TZ = timezone(timedelta(hours=3), name="MSK")

    @classmethod
    def _parse_expires(cls, payload: dict) -> datetime | None:
        raw = payload.get("expires") or payload.get("expires_in") or payload.get("expiresAt")
        if raw is None:
            return None
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
            return datetime.now(tz=timezone.utc) + timedelta(seconds=int(raw))
        try:
            ts = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=cls._NAIVE_TZ)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    async def ensure_fresh_access_token(self, connection: CIConnection) -> None:
        if not connection.oauth_access_token_enc:
            return
        if not connection.oauth_token_expires_at:
            return
        expires_at = connection.oauth_token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > datetime.now(tz=timezone.utc) + timedelta(seconds=60):
            return
        refresh = decrypt_str(connection.oauth_refresh_token_enc)
        if not refresh:
            log.warning("gitflic_oauth.no_refresh_token", connection_id=str(connection.id))
            return
        client_id, client_secret = _resolve_credentials(
            connection, base_url=connection.base_url
        )
        if not client_id:
            return
        try:
            payload = await self.refresh(
                refresh,
                oauth_base_url=_oauth_base(connection),
                client_id=client_id,
                client_secret=client_secret,
            )
        except RetryError as err:
            root = err.__cause__
            log.warning(
                "gitflic_oauth.refresh_retry_exhausted",
                connection_id=str(connection.id),
                error=str(root if root is not None else err),
            )
            raise GitFlicOAuthRefreshFailed() from (
                root if isinstance(root, BaseException) else err
            )
        except httpx.HTTPStatusError as err:
            log.warning(
                "gitflic_oauth.refresh_http_error",
                connection_id=str(connection.id),
                status_code=err.response.status_code,
            )
            raise GitFlicOAuthRefreshFailed() from err
        self.apply_tokens_to_connection(connection, payload)

    async def fetch_user_info(
        self, *, api_base_url: str, access_token: str
    ) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{api_base_url.rstrip('/')}/user",
                headers={"Authorization": f"token {access_token}"},
            )
            if resp.status_code >= 400:
                return {}
            return resp.json() or {}
