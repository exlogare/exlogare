"""OpenID Connect (authorization code + PKCE) for dashboard login."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)

_STATE_TTL_SECONDS = 600
_DISCOVERY_CACHE_TTL = 300
_STATE_PREFIX = "oidc:state:"


@dataclass(frozen=True)
class OidcClaims:
    sub: str
    email: str
    email_verified: bool
    name: str | None


class OidcError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def oidc_is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.oidc_enabled
        and settings.oidc_issuer.strip()
        and settings.oidc_client_id.strip()
        and settings.oidc_client_secret.strip()
    )


def oidc_redirect_uri() -> str:
    settings = get_settings()
    if settings.oidc_redirect_uri.strip():
        return settings.oidc_redirect_uri.strip()
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/auth/oidc/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        hashlib.sha256(verifier.encode("ascii"))
        .digest()
    )
    import base64

    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    return verifier, challenge_b64


class OidcService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._discovery: dict[str, Any] | None = None
        self._discovery_fetched_at: float = 0.0

    async def get_discovery(self) -> dict[str, Any]:
        now = time.time()
        if self._discovery and (now - self._discovery_fetched_at) < _DISCOVERY_CACHE_TTL:
            return self._discovery
        issuer = self.settings.oidc_issuer.rstrip("/")
        url = f"{issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        self._discovery = data
        self._discovery_fetched_at = now
        return data

    async def build_authorize_url(self) -> str:
        if not oidc_is_configured():
            raise OidcError("OIDC is not configured")
        discovery = await self.get_discovery()
        authorize = discovery.get("authorization_endpoint")
        if not authorize:
            raise OidcError("OIDC discovery missing authorization_endpoint")

        state = secrets.token_urlsafe(24)
        verifier, challenge = _pkce_pair()
        redis_client = get_redis()
        await redis_client.setex(
            f"{_STATE_PREFIX}{state}",
            _STATE_TTL_SECONDS,
            json.dumps({"code_verifier": verifier}),
        )

        params = {
            "client_id": self.settings.oidc_client_id,
            "response_type": "code",
            "scope": self.settings.oidc_scopes.strip() or "openid email profile",
            "redirect_uri": oidc_redirect_uri(),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{authorize}?{urlencode(params)}"

    async def consume_state(self, state: str) -> dict[str, Any] | None:
        redis_client = get_redis()
        key = f"{_STATE_PREFIX}{state}"
        raw = await redis_client.get(key)
        if raw is None:
            return None
        await redis_client.delete(key)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def exchange_code(self, code: str, *, code_verifier: str) -> dict[str, Any]:
        discovery = await self.get_discovery()
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise OidcError("OIDC discovery missing token_endpoint")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oidc_redirect_uri(),
            "client_id": self.settings.oidc_client_id,
            "client_secret": self.settings.oidc_client_secret,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(token_endpoint, data=data)
            if resp.status_code >= 400:
                log.warning(
                    "oidc.token_exchange_failed",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                raise OidcError("OIDC token exchange failed")
            return resp.json()

    async def validate_id_token(self, id_token: str) -> OidcClaims:
        discovery = await self.get_discovery()
        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise OidcError("OIDC discovery missing jwks_uri")

        async with httpx.AsyncClient(timeout=15.0) as client:
            jwks_resp = await client.get(jwks_uri)
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()

        key_set = KeySet.import_key_set(jwks)
        claims_requests = JWTClaimsRegistry(
            iss={"essential": True, "value": self.settings.oidc_issuer.rstrip("/")},
            aud={"essential": True, "value": self.settings.oidc_client_id},
            exp={"essential": True},
        )
        token = jwt.decode(id_token, key_set)
        claims_requests.validate(token.claims)
        claims = token.claims

        sub = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or "").strip().lower()
        if not sub:
            raise OidcError("OIDC id_token missing sub")
        if not email:
            raise OidcError("OIDC id_token missing email claim — enable email scope in the IdP")

        verified = bool(claims.get("email_verified", False))
        name = claims.get("name") or claims.get("preferred_username")
        return OidcClaims(
            sub=sub,
            email=email,
            email_verified=verified,
            name=str(name) if name else None,
        )
