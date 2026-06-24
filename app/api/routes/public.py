"""Public (no-auth) API endpoints."""

from __future__ import annotations

import html
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_app_version, get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.core.redis import get_redis
from app.services.email import get_email_sender
from app.services.email.layout import email_notice, wrap_email_html

router = APIRouter(prefix="/api/public", tags=["public"])
log = get_logger(__name__)


class ComponentStatus(BaseModel):
    key: str
    status: str  # "ok" | "degraded" | "down"
    updated_at: datetime
    detail: str | None = None


class StatusResponse(BaseModel):
    overall: str
    components: list[ComponentStatus]
    generated_at: datetime


_WORKER_HEARTBEAT_KEY = "heartbeat:worker"
_WORKER_HEARTBEAT_TTL_SECS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VersionResponse(BaseModel):
    version: str
    edition: str = "community"
    update_check_enabled: bool = True


@router.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    settings = get_settings()
    return VersionResponse(
        version=get_app_version(),
        update_check_enabled=settings.update_check_enabled,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(session: AsyncSession = Depends(get_db)) -> StatusResponse:
    settings = get_settings()
    now = _now()
    components: list[ComponentStatus] = []

    components.append(
        ComponentStatus(key="api", status="ok", updated_at=now, detail=None)
    )

    try:
        await session.execute(text("SELECT 1"))
        components.append(
            ComponentStatus(key="db", status="ok", updated_at=now, detail=None)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("public.status.db_fail", error=str(exc))
        components.append(
            ComponentStatus(
                key="db",
                status="down",
                updated_at=now,
                detail=exc.__class__.__name__,
            )
        )

    try:
        redis_client = get_redis()
        pong = await redis_client.ping()
        components.append(
            ComponentStatus(
                key="redis",
                status="ok" if pong else "down",
                updated_at=now,
                detail=None if pong else "no pong",
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("public.status.redis_fail", error=str(exc))
        components.append(
            ComponentStatus(
                key="redis",
                status="down",
                updated_at=now,
                detail=exc.__class__.__name__,
            )
        )

    try:
        redis_client = get_redis()
        raw = await redis_client.get(_WORKER_HEARTBEAT_KEY)
        if raw is None:
            components.append(
                ComponentStatus(
                    key="worker",
                    status="down",
                    updated_at=now,
                    detail="no heartbeat",
                )
            )
        else:
            try:
                ts = float(raw)
                age = time.time() - ts
                if age > _WORKER_HEARTBEAT_TTL_SECS:
                    components.append(
                        ComponentStatus(
                            key="worker",
                            status="degraded",
                            updated_at=datetime.fromtimestamp(ts, tz=timezone.utc),
                            detail=f"stale ({int(age)}s)",
                        )
                    )
                else:
                    components.append(
                        ComponentStatus(
                            key="worker",
                            status="ok",
                            updated_at=datetime.fromtimestamp(ts, tz=timezone.utc),
                            detail=None,
                        )
                    )
            except (TypeError, ValueError):
                components.append(
                    ComponentStatus(
                        key="worker",
                        status="degraded",
                        updated_at=now,
                        detail="unparsable heartbeat",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        components.append(
            ComponentStatus(
                key="worker",
                status="down",
                updated_at=now,
                detail=exc.__class__.__name__,
            )
        )

    email_status = "ok"
    email_detail: str | None = None
    if settings.email_provider == "console":
        email_status = "degraded"
        email_detail = "console sender"
    elif settings.email_provider == "smtp" or (
        settings.email_provider == "auto" and settings.smtp_host
    ):
        if not settings.smtp_host:
            email_status = "down"
            email_detail = "no smtp host"
    else:
        email_status = "degraded"
        email_detail = "no smtp configured"
    components.append(
        ComponentStatus(
            key="email",
            status=email_status,
            updated_at=now,
            detail=email_detail,
        )
    )

    # Overall: any "down" -> down; else any "degraded" -> degraded; else ok.
    overall = "ok"
    if any(c.status == "down" for c in components):
        overall = "down"
    elif any(c.status == "degraded" for c in components):
        overall = "degraded"

    return StatusResponse(overall=overall, components=components, generated_at=now)


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=10, max_length=5000)
    company: str | None = Field(default=None, max_length=200)


class ContactResponse(BaseModel):
    status: str = "sent"


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


@router.post("/contact", response_model=ContactResponse)
async def submit_contact(body: ContactRequest, request: Request) -> ContactResponse:
    if body.company:
        log.info("public.contact.honeypot", ip=_client_ip(request))
        return ContactResponse(status="sent")

    ip = _client_ip(request)
    try:
        await check_rate_limit(
            f"public:contact:{ip}",
            limit=5,
            window_seconds=3600,
        )
    except RateLimitExceeded as exc:
        log.warning("public.contact.rate_limited", ip=ip, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many contact submissions. Please try again later.",
        ) from exc

    settings = get_settings()
    sender = get_email_sender()
    text_body = (
        f"New contact form submission\n\n"
        f"From: {body.name} <{body.email}>\n"
        f"Subject: {body.subject}\n"
        f"IP: {ip}\n"
        f"\n---\n{body.message}\n"
    )
    meta = (
        f"From: {body.name} <{body.email}>\n"
        f"Subject: {body.subject}\n"
        f"IP: {ip}"
    )
    msg_html = html.escape(body.message).replace("\n", "<br>\n")
    inner_html = (
        email_notice(title="New contact form submission", body=meta, variant="neutral")
        + f'<p style="margin:16px 0 0;font-size:15px;line-height:22px;color:#64748b;">Message</p>'
        + f'<div style="font-size:16px;line-height:24px;color:#1a1f2c;">{msg_html}</div>'
    )
    html_body = wrap_email_html(
        inner_html=inner_html,
        title=f"[Contact] {body.subject}",
        preheader=f"From {body.name} — {body.subject}",
        settings=settings,
    )
    try:
        await sender.send(
            to=settings.contact_email,
            subject=f"[Contact] {body.subject}",
            text_body=text_body,
            html_body=html_body,
            reply_to=body.email,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("public.contact.email_failed", error=str(exc), ip=ip)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to send message. Please try again later.",
        ) from exc

    log.info("public.contact.submitted", ip=ip, from_email=str(body.email))
    return ContactResponse(status="sent")
