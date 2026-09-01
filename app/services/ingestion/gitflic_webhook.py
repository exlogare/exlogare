"""GitFlic webhook ingestor."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.schemas.failure_event import FailureEvent
from app.services.ingestion.base import WebhookIngestor

log = get_logger(__name__)


class GitFlicWebhookIngestor(WebhookIngestor):
    async def parse(
        self,
        tenant_id: uuid.UUID,
        ci_connection_id: uuid.UUID | None,
        payload: dict[str, Any],
    ) -> FailureEvent | None:
        action = str(payload.get("action") or "").upper()
        if action != "PIPELINE_FAIL":
            return None

        project = payload.get("project") or {}
        pipeline = payload.get("pipeline") or {}
        commit = pipeline.get("commit") or {}

        owner_alias = project.get("owner_alias") or ""
        alias = project.get("alias") or ""
        project_path = f"{owner_alias}/{alias}" if owner_alias and alias else None

        web_root = _web_root_from_transport(project.get("http_transport_url"))
        if web_root and project_path:
            project_web_url = f"{web_root}/project/{project_path}"
        else:
            project_web_url = None

        local_id = pipeline.get("local_id")
        pipeline_id = pipeline.get("id")
        pipeline_url = pipeline.get("url")
        if not pipeline_url and project_web_url and local_id is not None:
            pipeline_url = f"{project_web_url}/ci-cd/pipeline/{local_id}"

        ref = pipeline.get("ref")
        if ref and ref.startswith("refs/heads/"):
            ref = ref[len("refs/heads/"):]
        elif ref and ref.startswith("refs/tags/"):
            ref = ref[len("refs/tags/"):]

        return FailureEvent(
            tenant_id=tenant_id,
            ci_connection_id=ci_connection_id,
            provider="gitflic",
            source="gitflic_webhook",
            ci_run_id=str(local_id if local_id is not None else (pipeline_id or "")),
            project_id=str(payload.get("project_id") or project.get("project_id") or ""),
            project_path=project_path,
            project_web_url=project_web_url,
            pipeline_url=pipeline_url,
            status="failed",
            ref=ref,
            sha=pipeline.get("commit_id") or commit.get("id"),
            actor=commit.get("author_name"),
            raw=payload,
        )


def _web_root_from_transport(http_transport_url: str | None) -> str | None:
    """Derive the web root (scheme://host) from the project's git URL."""
    if not http_transport_url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(http_transport_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return None
    return None
