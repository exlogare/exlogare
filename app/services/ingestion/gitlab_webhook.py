from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.schemas.failure_event import FailureEvent
from app.services.ingestion.base import WebhookIngestor

log = get_logger(__name__)


class GitLabWebhookIngestor(WebhookIngestor):
    """Parses GitLab `pipeline_failed` and `job_failed` webhook payloads."""

    async def parse(
        self, tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, payload: dict[str, Any]
    ) -> FailureEvent | None:
        kind = payload.get("object_kind") or payload.get("event_type")
        if kind == "pipeline":
            return self._parse_pipeline(tenant_id, ci_connection_id, payload)
        if kind in {"build", "job"}:
            return self._parse_job(tenant_id, ci_connection_id, payload)
        return None

    def _parse_pipeline(
        self, tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, payload: dict
    ) -> FailureEvent | None:
        attrs = payload.get("object_attributes") or {}
        status = attrs.get("status") or attrs.get("detailed_status")
        if status != "failed":
            return None
        project = payload.get("project") or {}
        mr = (payload.get("merge_request") or {})
        pipeline_id = attrs.get("id")
        return FailureEvent(
            tenant_id=tenant_id,
            ci_connection_id=ci_connection_id,
            provider="gitlab",
            source="gitlab_webhook",
            ci_run_id=str(pipeline_id) if pipeline_id is not None else "",
            project_id=str(project.get("id") or ""),
            project_path=project.get("path_with_namespace"),
            project_web_url=project.get("web_url"),
            pipeline_url=_pipeline_url_for(project, pipeline_id, attrs.get("url")),
            mr_iid=str(mr["iid"]) if mr.get("iid") is not None else None,
            status=status,
            ref=attrs.get("ref"),
            sha=attrs.get("sha"),
            actor=(payload.get("user") or {}).get("username"),
            raw=payload,
        )

    def _parse_job(
        self, tenant_id: uuid.UUID, ci_connection_id: uuid.UUID | None, payload: dict
    ) -> FailureEvent | None:
        status = payload.get("build_status") or payload.get("status")
        if status != "failed":
            return None
        project = payload.get("project") or {}
        pipeline_obj = (
            payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
        ) or {}
        pipeline_id = payload.get("pipeline_id") or pipeline_obj.get("id")
        build_url = payload.get("build_url") or payload.get("url")
        return FailureEvent(
            tenant_id=tenant_id,
            ci_connection_id=ci_connection_id,
            provider="gitlab",
            source="gitlab_webhook",
            ci_run_id=str(pipeline_id or payload.get("build_id") or ""),
            ci_job_id=str(payload.get("build_id") or payload.get("id") or ""),
            project_id=str(payload.get("project_id") or project.get("id") or ""),
            project_path=project.get("path_with_namespace"),
            project_web_url=project.get("web_url"),
            pipeline_url=_pipeline_url_for(
                project, pipeline_id, pipeline_obj.get("web_url")
            )
            or (build_url or ""),
            job_url=build_url,
            status=status,
            ref=payload.get("ref"),
            sha=payload.get("sha") or payload.get("commit", {}).get("sha"),
            actor=(payload.get("user") or {}).get("username"),
            raw=payload,
        )


def _pipeline_url_for(
    project: dict[str, Any] | None,
    pipeline_id: Any,
    explicit_url: str | None = None,
) -> str:
    """Return a public pipeline URL, preferring an explicit one when given."""

    if explicit_url:
        return str(explicit_url)
    project = project or {}
    web = (project.get("web_url") or "").rstrip("/")
    if not web or pipeline_id is None:
        return ""
    return f"{web}/-/pipelines/{pipeline_id}"
