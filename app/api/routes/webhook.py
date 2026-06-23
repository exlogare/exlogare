from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import constant_time_equals, decrypt_str
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.rate_limit import RateLimitExceeded, check_rate_limit
from app.models.ci_connection import CIConnection, CIProvider, ConnectionStatus
from app.models.pipeline_event import PipelineEvent
from app.services.audit import record_audit
from app.services.ingestion.bitbucket_webhook import parse_bitbucket_webhook_body
from app.services.ingestion.github_webhook import parse_github_webhook_body
from app.services.ingestion.gitflic_webhook import GitFlicWebhookIngestor
from app.services.ingestion.gitlab_webhook import GitLabWebhookIngestor
from app.services.pipeline import persist_ingestion_event
from app.services.tenants import (
    resolve_bitbucket_connection,
    resolve_gitflic_connection,
    resolve_gitlab_connection,
    resolve_github_connection,
)

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _extract_project_id(payload: dict) -> str | None:
    if (pid := payload.get("project_id")) is not None:
        return str(pid)
    project = payload.get("project") or {}
    if (pid := project.get("id")) is not None:
        return str(pid)
    return None


def _verify_secret(provided: str | None, connection_secret_enc: str | None) -> bool:
    settings = get_settings()
    expected = None
    if connection_secret_enc:
        try:
            expected = decrypt_str(connection_secret_enc)
        except ValueError:
            expected = None
    if not expected:
        expected = settings.gitlab_webhook_secret or None
    if not expected:
        return True
    if not provided:
        return False
    return constant_time_equals(provided, expected)


def _extract_pipeline_status_and_meta(payload: dict) -> tuple[str | None, dict]:
    kind = payload.get("object_kind") or payload.get("event_type")
    attrs = payload.get("object_attributes") or {}
    status_val: str | None = None
    meta: dict = {}
    if kind == "pipeline":
        status_val = attrs.get("status")
        meta = {
            "ci_run_id": str(attrs.get("id")) if attrs.get("id") is not None else None,
            "ref": attrs.get("ref"),
            "duration": attrs.get("duration"),
            "project_id": _extract_project_id(payload),
            "project_path": (payload.get("project") or {}).get("path_with_namespace"),
        }
    elif kind in {"build", "job"}:
        status_val = payload.get("build_status") or payload.get("status")
        meta = {
            "ci_run_id": str(payload.get("pipeline_id") or payload.get("build_id") or ""),
            "ref": payload.get("ref"),
            "duration": None,
            "project_id": _extract_project_id(payload),
            "project_path": (payload.get("project") or {}).get("path_with_namespace"),
        }
    return status_val, meta


def _verify_github_hub_signature(
    body: bytes, secret: str, signature: str | None
) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    want = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature[7:])


def _verify_bitbucket_hub_signature(
    body: bytes, secret: str, signature: str | None
) -> bool:
    """Validate Bitbucket's ``X-Hub-Signature`` HMAC-SHA256."""
    if not secret:
        return True
    if not signature:
        return False
    if signature.startswith("sha256="):
        sig_hex = signature[7:]
    elif signature.startswith("sha1="):
        # Some legacy DC plugins use SHA-1 — not recommended but tolerated.
        want = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(want, signature[5:])
    else:
        sig_hex = signature
    want = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig_hex)


def _bitbucket_repo_identifiers(payload: dict) -> tuple[str | None, str | None]:
    """Return (repository_uuid, full_name) extracted from a Bitbucket payload."""
    r = (payload.get("repository") or {}) or {}
    uuid_ = r.get("uuid")
    full_name = r.get("full_name")
    if not full_name:
        # DC payload shape
        proj = (r.get("project") or {}) or {}
        key = proj.get("key")
        slug = r.get("slug")
        if key and slug:
            full_name = f"{key}/{slug}"
            uuid_ = uuid_ or full_name  # DC uses the synthetic id as the lookup key
    return (str(uuid_) if uuid_ else None, str(full_name) if full_name else None)


def _github_repo_id(payload: dict) -> str | None:
    r = (payload.get("repository") or {}) or {}
    if (rid := r.get("id")) is not None:
        return str(rid)
    wr = (payload.get("workflow_run") or {}) or {}
    r2 = (wr.get("repository") or {}) or {}
    if (rid := r2.get("id")) is not None:
        return str(rid)
    return None


async def _record_pipeline_event(
    session: AsyncSession,
    connection: CIConnection,
    status_val: str,
    meta: dict,
    *,
    provider: str = "gitlab",
) -> None:
    ci_run_id = meta.get("ci_run_id")
    if not ci_run_id:
        return
    duration = meta.get("duration")
    try:
        duration_int = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_int = None
    stmt = (
        insert(PipelineEvent)
        .values(
            tenant_id=connection.tenant_id,
            ci_connection_id=connection.id,
            provider=provider,
            ci_run_id=ci_run_id,
            status=status_val,
            project_id=meta.get("project_id"),
            project_path=meta.get("project_path"),
            ref=meta.get("ref"),
            duration_seconds=duration_int,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "provider", "ci_run_id"],
            set_={
                "status": status_val,
                "duration_seconds": duration_int,
            },
        )
    )
    await session.execute(stmt)


@router.post("/gitlab", status_code=status.HTTP_202_ACCEPTED)
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
    session: AsyncSession = Depends(get_db),
) -> dict:
    payload = await request.json()

    project_id = _extract_project_id(payload)
    connection = await resolve_gitlab_connection(session, project_id=project_id)
    if not connection:
        log.warning("webhook.no_connection", project_id=project_id)
        raise HTTPException(status_code=404, detail="No CI connection found for this project")

    try:
        await check_rate_limit(
            f"tenant:{connection.tenant_id}:webhook",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    if not _verify_secret(x_gitlab_token, connection.webhook_secret_enc):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    connection.last_delivery_at = datetime.now(tz=timezone.utc)
    if connection.status == ConnectionStatus.PENDING_MANUAL:
        connection.status = ConnectionStatus.ACTIVE

    status_val, meta = _extract_pipeline_status_and_meta(payload)
    if status_val:
        await _record_pipeline_event(session, connection, status_val, meta)

    ingestor = GitLabWebhookIngestor()
    event = await ingestor.parse(connection.tenant_id, connection.id, payload)
    if event is None:
        await session.commit()
        return {"status": "ignored", "reason": "non-failure event"}

    try:
        await check_rate_limit(
            f"tenant:{connection.tenant_id}:webhook",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    settings = get_settings()
    secret = ""
    if connection.webhook_secret_enc:
        try:
            secret = decrypt_str(connection.webhook_secret_enc)
        except ValueError:
            secret = ""
    if not secret:
        secret = settings.bitbucket_webhook_secret or ""
    if secret and not _verify_bitbucket_hub_signature(body, secret, x_hub_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    connection.last_delivery_at = datetime.now(tz=timezone.utc)
    if connection.status == ConnectionStatus.PENDING_MANUAL:
        connection.status = ConnectionStatus.ACTIVE

    event = parse_bitbucket_webhook_body(
        connection.tenant_id, connection.id, ev_name, payload
    )
    if event is None:
        await session.commit()
        return {"status": "ignored", "reason": "non-failure event"}

    created = await persist_ingestion_event(session, event)
    await record_audit(
        session,
        tenant_id=connection.tenant_id,
        action="bitbucket_webhook_received",
        target=f"bitbucket:{event.ci_run_id}",
        meta={"deduped": created is None, "source": event.source},
    )
    await session.commit()
    if created is not None:
        from app.workers.tasks import analyze_failure

        analyze_failure.delay(event.model_dump(mode="json"))
    return {
        "status": "accepted" if created else "deduped",
        "ci_run_id": event.ci_run_id,
    }


@router.post("/gitflic", status_code=status.HTTP_202_ACCEPTED)
async def gitflic_webhook(
    request: Request,
    secret_query: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """GitFlic project webhook receiver."""
    payload = await request.json()
    project_uuid = (payload.get("project") or {}).get("project_id") or payload.get(
        "project_id"
    )
    connection = await resolve_gitflic_connection(
        session, project_id=str(project_uuid) if project_uuid else None
    )
    if not connection or connection.provider != CIProvider.GITFLIC:
        log.warning("webhook.gitflic_no_connection", project_id=project_uuid)
        raise HTTPException(
            status_code=404, detail="No CI connection found for this project"
        )

    try:
        await check_rate_limit(
            f"tenant:{connection.tenant_id}:webhook",
            limit=get_settings().rate_limit_per_minute,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    settings = get_settings()
    provided = secret_query or request.headers.get("X-GitFlic-Secret") or request.headers.get(
        "X-Gitflic-Secret"
    )
    expected = None
    if connection.webhook_secret_enc:
        try:
            expected = decrypt_str(connection.webhook_secret_enc)
        except ValueError:
            expected = None
    if not expected:
        expected = settings.gitflic_webhook_secret or None
    if expected and (not provided or not constant_time_equals(provided, expected)):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    connection.last_delivery_at = datetime.now(tz=timezone.utc)
    if connection.status == ConnectionStatus.PENDING_MANUAL:
        connection.status = ConnectionStatus.ACTIVE

    ingestor = GitFlicWebhookIngestor()
    event = await ingestor.parse(connection.tenant_id, connection.id, payload)
    if event is None:
        await session.commit()
        return {"status": "ignored", "reason": "non-failure event"}

    created = await persist_ingestion_event(session, event)
    await record_audit(
        session,
        tenant_id=connection.tenant_id,
        action="gitflic_webhook_received",
        target=f"gitflic:{event.ci_run_id}",
        meta={"deduped": created is None, "source": event.source},
    )
    await session.commit()
    if created is not None:
        from app.workers.tasks import analyze_failure

        analyze_failure.delay(event.model_dump(mode="json"))
    return {
        "status": "accepted" if created else "deduped",
        "ci_run_id": event.ci_run_id,
    }
