"""Bitbucket Cloud OAuth 2.0 service."""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.ci_connection import CIConnection

log = get_logger(__name__)
_STATE_TTL_SECONDS = 600
_DEFAULT_SCOPE = "repository pullrequest:write webhook pipeline account"

_CLOUD_HOSTS = (
    "https://bitbucket.org",
    "http://bitbucket.org",
    "https://api.bitbucket.org",
    "http://api.bitbucket.org",
)


def is_bitbucket_cloud(base_url: str | None) -> bool:
    """Return True for Cloud (bitbucket.org), False for any DC/self-hosted host."""
    if not base_url:
        # Default config is Cloud, so empty-string falls through to Cloud.
        return True
    u = base_url.rstrip("/").lower()
    return u in _CLOUD_HOSTS


def _authorize_url() -> str:
    return "https://bitbucket.org/site/oauth2/authorize"


def _token_url() -> str:
    return "https://bitbucket.org/site/oauth2/access_token"


class BitbucketOAuthService:
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
        settings = get_settings()
        base = (base_url or settings.bitbucket_base_url).rstrip("/")
        redirect_uri = (redirect_uri or settings.bitbucket_oauth_redirect_uri).strip()
        client_id = client_id or settings.bitbucket_oauth_client_id
        if not client_id:
            raise RuntimeError("Bitbucket OAuth client_id is required")

        state = secrets.token_urlsafe(24)
        redis_client = get_redis()
        state_payload = {
            "tenant_id": str(tenant_id),
            "base_url": base,
            "redirect_uri": redirect_uri,
            "connection_id": str(connection_id) if connection_id else None,
            "client_id": client_id,
            "client_secret": client_secret or "",
        }
        await redis_client.setex(
            f"oauth:bitbucket:state:{state}",
            _STATE_TTL_SECONDS,
            json.dumps(state_payload),
        )
        params = {
            "client_id": client_id,
            "response_type": "code",
            "state": state,
            "scope": _DEFAULT_SCOPE,
        }
        if redirect_uri:
            params["redirect_uri"] = redirect_uri
        return f"{_authorize_url()}?{urlencode(params)}"

    async def consume_state(self, state: str) -> dict | None:
        redis_client = get_redis()
        key = f"oauth:bitbucket:state:{state}"
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
        base_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict:
        settings = get_settings()
        if not client_id:
            client_id = settings.bitbucket_oauth_client_id
        if not client_secret:
            client_secret = settings.bitbucket_oauth_client_secret
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        }
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(_token_url(), data=data, headers=headers)
            resp.raise_for_status()
            return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def refresh_token(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> dict:
        settings = get_settings()
        if not client_id:
            client_id = settings.bitbucket_oauth_client_id
        if not client_secret:
            client_secret = settings.bitbucket_oauth_client_secret
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(_token_url(), data=data, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def apply_tokens_to_connection(
        self, connection: CIConnection, token_payload: dict
    ) -> None:
        at = token_payload.get("access_token")
        if at:
            connection.oauth_access_token_enc = encrypt_str(at)
        rt = token_payload.get("refresh_token")
        if rt:
            connection.oauth_refresh_token_enc = encrypt_str(str(rt))
        if token_payload.get("expires_in"):
            expires_in = int(token_payload["expires_in"])
            connection.oauth_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                seconds=expires_in
            )
        connection.oauth_scope = token_payload.get("scopes") or token_payload.get(
            "scope", _DEFAULT_SCOPE
        )

    async def fetch_user_info(self, *, connection: CIConnection) -> dict:
        if not connection.oauth_access_token_enc:
            return {}
        s = get_settings()
        api_base = s.bitbucket_api_base_url.rstrip("/")
        at = decrypt_str(connection.oauth_access_token_enc)
        async with httpx.AsyncClient(
            base_url=api_base, timeout=15, headers={"Authorization": f"Bearer {at}"}
        ) as client:
            r = await client.get("/user")
            if r.status_code >= 400:
                return {}
            return r.json() or {}
