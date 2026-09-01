"""Bitbucket webhook payload parser (Cloud + DC)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.schemas.failure_event import FailureEvent
from app.services.ingestion.base import WebhookIngestor

log = get_logger(__name__)


_CLOUD_FAIL_STATES = {"FAILED", "STOPPED", "ERROR"}
_DC_FAIL_STATES = {"FAILED", "FAILING"}


def parse_bitbucket_webhook_body(
    tenant_id: uuid.UUID,
    ci_connection_id: uuid.UUID | None,
    event_name: str,
    p: dict[str, Any],
) -> FailureEvent | None:
    """Entry point — uses ``X-Event-Key`` from the request headers."""
    ev = (event_name or "").lower()
    if ev in ("diagnostics:ping", "ping"):
        return None
    if ev == "repo:commit_status_updated":
        return _parse_cloud_commit_status(tenant_id, ci_connection_id, p)
    if ev == "repo:build_status_updated":
        return _parse_dc_build_status(tenant_id, ci_connection_id, p)
    return None


class BitbucketWebhookIngestor(WebhookIngestor):
    async def parse(
        self,
        tenant_id: uuid.UUID,
        ci_connection_id: uuid.UUID | None,
        payload: dict[str, Any],
    ) -> FailureEvent | None:
        ev = (payload or {}).get("_event_name")
        if not isinstance(ev, str):
            return None
        body = {k: v for k, v in (payload or {}).items() if k != "_event_name"}
        return parse_bitbucket_webhook_body(tenant_id, ci_connection_id, ev, body)


def _parse_cloud_commit_status(
    tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, p: dict[str, Any]
) -> FailureEvent | None:
    cs = p.get("commit_status")
    if not isinstance(cs, dict):
        return None
    state = (cs.get("state") or "").upper()
    if state not in _CLOUD_FAIL_STATES:
        return None
    repo = p.get("repository") or {}
    full_name = repo.get("full_name") or ""
    workspace = ""
    slug = ""
    if "/" in full_name:
        workspace, slug = full_name.split("/", 1)
    pipeline_url = cs.get("url") or ""
    run_id = (
        _extract_pipeline_id_from_url(pipeline_url)
        or _extract_build_number_from_key(cs.get("key"))
        or ""
    )
    commit = cs.get("commit") or p.get("commit") or {}
    sha = commit.get("hash")
    ref = cs.get("refname") or (cs.get("ref") or {}).get("name") if isinstance(cs.get("ref"), dict) else cs.get("refname")
    if not ref:
        ref = cs.get("name")
    return FailureEvent(
        tenant_id=tenant_id,
        ci_connection_id=ci_connection_id,
        provider="bitbucket",
        source="bitbucket_webhook",
        ci_run_id=str(run_id) if run_id else "",
        project_id=str(repo.get("uuid") or ""),
        project_path=full_name or (f"{workspace}/{slug}" if workspace and slug else None),
        project_web_url=(repo.get("links") or {}).get("html", {}).get("href"),
        pipeline_url=pipeline_url,
        ref=ref,
        sha=sha,
        status=state.lower(),
        raw=p,
    )


def _parse_dc_build_status(
    tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, p: dict[str, Any]
) -> FailureEvent | None:
    bs = p.get("buildStatus") or p.get("commitStatus")
    if not isinstance(bs, dict):
        return None
    state = (bs.get("state") or "").upper()
    if state not in _DC_FAIL_STATES:
        return None
    repo = p.get("repository") or {}
    project = repo.get("project") or {}
    project_key = project.get("key") or ""
    repo_slug = repo.get("slug") or ""
    commit = p.get("commit") or bs.get("commit") or {}
    sha = commit.get("id") or commit.get("hash") or bs.get("commitId")
    ref = (
        bs.get("ref")
        or (commit.get("ref") if isinstance(commit, dict) else None)
        or bs.get("buildNumber")
    )
    pipeline_url = bs.get("url") or ""
    project_path = (
        f"{project_key}/{repo_slug}" if project_key and repo_slug else None
    )
    # DC's repo URL: <base>/projects/{KEY}/repos/{slug}/browse
    project_web_url = (
        (repo.get("links") or {}).get("self", [{}])[0].get("href")
        if isinstance(repo.get("links"), dict)
        else None
    )
    return FailureEvent(
        tenant_id=tenant_id,
        ci_connection_id=ci_connection_id,
        provider="bitbucket",
        source="bitbucket_webhook",
        ci_run_id=str(bs.get("key") or bs.get("buildNumber") or sha or ""),
        project_id=str(repo.get("id") or ""),
        project_path=project_path,
        project_web_url=project_web_url,
        pipeline_url=pipeline_url,
        job_url=pipeline_url,
        ref=ref,
        sha=sha,
        status=state.lower(),
        raw=p,
    )


def _extract_pipeline_id_from_url(url: str | None) -> str | None:
    """Pull a pipeline result number out of a Cloud results URL."""
    if not url:
        return None
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    last = base.rsplit("/", 1)[-1]
    return last if last and last.isdigit() else None


def _extract_build_number_from_key(key: object) -> str | None:
    """Pull the build number out of a ``PIPELINE-{N}`` commit_status key."""
    if not isinstance(key, str):
        return None
    s = key.strip()
    if "-" not in s:
        return None
    tail = s.rsplit("-", 1)[-1]
    return tail if tail.isdigit() else None
