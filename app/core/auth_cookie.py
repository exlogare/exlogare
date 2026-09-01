"""Helpers for the session + CSRF cookie pair."""
from __future__ import annotations

import secrets

from fastapi import Response

from app.core.config import get_settings

SESSION_COOKIE_NAME = "exlogare_session"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def _is_secure_cookie() -> bool:
    return get_settings().app_env in ("prod", "staging")


def generate_csrf_token() -> str:
    """Return a fresh URL-safe random CSRF token."""
    return secrets.token_urlsafe(32)


def set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path="/",
    )


def set_csrf_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        httponly=False,
        secure=_is_secure_cookie(),
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(key=name, path="/")
