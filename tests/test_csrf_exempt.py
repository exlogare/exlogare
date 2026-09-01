from __future__ import annotations

import pytest

from app.core.csrf import EXEMPT_PREFIXES, _is_exempt


@pytest.mark.parametrize(
    "path",
    [
        "/webhook/gitlab",
        "/webhooks/github",
        "/api/ingest/log",
        "/api/auth/oidc/callback",
        "/api/integrations/telegram/webhook",
        "/api/public/status",
    ],
)
def test_csrf_exempt_paths(path: str) -> None:
    assert _is_exempt(path), f"{path} must be CSRF-exempt"


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/request",
        "/api/auth/verify",
        "/api/auth/me",
        "/api/tokens",
        "/api/llm/ping",
    ],
)
def test_magic_link_and_state_changing_paths_not_exempt(path: str) -> None:
    assert not _is_exempt(path), f"{path} must NOT be CSRF-exempt"


def test_magic_link_prefixes_removed_from_exemptions() -> None:
    assert "/api/auth/request" not in EXEMPT_PREFIXES
    assert "/api/auth/verify" not in EXEMPT_PREFIXES
    assert "/api/auth/oidc/callback" in EXEMPT_PREFIXES
