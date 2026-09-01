from __future__ import annotations

import os

import httpx
import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_oidc_status_disabled_by_default():
    os.environ["OIDC_ENABLED"] = "false"
    get_settings.cache_clear()
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/oidc/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert "display_name" in body


@pytest.mark.asyncio
async def test_oidc_status_enabled_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example/realms/exlogare")
    monkeypatch.setenv("OIDC_CLIENT_ID", "exlogare")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OIDC_DISPLAY_NAME", "Keycloak")
    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/oidc/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["display_name"] == "Keycloak"


@pytest.mark.asyncio
async def test_oidc_login_404_when_disabled():
    os.environ["OIDC_ENABLED"] = "false"
    get_settings.cache_clear()
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 404
