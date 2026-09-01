"""Group-aware GitLab OAuth token refresh."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.models.ci_connection import CIConnection, CIProvider, ConnectionStatus
from app.services.oauth.gitlab import (
    GitLabOAuthRefreshFailed,
    GitLabOAuthService,
    _resolve_credentials,
)

log = get_logger(__name__)

_REFRESH_LEEWAY = timedelta(seconds=30)


def group_key(conn: CIConnection) -> tuple[uuid.UUID, str]:
    """Identity of an OAuth credential group: tenant + GitLab instance."""
    return conn.tenant_id, (conn.base_url or "").rstrip("/")


def propagate_tokens(group: Iterable[CIConnection], source: CIConnection) -> None:
    """Copy OAuth credential state from ``source`` to every other connection"""
    for c in group:
        if c is source:
            continue
        c.oauth_access_token_enc = source.oauth_access_token_enc
        c.oauth_refresh_token_enc = source.oauth_refresh_token_enc
        c.oauth_token_expires_at = source.oauth_token_expires_at
        c.oauth_client_id = source.oauth_client_id
        c.oauth_client_secret_enc = source.oauth_client_secret_enc


def _expires_at(c: CIConnection) -> datetime:
    if c.oauth_token_expires_at is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    exp = c.oauth_token_expires_at
    return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)


async def refresh_group_tokens(
    oauth: GitLabOAuthService,
    group: list[CIConnection],
    *,
    mark_error_on_exhaust: bool = False,
) -> bool:
    """Ensure every connection in a credential group has a fresh access token."""
    if not group:
        return False

    now = datetime.now(tz=timezone.utc)

    fresh = [
        c
        for c in group
        if c.oauth_access_token_enc and _expires_at(c) > now + _REFRESH_LEEWAY
    ]
    if fresh:
        source = max(fresh, key=_expires_at)
        propagate_tokens(group, source)
        return True

    if not any(c.oauth_refresh_token_enc for c in group) and not any(
        c.oauth_access_token_enc for c in group
    ):
        return False

    tried_refresh_tokens: set[str] = set()
    ordered = sorted(
        [c for c in group if c.oauth_refresh_token_enc],
        key=_expires_at,
        reverse=True,
    )
    for candidate in ordered:
        rt_enc = candidate.oauth_refresh_token_enc
        if not rt_enc or rt_enc in tried_refresh_tokens:
            continue
        tried_refresh_tokens.add(rt_enc)
        try:
            raw_rt = decrypt_str(rt_enc)
        except Exception:
            continue
        if not raw_rt:
            continue
        client_id, client_secret = _resolve_credentials(
            candidate, base_url=candidate.base_url
        )
        if not client_id:
            continue
        try:
            payload = await oauth.refresh(
                raw_rt,
                base_url=candidate.base_url,
                client_id=client_id,
                client_secret=client_secret,
            )
        except Exception as exc:
            log.info(
                "gitlab_oauth.group_refresh_attempt_failed",
                connection_id=str(candidate.id),
                error=str(exc),
            )
            continue
        oauth.apply_tokens_to_connection(candidate, payload)
        propagate_tokens(group, candidate)
        log.info(
            "gitlab_oauth.group_refresh_propagated",
            tenant_id=str(candidate.tenant_id),
            base_url=candidate.base_url,
            group_size=len(group),
        )
        return True

    if mark_error_on_exhaust:
        for c in group:
            if c.status != ConnectionStatus.ERROR:
                c.status = ConnectionStatus.ERROR
    log.warning(
        "gitlab_oauth.group_refresh_exhausted",
        tenant_id=str(group[0].tenant_id),
        base_url=group[0].base_url,
        group_size=len(group),
    )
    return False


async def load_group(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    base_url: str,
) -> list[CIConnection]:
    """All GitLab connections for one (tenant, base_url) credential group."""
    normalized = (base_url or "").rstrip("/")
    stmt = select(CIConnection).where(
        CIConnection.tenant_id == tenant_id,
        CIConnection.provider == CIProvider.GITLAB,
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [c for c in rows if (c.base_url or "").rstrip("/") == normalized]


async def ensure_group_fresh_for_connection(
    session: AsyncSession,
    conn: CIConnection,
    *,
    oauth: GitLabOAuthService | None = None,
    raise_on_exhaust: bool = True,
) -> bool:
    """Refresh (or propagate) OAuth tokens for the group ``conn`` belongs to."""
    oauth = oauth or GitLabOAuthService()
    group = await load_group(
        session, tenant_id=conn.tenant_id, base_url=conn.base_url
    )
    if conn not in group:
        group.append(conn)

    has_any_refresh = any(c.oauth_refresh_token_enc for c in group)
    has_any_access = any(c.oauth_access_token_enc for c in group)
    ok = await refresh_group_tokens(oauth, group, mark_error_on_exhaust=False)
    if ok:
        return True
    if not has_any_refresh and not has_any_access:
        # Nothing to refresh at all — caller decides whether that's fatal.
        return False
    if raise_on_exhaust:
        raise GitLabOAuthRefreshFailed()
    return False
