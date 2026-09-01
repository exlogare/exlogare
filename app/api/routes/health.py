from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.redis import get_redis

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health")
async def health() -> dict:
    """Shallow liveness probe."""
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
    }


@router.get("/healthz")
async def healthz(
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Readiness probe - returns 503 if any dependency is unhealthy."""
    settings = get_settings()
    body: dict[str, object] = {
        "status": "ok",
        "env": settings.app_env,
        "llm": {
            "enabled": settings.llm_enabled,
            "base_url": settings.llm_base_url or "https://api.openai.com/v1",
            "model": settings.llm_model,
            "configured": bool(settings.llm_enabled and settings.llm_api_key),
        },
        "checks": {},
    }

    checks: dict[str, str] = body["checks"]  # type: ignore[assignment]

    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 - we convert to a 503
        log.warning("healthz.db_fail", error=str(exc))
        checks["db"] = f"fail: {exc.__class__.__name__}"
        body["status"] = "degraded"

    try:
        redis_client = get_redis()
        pong = await redis_client.ping()
        checks["redis"] = "ok" if pong else "fail: no pong"
        if not pong:
            body["status"] = "degraded"
    except Exception as exc:  # noqa: BLE001
        log.warning("healthz.redis_fail", error=str(exc))
        checks["redis"] = f"fail: {exc.__class__.__name__}"
        body["status"] = "degraded"

    if body["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
