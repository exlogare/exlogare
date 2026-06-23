from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.schemas.failure_event import FailureEvent
from app.services.ingestion.base import WebhookIngestor

log = get_logger(__name__)


def parse_github_webhook_body(
    tenant_id: uuid.UUID,
    ci_connection_id: uuid.UUID | None,
    event_name: str,
    p: dict[str, Any],
) -> FailureEvent | None:
    """Entry point for GitHub (uses ``X-GitHub-Event`` from headers)."""
    if event_name == "ping":
        return None
    if event_name == "workflow_run":
        return _parse_workflow_run(tenant_id, ci_connection_id, p)
    if event_name == "check_run":
        return _parse_check_run(tenant_id, ci_connection_id, p)
    return None


class GitHubWebhookIngestor(WebhookIngestor):
    """Compatible with :class:`WebhookIngestor` when the route passes event name out-of-band."""

    async def parse(
        self, tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, payload: dict[str, Any]
    ) -> FailureEvent | None:
        ev = (payload or {}).get("_event_name")
        if not isinstance(ev, str):
            return None
        body = {k: v for k, v in (payload or {}).items() if k != "_event_name"}
        return parse_github_webhook_body(
            tenant_id, ci_connection_id, ev, body
        )


def _parse_workflow_run(
    tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, p: dict[str, Any]
) -> FailureEvent | None:
    wr = p.get("workflow_run")
    if not isinstance(wr, dict):
        return None
    if p.get("action") != "completed":
        return None
    concl = wr.get("conclusion")
    if concl not in ("failure", "cancelled", "timed_out"):
        return None
    repo = wr.get("repository") or p.get("repository") or {}
    rid = wr.get("id")
    prs = wr.get("pull_requests") or []
    mr: str | None = None
    if prs and isinstance(prs[0], dict) and prs[0].get("number") is not None:
        mr = str(prs[0]["number"])
    return FailureEvent(
        tenant_id=tenant_id,
        ci_connection_id=ci_connection_id,
        provider="github",
        source="github_webhook",
        ci_run_id=str(rid) if rid is not None else "",
        project_id=str(repo.get("id") or ""),
        project_path=repo.get("full_name"),
        project_web_url=repo.get("html_url"),
        pipeline_url=wr.get("html_url") or wr.get("url") or "",
        ref=wr.get("head_branch"),
        sha=wr.get("head_sha"),
        status=str(concl or "failed"),
        mr_iid=mr,
        raw=p,
    )


def _parse_check_run(
    tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, p: dict[str, Any]
) -> FailureEvent | None:
    cr = p.get("check_run")
    if not isinstance(cr, dict):
        return None
    concl = cr.get("conclusion")
    if concl not in ("failure", "cancelled", "timed_out", "action_required"):
        return None
    if cr.get("status") and cr.get("status") not in ("completed",):
        return None
    repo = cr.get("check_suite", {}).get("repository") or cr.get("repository")
    if not isinstance(repo, dict):
        repo = p.get("repository") or {}
    return FailureEvent(
        tenant_id=tenant_id,
        ci_connection_id=ci_connection_id,
        provider="github",
        source="github_webhook",
        ci_run_id=str(cr.get("id") or ""),
        ci_job_id="",
        project_id=str(repo.get("id") or ""),
        project_path=repo.get("full_name"),
        project_web_url=repo.get("html_url"),
        pipeline_url=cr.get("html_url") or "",
        ref=cr.get("head_branch") or p.get("check_suite", {}).get("head_branch"),
        sha=cr.get("head_sha"),
        job_url=cr.get("html_url"),
        status=str(concl or "failed"),
        raw=p,
    )
