from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.crypto import decrypt_str, encrypt_str
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.ci_connection import CIConnection

log = get_logger(__name__)
_STATE_TTL_SECONDS = 600
_DEFAULT_SCOPE = "repo read:user"


def github_token_endpoint(conn: CIConnection) -> str:
    """https://github.com/login/oauth/access_token (or GHE)."""
    base = (conn.base_url or get_settings().github_base_url or "https://github.com").rstrip("/")
    u = base.lower()
    if u in ("https://github.com", "http://github.com", "https://github.com/"):
        return "https://github.com/login/oauth/access_token"
    if not u.startswith("http"):
        return f"https://{u}/login/oauth/access_token"
    return f"{base}/login/oauth/access_token"


def github_login_host(conn: CIConnection | None) -> str:
    s = get_settings()
    b = (conn.base_url if conn is not None else s.github_base_url) or "https://github.com"
    b = b.rstrip("/").lower()
    if b in ("https://github.com", "http://github.com"):
        return "https://github.com"
    p = urlparse(b if b.startswith("http") else f"https://{b}")
    if p.netloc:
        return f"{p.scheme or 'https'}://{p.netloc}"
    return f"https://{b}"


def _default_authorize_url(conn: CIConnection | None) -> str:
    h = github_login_host(conn)
    return f"{h.rstrip('/')}/login/oauth/authorize"


class GitHubOAuthService:
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
        base = (base_url or settings.github_base_url).rstrip("/")
        redirect_uri = (redirect_uri or settings.github_oauth_redirect_uri).strip()
        client_id = client_id or settings.github_oauth_client_id
        if not client_id:
            raise RuntimeError("GitHub OAuth client_id is required")

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
            f"oauth:github:state:{state}",
            _STATE_TTL_SECONDS,
            json.dumps(state_payload),
        )
        # authorize URL host must match GitHub.com or GHE
        conn = CIConnection(base_url=base) if base else None
        auth_url = _default_authorize_url(conn)
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": _DEFAULT_SCOPE,
        }
        return f"{auth_url.split('?')[0]}?{urlencode(params)}"

    async def consume_state(self, state: str) -> dict | None:
        redis_client = get_redis()
        key = f"oauth:github:state:{state}"
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
        conn = CIConnection(base_url=base_url)
        token_url = github_token_endpoint(conn)
        if not client_id:
            client_id = settings.github_oauth_client_id
        if not client_secret:
            client_secret = settings.github_oauth_client_secret
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(token_url, data=data, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def apply_tokens_to_connection(
        self, connection: CIConnection, token_payload: dict
    ) -> None:
        at = token_payload.get("access_token")
        if at:
            connection.oauth_access_token_enc = encrypt_str(at)
        # GitHub classic OAuth may omit refresh_token; optional expiry
        rt = token_payload.get("refresh_token")
        if rt:
            connection.oauth_refresh_token_enc = encrypt_str(str(rt))
        if token_payload.get("expires_in"):
            expires_in = int(token_payload["expires_in"])
            connection.oauth_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
                seconds=expires_in
            )
        connection.oauth_scope = token_payload.get("scope", _DEFAULT_SCOPE)

    async def fetch_user_info(self, *, connection: CIConnection) -> dict:
        s = get_settings()
        u = (connection.base_url or s.github_base_url or "https://github.com").rstrip(
            "/"
        ).lower()
        if u in ("https://github.com", "http://github.com"):
            base = s.github_api_base_url.rstrip("/")
        else:
            base = f"{(connection.base_url or s.github_base_url).rstrip('/')}/api/v3"
        if not connection.oauth_access_token_enc:
            return {}
        at = decrypt_str(connection.oauth_access_token_enc)
        async with httpx.AsyncClient(
            base_url=base, timeout=15, headers={"Authorization": f"Bearer {at}"}
        ) as client:
            r = await client.get("/user")
            if r.status_code >= 400:
                return {}
            return r.json() or {}
