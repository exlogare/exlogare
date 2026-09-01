"""HMAC-signed outbound webhook delivery."""
from __future__ import annotations

import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.models.outbound_webhook import (
    OutboundWebhookEvent,
    OutboundWebhookSubscription,
)

log = get_logger(__name__)

MAX_CONSECUTIVE_FAILURES = 10
"""After this many consecutive *delivery* failures (transport or
4xx/5xx) we auto-disable the subscription. The number is deliberately
low — webhook receivers either work or they don't, and a row stuck at
8/10 failures is a worse signal than "we gave up, please re-enable"."""

REQUEST_TIMEOUT_SECONDS = 8.0
"""Network timeout for a single attempt. Receivers like PagerDuty
Events API specify their own 30s budget; we stay below to leave room
for our own retry handler. Exceeding this counts as a transport
error."""


@dataclass(frozen=True)
class DeliveryOutcome:
    ok: bool
    status: int
    error: str | None


def build_signature(secret: str, timestamp: int, body: bytes) -> str:
    """Return the value for the ``X-Exlogare-Signature`` header."""
    msg = f"{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), msg, sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(
    secret: str, header: str, body: bytes, *, max_age_seconds: int = 300
) -> bool:
    """Counterpart to :func:`build_signature` for receiver-side scripts."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts = int(parts.get("t", "0"))
        provided = parts.get("v1", "")
    except (ValueError, AttributeError):
        return False
    if not provided:
        return False
    if abs(int(time.time()) - ts) > max_age_seconds:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, sha256
    ).hexdigest()
    return hmac.compare_digest(provided, expected)


def build_payload(
    event_type: str,
    tenant_id: uuid.UUID,
    analysis: dict[str, Any],
    *,
    delivered_at: datetime | None = None,
) -> dict[str, Any]:
    """Canonical wire payload. Stable contract — see public docs."""
    when = (delivered_at or datetime.now(tz=timezone.utc)).isoformat()
    return {
        "type": event_type,
        "id": f"evt_{uuid.uuid4()}",
        "delivered_at": when,
        "tenant_id": str(tenant_id),
        "analysis": analysis,
    }


def _serialise_payload(payload: dict[str, Any]) -> bytes:
    """JSON-encode with sort_keys to keep signing reproducible."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _safe_decrypt(token: str | None) -> str | None:
    """Wrap :func:`decrypt_str` so a corrupted ciphertext is treated"""
    if not token:
        return None
    try:
        return decrypt_str(token)
    except ValueError:
        return None


async def deliver_webhook_now(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    payload: dict[str, Any],
) -> DeliveryOutcome:
    """Single delivery attempt. Updates subscription telemetry."""
    sub = await session.get(OutboundWebhookSubscription, subscription_id)
    if sub is None:
        return DeliveryOutcome(ok=False, status=0, error="subscription_not_found")
    if not sub.enabled:
        return DeliveryOutcome(ok=False, status=0, error="subscription_disabled")

    body = _serialise_payload(payload)
    secret = _safe_decrypt(sub.secret_enc)
    if not secret:
        sub.enabled = False
        sub.disabled_at = datetime.now(tz=timezone.utc)
        sub.last_error = "secret_decryption_failed"
        return DeliveryOutcome(ok=False, status=0, error="secret_decryption_failed")

    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Exlogare-Webhook/1.0",
        "X-Exlogare-Event": str(payload.get("type", "")),
        "X-Exlogare-Delivery": str(payload.get("id", "")),
        "X-Exlogare-Signature": build_signature(secret, ts, body),
    }

    status = 0
    error: str | None = None
    ok = False
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(sub.url, content=body, headers=headers)
            status = resp.status_code
            ok = 200 <= status < 300
            if not ok:
                error = f"http_{status}"
    except httpx.HTTPError as exc:
        ok = False
        error = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        ok = False
        error = type(exc).__name__
        log.exception("outbound_webhook.unexpected_error", subscription_id=str(sub.id))

    now = datetime.now(tz=timezone.utc)
    sub.last_delivery_at = now
    sub.last_status = status or None
    if ok:
        sub.consecutive_failures = 0
        sub.last_error = None
    else:
        sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
        sub.last_error = (error or f"http_{status}")[:512]
        if sub.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            sub.enabled = False
            sub.disabled_at = now
            log.warning(
                "outbound_webhook.auto_disabled",
                subscription_id=str(sub.id),
                tenant_id=str(sub.tenant_id),
                consecutive_failures=sub.consecutive_failures,
            )

    return DeliveryOutcome(ok=ok, status=status, error=error)


async def schedule_webhook_fanout(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    event_type: str,
    analysis: dict[str, Any],
) -> list[uuid.UUID]:
    """Find every active subscription that opted in to ``event_type``"""
    from app.models.tenant import Tenant
    from app.services.selfhost_policy import get_plan_spec, outbound_webhooks_allowed

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return []
    spec = get_plan_spec(tenant)
    if not outbound_webhooks_allowed(spec):
        log.debug(
            "outbound_webhook.skipped_disabled",
            tenant_id=str(tenant_id),
        )
        return []

    stmt = select(OutboundWebhookSubscription).where(
        OutboundWebhookSubscription.tenant_id == tenant_id,
        OutboundWebhookSubscription.enabled.is_(True),
    )
    rows = (await session.execute(stmt)).scalars().all()

    scheduled: list[uuid.UUID] = []
    payload_template = analysis

    from app.workers.tasks import deliver_webhook  # type: ignore

    for sub in rows:
        events: list[str] = list(sub.events or [])
        if event_type not in events:
            continue
        payload = build_payload(event_type, tenant_id, payload_template)
        deliver_webhook.delay(str(sub.id), payload)  # type: ignore[arg-type]
        scheduled.append(sub.id)
    if scheduled:
        log.info(
            "outbound_webhook.scheduled",
            tenant_id=str(tenant_id),
            event=event_type,
            count=len(scheduled),
        )
    return scheduled


def event_is_known(event: str) -> bool:
    try:
        OutboundWebhookEvent(event)
    except ValueError:
        return False
    return True
