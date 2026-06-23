"""Double-submit CSRF middleware."""
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.auth_cookie import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

EXEMPT_PREFIXES: tuple[str, ...] = (
    "/webhook/",
    "/webhooks/",
    "/auth/gitlab/callback",
    "/api/auth/request",
    "/api/auth/magic-link",
    "/api/auth/verify",
    "/api/ingest/",
    "/api/integrations/telegram/webhook",
    "/api/integrations/gitflic/oauth/callback",
    "/api/public/",
)


def _is_exempt(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)
        if _is_exempt(request.url.path):
            return await call_next(request)

        cookie_val = request.cookies.get(CSRF_COOKIE_NAME)
        header_val = request.headers.get(CSRF_HEADER_NAME)
        if not cookie_val or not header_val:
            return JSONResponse(
                status_code=403, content={"detail": "CSRF token missing"}
            )
        if not hmac.compare_digest(cookie_val, header_val):
            return JSONResponse(
                status_code=403, content={"detail": "CSRF token mismatch"}
            )
        return await call_next(request)


__all__ = ["CSRFMiddleware"]
