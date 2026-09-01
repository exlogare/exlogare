from __future__ import annotations

from app.core.config import get_settings


def test_oauth_redirects_derive_from_public_base_url(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ci.example.com")
    monkeypatch.setenv("GITLAB_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("BITBUCKET_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("GITFLIC_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "")
    get_settings.cache_clear()
    s = get_settings()
    assert s.gitlab_oauth_redirect_uri == "https://ci.example.com/auth/gitlab/callback"
    assert (
        s.github_oauth_redirect_uri
        == "https://ci.example.com/api/integrations/github/oauth/callback"
    )
    assert (
        s.bitbucket_oauth_redirect_uri
        == "https://ci.example.com/api/integrations/bitbucket/oauth/callback"
    )
    assert (
        s.gitflic_oauth_redirect_uri
        == "https://ci.example.com/api/integrations/gitflic/oauth/callback"
    )
    assert s.oidc_redirect_uri == "https://ci.example.com/api/auth/oidc/callback"


def test_oauth_redirect_explicit_override(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ci.example.com")
    monkeypatch.setenv("GITLAB_OAUTH_REDIRECT_URI", "https://alt.example/cb")
    get_settings.cache_clear()
    s = get_settings()
    assert s.gitlab_oauth_redirect_uri == "https://alt.example/cb"
