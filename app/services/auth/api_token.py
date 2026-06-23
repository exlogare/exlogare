"""API token generation, hashing, and verification."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_token import ApiToken

TOKEN_PREFIX = "exl_"
PREFIX_DISPLAY_LEN = 12

ALLOWED_SCOPES: frozenset[str] = frozenset({"ingest", "read"})


@dataclass
class GeneratedToken:
    raw: str
    token_hash: str
    token_prefix: str


def generate_token() -> GeneratedToken:
    """Generate a fresh raw token plus its storable hash and display prefix."""
    raw = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return GeneratedToken(
        raw=raw,
        token_hash=hash_token(raw),
        token_prefix=raw[:PREFIX_DISPLAY_LEN],
    )


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_api_token(raw: str) -> bool:
    return raw.startswith(TOKEN_PREFIX)


def normalize_scopes(scopes: Iterable[str]) -> list[str]:
    """Lowercase, dedupe, and validate scopes. Raises ``ValueError`` on unknown scope."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in scopes:
        s = str(raw).strip().lower()
        if not s:
            continue
        if s not in ALLOWED_SCOPES:
            raise ValueError(f"Unknown scope: {raw!r}")
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


async def verify_token(session: AsyncSession, raw: str) -> ApiToken | None:
    """Return the ``ApiToken`` for ``raw`` if it is active, else ``None``."""
    if not looks_like_api_token(raw):
        return None
    row = await session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(raw))
    )
    token = row.scalar_one_or_none()
    if token is None:
        return None
    if token.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    if token.expires_at is not None and token.expires_at <= now:
        return None

    await session.execute(
        update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now)
    )
    token.last_used_at = now
    return token
