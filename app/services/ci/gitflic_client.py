"""GitFlic CI client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def _is_retryable_http(exc: BaseException) -> bool:
    """Retry only on transport errors and 5xx — NEVER on 4xx."""
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))

from app.core.config import get_settings
from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.models.ci_connection import CIConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.ci.base import CIProviderClient, FeedbackPublisher

log = get_logger(__name__)
_settings = get_settings()


def _bearer_token(conn: CIConnection) -> str | None:
    if conn.oauth_access_token_enc:
        return decrypt_str(conn.oauth_access_token_enc)
    if conn.api_token_enc:
        return decrypt_str(conn.api_token_enc)
    return None


def _auth_headers(conn: CIConnection) -> dict[str, str]:
    token = _bearer_token(conn)
    if not token:
        return {}
    return {"Authorization": f"token {token}"}


def _api_base_for(connection: CIConnection) -> str:
    """Resolve the REST base URL for a connection."""
    extra_override = (connection.extra or {}).get("api_base_url")
    if extra_override:
        return str(extra_override).rstrip("/")
    base = (connection.base_url or _settings.gitflic_base_url).rstrip("/")
    if base == _settings.gitflic_base_url.rstrip("/"):
        return _settings.gitflic_api_base_url.rstrip("/")
    return f"{base}/rest-api"


class GitFlicClient(CIProviderClient, FeedbackPublisher):
    """GitFlic API client."""

    def __init__(self, connection: CIConnection, timeout: float = 15.0) -> None:
        self.connection = connection
        self.api_base = _api_base_for(connection)
        self._client = httpx.AsyncClient(
            base_url=self.api_base,
            timeout=timeout,
            headers=_auth_headers(connection),
        )

    async def __aenter__(self) -> GitFlicClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception(_is_retryable_http),
    )
    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception(_is_retryable_http),
    )
    async def _post(self, path: str, json: Any = None) -> httpx.Response:
        resp = await self._client.post(path, json=json)
        resp.raise_for_status()
        return resp

    async def _delete(self, path: str) -> httpx.Response:
        return await self._client.delete(path)

    @staticmethod
    def _split_project_path(project_path: str | None) -> tuple[str, str] | None:
        if not project_path or "/" not in project_path:
            return None
        owner, _, alias = project_path.partition("/")
        owner = owner.strip("/")
        alias = alias.strip("/")
        if not owner or not alias:
            return None
        return owner, alias

    def _project_root(self, project_path: str) -> str:
        return f"/project/{project_path}"

    async def fetch_job_log(self, event: FailureEvent) -> str:
        """Fetch the failing job's primary ``.log`` artifact."""
        project_path = event.project_path or self.connection.external_project_name
        owner_alias = self._split_project_path(project_path)
        if owner_alias is None:
            return ""
        pipeline_local = event.ci_run_id
        if not event.ci_job_id:
            jobs = await self.list_pipeline_jobs(project_path, pipeline_local)
            failed = next(
                (j for j in jobs if str(j.get("status", "")).upper() == "FAILED"),
                None,
            )
            if not failed:
                return ""
            event.ci_job_id = str(failed.get("localId") or failed.get("id"))
        root = self._project_root(project_path)
        # Artifact list for the job.
        try:
            resp = await self._get(
                f"{root}/cicd/job/{event.ci_job_id}/artifacts"
            )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "gitflic.fetch_log.artifacts_failed",
                project=project_path,
                job=event.ci_job_id,
                status=exc.response.status_code,
            )
            return ""
        items = (
            (resp.json() or {})
            .get("_embedded", {})
            .get("restPipelineJobArtifactModelList", [])
        )
        artifact = next(
            (a for a in items if str(a.get("fileName", "")).endswith(".log")),
            None,
        ) or (items[0] if items else None)
        if not artifact:
            return ""
        artifact_uuid = artifact.get("id")
        if not artifact_uuid:
            return ""
        try:
            dl = await self._get(
                f"{root}/cicd/job/{event.ci_job_id}/artifact/{artifact_uuid}/download"
            )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "gitflic.fetch_log.download_failed",
                project=project_path,
                job=event.ci_job_id,
                status=exc.response.status_code,
            )
            return ""
        return dl.text

    async def list_pipeline_jobs(
        self, project_path: str, pipeline_local_id: str
    ) -> list[dict]:
        root = self._project_root(project_path)
        resp = await self._get(
            f"{root}/cicd/pipeline/{pipeline_local_id}/jobs",
            params={"size": "100"},
        )
        return (
            (resp.json() or {})
            .get("_embedded", {})
            .get("restPipelineJobModelList", [])
        )

    async def list_recent_failed_runs(
        self, since_run_id: str | None, limit: int
    ) -> list[FailureEvent]:
        project_path = self.connection.external_project_name
        if not project_path:
            return []
        root = self._project_root(project_path)
        try:
            resp = await self._get(
                f"{root}/cicd/pipeline",
                params={"size": str(max(1, min(limit, 100)))},
            )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "gitflic.poll.pipeline_failed",
                status=exc.response.status_code,
                project=project_path,
            )
            return []
        items = (
            (resp.json() or {})
            .get("_embedded", {})
            .get("restPipelineModelList", [])
        )
        cutoff = None
        if since_run_id and since_run_id.isdigit():
            cutoff = int(since_run_id)
        events: list[FailureEvent] = []
        web_root = (self.connection.external_project_url or "").rstrip("/")
        for p in items:
            if str(p.get("status", "")).upper() != "FAILED":
                continue
            local_id = p.get("localId")
            if local_id is None:
                continue
            if cutoff is not None and int(local_id) <= cutoff:
                continue
            pipeline_web = f"{web_root}/ci-cd/pipeline/{local_id}" if web_root else None
            events.append(
                FailureEvent(
                    tenant_id=self.connection.tenant_id,
                    ci_connection_id=self.connection.id,
                    provider="gitflic",
                    source="gitflic_poll",
                    ci_run_id=str(local_id),
                    project_id=self.connection.external_project_id,
                    project_path=project_path,
                    project_web_url=self.connection.external_project_url,
                    pipeline_url=pipeline_web,
                    status="failed",
                    ref=p.get("ref"),
                    sha=p.get("commitId"),
                    occurred_at=_parse_iso(
                        p.get("finishedAt") or p.get("createdAt")
                    ),
                    raw=p,
                )
            )
        return events

    async def list_projects(
        self, *, search: str | None = None, per_page: int = 50
    ) -> list[dict]:
        """Return projects the authenticated user owns."""
        params: dict[str, Any] = {"size": max(1, min(per_page, 100))}
        if search:
            params["q"] = search
        try:
            resp = await self._get("/project/my", params=params)
        except httpx.HTTPStatusError as exc:
            log.warning(
                "gitflic.list_projects.failed",
                status=exc.response.status_code,
            )
            return []
        return (resp.json() or {}).get("_embedded", {}).get("projectList", [])

    async def get_project(self, project_path: str) -> dict | None:
        try:
            resp = await self._get(f"/project/{project_path}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return resp.json()

    async def register_webhook(
        self,
        project_path: str,
        *,
        url: str,
        secret: str,
    ) -> dict:
        root = self._project_root(project_path)
        payload = {
            "url": url,
            "secret": secret,
            "events": {
                "PIPELINE_FAIL": True,
                "PIPELINE_NEW": False,
                "PIPELINE_SUCCESS": False,
                "MERGE_REQUEST_CREATE": False,
                "MERGE_REQUEST_UPDATE": False,
                "WEBHOOK_SEND": False,
            },
        }
        resp = await self._post(f"{root}/setting/webhook", json=payload)
        return resp.json()

    async def list_webhooks(self, project_path: str) -> list[dict]:
        root = self._project_root(project_path)
        try:
            resp = await self._get(f"{root}/setting/webhook")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 404):
                return []
            raise
        return (resp.json() or {}).get("_embedded", {}).get("webhookList", [])

    async def delete_webhook(self, project_path: str, hook_id: str) -> bool:
        root = self._project_root(project_path)
        # GitFlic uses POST .../delete (not DELETE verb).
        resp = await self._client.post(f"{root}/setting/webhook/{hook_id}/delete")
        if resp.status_code in (200, 204, 404):
            return True
        resp.raise_for_status()
        return False

    async def publish(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        policy: dict[str, bool] | None = None,
        analysis_id: str | None = None,
    ) -> dict | None:
        log.info(
            "gitflic.publish_skipped",
            reason="mvp_no_git_host_feedback",
            run_id=event.ci_run_id,
        )
        return None


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)
