from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from app.core.config import get_settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(secret: str, signing_input: bytes) -> bytes:
    return hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()


def issue_access_token(
    *,
    user_id: uuid.UUID | str,
    email: str,
    tenant_id: uuid.UUID | str | None = None,
    expires_minutes: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Issue a compact HS256 JWT without requiring a third-party dependency."""

    settings = get_settings()
    now = int(time.time())
    ttl = (expires_minutes if expires_minutes is not None else settings.jwt_expires_minutes) * 60
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + ttl,
    }
    if tenant_id is not None:
        payload["tid"] = str(tenant_id)
    if extra:
        payload.update(extra)
    header_b = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b}.{payload_b}".encode()
    sig = _b64url(_sign(settings.jwt_secret, signing_input))
    return f"{header_b}.{payload_b}.{sig}"


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        header_b, payload_b, sig_b = token.split(".")
    except ValueError as exc:
        raise ValueError("Malformed token") from exc
    signing_input = f"{header_b}.{payload_b}".encode()
    expected = _sign(settings.jwt_secret, signing_input)
    if not hmac.compare_digest(_b64url_decode(sig_b), expected):
        raise ValueError("Invalid signature")
    payload = json.loads(_b64url_decode(payload_b))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token expired")
    return payload
