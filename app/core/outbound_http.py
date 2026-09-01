"""Shared httpx.AsyncClient factories for RKN-blocked destinations."""

from __future__ import annotations

import httpx

from app.core.config import get_settings


def _resolve_proxy(override: str, fallback: str) -> str | None:
    """Return the effective proxy URL for a channel, or ``None`` for direct."""
    if override == "none":
        return None
    return override or fallback or None


def telegram_proxy_url() -> str | None:
    s = get_settings()
    return _resolve_proxy(s.telegram_proxy_url, s.outbound_http_proxy_url)


def slack_proxy_url() -> str | None:
    s = get_settings()
    return _resolve_proxy(s.slack_proxy_url, s.outbound_http_proxy_url)


def telegram_client(timeout: float | None = None) -> httpx.AsyncClient:
    """AsyncClient configured to route Telegram Bot API calls through the proxy."""
    s = get_settings()
    return httpx.AsyncClient(
        proxy=telegram_proxy_url(),
        timeout=timeout if timeout is not None else s.outbound_http_proxy_timeout,
    )


def slack_client(timeout: float | None = None) -> httpx.AsyncClient:
    """AsyncClient configured to route Slack API calls through the proxy."""
    s = get_settings()
    return httpx.AsyncClient(
        proxy=slack_proxy_url(),
        timeout=timeout if timeout is not None else s.outbound_http_proxy_timeout,
    )
