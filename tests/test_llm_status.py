from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import get_settings
from app.models.membership import MembershipRole


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_admin():
    from app.core.deps import CurrentPrincipal

    user = SimpleNamespace(id="u1", email="admin@example.com", display_name="Admin")
    tenant = SimpleNamespace(id="t1", name="T", slug="t")
    return CurrentPrincipal(user=user, tenant=tenant, role=MembershipRole.OWNER)


@pytest.mark.asyncio
async def test_llm_status_hides_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-should-not-leak")
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_SYSTEM_PROMPT", "You are a test analyzer.")
    get_settings.cache_clear()

    from app.core.deps import require_admin
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_admin] = _fake_admin

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/llm/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_enabled"] is True
    assert body["llm_configured"] is True
    assert body["llm_model"] == "llama3.1"
    assert body["llm_base_url"] == "http://ollama:11434/v1"
    assert "sk-super-secret" not in resp.text
    assert "api_key" not in body
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_llm_ping_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_SYSTEM_PROMPT", "You are a test analyzer.")
    get_settings.cache_clear()

    from app.core.deps import require_admin
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_admin] = _fake_admin

    mock_choice = MagicMock()
    mock_choice.message.content = "pong"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await client.get("/api/auth/csrf")
            token = csrf.json()["csrf"]
            resp = await client.post(
                "/api/llm/ping",
                headers={"X-CSRF-Token": token},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "llama3.1"
    assert body["error"] is None
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_llm_ping_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1")
    monkeypatch.setenv("LLM_SYSTEM_PROMPT", "You are a test analyzer.")
    get_settings.cache_clear()

    from app.core.deps import require_admin
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_admin] = _fake_admin

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await client.get("/api/auth/csrf")
            token = csrf.json()["csrf"]
            resp = await client.post(
                "/api/llm/ping",
                headers={"X-CSRF-Token": token},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "connection refused" in (body["error"] or "")
    app.dependency_overrides.clear()


def test_password_login_rejects_empty_hash():
    from app.services.auth.password import verify_password

    assert verify_password("anything", "") is False
    assert verify_password("anything", None) is False
