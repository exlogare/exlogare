"""LLM status and connectivity probe for Community Edition."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.deps import CurrentPrincipal, require_admin
from app.core.logging import get_logger

router = APIRouter(prefix="/api/llm", tags=["llm"])
log = get_logger(__name__)


class LlmStatusResponse(BaseModel):
    llm_enabled: bool
    llm_configured: bool
    llm_base_url: str
    llm_model: str


class LlmPingResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None
    model: str | None = None


def _status_payload() -> LlmStatusResponse:
    settings = get_settings()
    base = settings.llm_base_url.strip() or "https://api.openai.com/v1"
    return LlmStatusResponse(
        llm_enabled=settings.llm_enabled,
        llm_configured=bool(settings.llm_enabled and settings.llm_api_key.strip()),
        llm_base_url=base,
        llm_model=settings.llm_model,
    )


@router.get("/status", response_model=LlmStatusResponse)
async def llm_status(
    _principal: CurrentPrincipal = Depends(require_admin),
) -> LlmStatusResponse:
    return _status_payload()


@router.post("/ping", response_model=LlmPingResponse)
async def llm_ping(
    _principal: CurrentPrincipal = Depends(require_admin),
) -> LlmPingResponse:
    settings = get_settings()
    if not settings.llm_enabled:
        raise HTTPException(status_code=400, detail="LLM is disabled (LLM_ENABLED=false)")
    if not settings.llm_api_key.strip():
        raise HTTPException(
            status_code=400,
            detail="LLM_API_KEY is empty — set any non-empty value for local models",
        )

    from openai import AsyncOpenAI

    kwargs: dict = {"api_key": settings.llm_api_key}
    if settings.llm_base_url.strip():
        kwargs["base_url"] = settings.llm_base_url.strip()
    client = AsyncOpenAI(**kwargs)

    started = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            max_tokens=8,
            temperature=0,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            return LlmPingResponse(
                ok=False,
                latency_ms=latency_ms,
                error="Empty completion from model",
                model=settings.llm_model,
            )
        return LlmPingResponse(ok=True, latency_ms=latency_ms, model=settings.llm_model)
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - started) * 1000)
        log.warning("llm.ping_failed", error=str(exc))
        return LlmPingResponse(
            ok=False,
            latency_ms=latency_ms,
            error=str(exc)[:500],
            model=settings.llm_model,
        )
