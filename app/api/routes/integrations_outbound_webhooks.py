"""Tenant-managed outbound webhook subscriptions API."""
from __future__ import annotations

import secrets
import time
import uuid
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_str, encrypt_str
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin, require_non_viewer
from app.core.logging import get_logger
from app.models.analysis_result import AnalysisResult
from app.models.outbound_webhook import (
    OutboundWebhookEvent,
    OutboundWebhookSubscription,
)
from app.services.audit import record_audit
from app.services.notifications.outbound_webhook import (
    REQUEST_TIMEOUT_SECONDS,
    build_payload,
    build_signature,
    deliver_webhook_now,
)
from app.services.selfhost_policy import get_plan_spec, outbound_webhooks_allowed

router = APIRouter(prefix="/api/integrations/outbound-webhooks", tags=["integrations"])
log = get_logger(__name__)


_SECRET_BYTES = 32

_MAX_PER_TENANT = 25


def _new_secret() -> str:
    return secrets.token_hex(_SECRET_BYTES)


def _assert_webhooks_allowed(principal: CurrentPrincipal) -> None:
    spec = get_plan_spec(principal.tenant)
    if not outbound_webhooks_allowed(spec):
        raise HTTPException(
            status_code=403,
            detail="Outbound webhooks are not available",
        )


def _validate_url(url: str) -> None:
    """Reject anything that isn't a fully-qualified ``https://`` URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400, detail="Webhook URL must use http(s)"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="Webhook URL is missing a host")
    if parsed.scheme == "http" and not (
        host == "localhost" or host.endswith(".localhost") or host == "127.0.0.1"
    ):
        raise HTTPException(
            status_code=400,
            detail="Use https:// for non-localhost webhook receivers",
        )


def _normalise_events(events: list[str]) -> list[str]:
    """Validate the requested events against the published enum."""
    if not events:
        return [e.value for e in OutboundWebhookEvent]
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in events:
        if raw in seen:
            continue
        try:
            OutboundWebhookEvent(raw)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown event '{raw}'. "
                    f"Allowed: {', '.join(e.value for e in OutboundWebhookEvent)}"
                ),
            )
        deduped.append(raw)
        seen.add(raw)
    return deduped


class _BaseUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(max_length=2048)
    events: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class CreateRequest(_BaseUpsert):
    pass


class UpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, max_length=2048)
    events: list[str] | None = None
    enabled: bool | None = None


class SubscriptionOut(BaseModel):
    id: str
    name: str
    url: str
    events: list[str]
    enabled: bool
    consecutive_failures: int
    last_delivery_at: str | None
    last_status: int | None
    last_error: str | None
    disabled_at: str | None


class CreateResponse(SubscriptionOut):
    """Response for create + rotate; carries the plaintext ``secret``."""

    secret: str


class RedeliverRequest(BaseModel):
    analysis_id: uuid.UUID


class TestResponse(BaseModel):
    ok: bool
    status: int
    detail: str | None = None


def _to_out(sub: OutboundWebhookSubscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=str(sub.id),
        name=sub.name,
        url=sub.url,
        events=list(sub.events or []),
        enabled=bool(sub.enabled),
        consecutive_failures=int(sub.consecutive_failures or 0),
        last_delivery_at=sub.last_delivery_at.isoformat()
        if sub.last_delivery_at
        else None,
        last_status=sub.last_status,
        last_error=sub.last_error,
        disabled_at=sub.disabled_at.isoformat() if sub.disabled_at else None,
    )


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    principal: Annotated[CurrentPrincipal, Depends(require_non_viewer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[SubscriptionOut]:
    rows = (
        await session.execute(
            select(OutboundWebhookSubscription)
            .where(OutboundWebhookSubscription.tenant_id == principal.tenant.id)
            .order_by(OutboundWebhookSubscription.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=CreateResponse, status_code=201)
async def create_subscription(
    body: CreateRequest,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CreateResponse:
    _assert_webhooks_allowed(principal)
    _validate_url(body.url)

    count = (
        await session.execute(
            select(OutboundWebhookSubscription).where(
                OutboundWebhookSubscription.tenant_id == principal.tenant.id
            )
        )
    ).scalars().all()
    if len(count) >= _MAX_PER_TENANT:
        raise HTTPException(
            status_code=400,
            detail=f"Tenant cap reached ({_MAX_PER_TENANT} subscriptions)",
        )

    events = _normalise_events(body.events)
    secret = _new_secret()
    sub = OutboundWebhookSubscription(
        tenant_id=principal.tenant.id,
        created_by_user_id=principal.user.id,
        name=body.name,
        url=body.url,
        secret_enc=encrypt_str(secret),
        events=events,
        enabled=body.enabled,
    )
    session.add(sub)
    await session.flush()
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="outbound_webhook_created",
        actor=principal.user.email,
        target=str(sub.id),
        meta={"url": sub.url, "events": events},
    )
    await session.commit()

    return CreateResponse(secret=secret, **_to_out(sub).model_dump())


@router.patch("/{subscription_id}", response_model=SubscriptionOut)
async def update_subscription(
    subscription_id: uuid.UUID,
    body: UpdateRequest,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SubscriptionOut:
    _assert_webhooks_allowed(principal)
    sub = await _load_owned(session, subscription_id, principal.tenant.id)

    changes: dict[str, object] = {}
    if body.name is not None:
        sub.name = body.name.strip()
        changes["name"] = sub.name
    if body.url is not None:
        _validate_url(body.url)
        sub.url = body.url
        changes["url"] = sub.url
    if body.events is not None:
        sub.events = _normalise_events(body.events)
        changes["events"] = sub.events
    if body.enabled is not None:
        sub.enabled = body.enabled
        changes["enabled"] = sub.enabled
        if body.enabled:
            sub.disabled_at = None
            sub.consecutive_failures = 0
            sub.last_error = None

    if changes:
        await record_audit(
            session,
            tenant_id=principal.tenant.id,
            action="outbound_webhook_updated",
            actor=principal.user.email,
            target=str(sub.id),
            meta=changes,
        )
    await session.commit()
    return _to_out(sub)


@router.post(
    "/{subscription_id}/rotate-secret",
    response_model=CreateResponse,
)
async def rotate_secret(
    subscription_id: uuid.UUID,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CreateResponse:
    _assert_webhooks_allowed(principal)
    sub = await _load_owned(session, subscription_id, principal.tenant.id)
    secret = _new_secret()
    sub.secret_enc = encrypt_str(secret)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="outbound_webhook_rotated",
        actor=principal.user.email,
        target=str(sub.id),
    )
    await session.commit()
    return CreateResponse(secret=secret, **_to_out(sub).model_dump())


@router.post("/{subscription_id}/test", response_model=TestResponse)
async def test_subscription(
    subscription_id: uuid.UUID,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TestResponse:
    """Synchronous probe POST. Useful for debugging during setup."""
    _assert_webhooks_allowed(principal)
    sub = await _load_owned(session, subscription_id, principal.tenant.id)
    secret = decrypt_str(sub.secret_enc) if sub.secret_enc else ""
    if not secret:
        raise HTTPException(status_code=400, detail="Stored secret cannot be decrypted")

    payload = build_payload(
        OutboundWebhookEvent.ANALYSIS_COMPLETED.value,
        principal.tenant.id,
        {
            "id": "test_analysis",
            "provider": "exlogare",
            "source": "test_delivery",
            "ci_run_id": "test",
            "ci_job_id": None,
            "project_path": "exlogare/test",
            "root_cause": "This is a test delivery from Exlogare",
            "explanation": "If you see this, the receiver is reachable",
            "fix_suggestion": "No action needed",
            "severity": "low",
            "confidence": 1.0,
            "needs_more_context": False,
        },
    )
    import json

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Exlogare-Webhook/1.0",
        "X-Exlogare-Event": payload["type"],
        "X-Exlogare-Delivery": payload["id"],
        "X-Exlogare-Signature": build_signature(secret, ts, body),
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(sub.url, content=body, headers=headers)
        ok = 200 <= resp.status_code < 300
        return TestResponse(
            ok=ok,
            status=resp.status_code,
            detail=None if ok else resp.text[:200],
        )
    except httpx.HTTPError as exc:
        return TestResponse(ok=False, status=0, detail=str(exc)[:200])


@router.post("/{subscription_id}/redeliver")
async def redeliver(
    subscription_id: uuid.UUID,
    body: RedeliverRequest,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Re-emit ``analysis.completed`` for an existing analysis id."""
    _assert_webhooks_allowed(principal)
    sub = await _load_owned(session, subscription_id, principal.tenant.id)

    row = (
        await session.execute(
            select(AnalysisResult).where(
                AnalysisResult.id == body.analysis_id,
                AnalysisResult.tenant_id == principal.tenant.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    payload_data = {
        "id": str(row.id),
        "provider": row.provider,
        "source": row.source,
        "ci_run_id": row.ci_run_id,
        "ci_job_id": row.ci_job_id or None,
        "project_id": row.project_id,
        "project_path": row.project_path,
        "project_web_url": row.project_web_url,
        "pipeline_url": row.pipeline_url,
        "job_url": row.job_url,
        "mr_iid": row.mr_iid,
        "root_cause": row.root_cause,
        "explanation": row.explanation,
        "fix_suggestion": row.fix_suggestion,
        "severity": row.severity.value if row.severity else None,
        "confidence": row.confidence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    payload = build_payload(
        OutboundWebhookEvent.ANALYSIS_COMPLETED.value,
        principal.tenant.id,
        payload_data,
    )

    from app.workers.tasks import deliver_webhook  # type: ignore

    deliver_webhook.delay(str(sub.id), payload)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="outbound_webhook_redelivered",
        actor=principal.user.email,
        target=str(sub.id),
        meta={"analysis_id": str(row.id)},
    )
    await session.commit()
    return {"status": "scheduled", "delivery_id": payload["id"]}


@router.delete("/{subscription_id}")
async def delete_subscription(
    subscription_id: uuid.UUID,
    principal: Annotated[CurrentPrincipal, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _assert_webhooks_allowed(principal)
    sub = await _load_owned(session, subscription_id, principal.tenant.id)
    await session.delete(sub)
    await record_audit(
        session,
        tenant_id=principal.tenant.id,
        action="outbound_webhook_deleted",
        actor=principal.user.email,
        target=str(subscription_id),
    )
    await session.commit()
    return {"status": "deleted"}


async def _load_owned(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> OutboundWebhookSubscription:
    sub = (
        await session.execute(
            select(OutboundWebhookSubscription).where(
                OutboundWebhookSubscription.id == subscription_id,
                OutboundWebhookSubscription.tenant_id == tenant_id,
            )
        )
    ).scalars().first()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub
