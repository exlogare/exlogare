from __future__ import annotations

from app.core.config import get_settings
from app.services.auth.oidc import oidc_is_configured, oidc_redirect_uri


def test_oidc_not_configured_without_secrets(monkeypatch):
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example/realms/x")
    monkeypatch.setenv("OIDC_CLIENT_ID", "")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "")
    get_settings.cache_clear()
    assert oidc_is_configured() is False


def test_oidc_redirect_uri_default(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ce.example.com")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "")
    get_settings.cache_clear()
    assert oidc_redirect_uri() == "https://ce.example.com/api/auth/oidc/callback"
