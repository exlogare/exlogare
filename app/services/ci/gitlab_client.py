from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

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
    if conn.oauth_access_token_enc:
        return {"Authorization": f"Bearer {token}"}
    return {"PRIVATE-TOKEN": token}


class GitLabClient(CIProviderClient, FeedbackPublisher):
    """GitLab API client: implements both CI log fetching and MR feedback posting."""

    def __init__(self, connection: CIConnection, timeout: float = 15.0) -> None:
        self.connection = connection
        self.base_url = (connection.base_url or _settings.gitlab_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=_auth_headers(connection),
        )

    async def __aenter__(self) -> GitLabClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _post(self, path: str, data: dict | None = None, json: Any = None) -> httpx.Response:
        resp = await self._client.post(path, data=data, json=json)
        resp.raise_for_status()
        return resp

    async def _delete(self, path: str) -> httpx.Response:
        return await self._client.delete(path)


    async def fetch_job_log(self, event: FailureEvent) -> str:
        if not event.ci_job_id:
            jobs = await self.list_pipeline_jobs(event.project_id or "", event.ci_run_id)
            failed = next((j for j in jobs if j.get("status") == "failed"), None)
            if not failed:
                return ""
            event.ci_job_id = str(failed["id"])
        path = f"/api/v4/projects/{event.project_id}/jobs/{event.ci_job_id}/trace"
        resp = await self._get(path)
        return resp.text

    async def list_pipeline_jobs(self, project_id: str, pipeline_id: str) -> list[dict]:
        path = f"/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs"
        resp = await self._get(path, params={"per_page": 100})
        return resp.json()

    async def get_latest_job_id(self) -> str | None:
        """Return the id of the most recent job in the project, or None if none."""
        project_id = self.connection.external_project_id
        if not project_id:
            return None
        resp = await self._get(
            f"/api/v4/projects/{project_id}/jobs",
            params={"per_page": "1", "order_by": "id", "sort": "desc"},
        )
        items = resp.json() or []
        if not items:
            return None
        jid = items[0].get("id")
        return str(jid) if jid is not None else None

    async def list_recent_failed_jobs(
        self, since_job_id: str | None, limit: int
    ) -> list[FailureEvent]:
        """Poll failed jobs directly (not pipelines)."""
        project_id = self.connection.external_project_id
        if not project_id:
            return []
        path = f"/api/v4/projects/{project_id}/jobs"
        params: list[tuple[str, str]] = [
            ("scope[]", "failed"),
            ("per_page", str(max(1, min(limit, 100)))),
            ("order_by", "id"),
            ("sort", "desc"),
        ]
        resp = await self._get(path, params=params)
        items = resp.json() or []
        cutoff = int(since_job_id) if since_job_id and since_job_id.isdigit() else None

        events: list[FailureEvent] = []
        for j in items:
            jid_raw = j.get("id")
            if jid_raw is None:
                continue
            jid = int(jid_raw)
            if cutoff is not None and jid <= cutoff:
                continue
            pipeline = j.get("pipeline") or {}
            commit = j.get("commit") or {}
            user = j.get("user") or {}
            pid = pipeline.get("id")
            pipeline_web = pipeline.get("web_url")
            job_web = j.get("web_url")
            events.append(
                FailureEvent(
                    tenant_id=self.connection.tenant_id,
                    ci_connection_id=self.connection.id,
                    provider="gitlab",
                    source="gitlab_poll",
                    ci_run_id=str(pid) if pid is not None else str(jid),
                    ci_job_id=str(jid),
                    project_id=str(project_id),
                    project_path=self.connection.external_project_name,
                    project_web_url=self.connection.external_project_url,
                    pipeline_url=pipeline_web or job_web,
                    job_url=job_web,
                    status=j.get("status", "failed"),
                    ref=j.get("ref") or pipeline.get("ref"),
                    sha=commit.get("id") or pipeline.get("sha"),
                    actor=user.get("username"),
                    occurred_at=_parse_iso(
                        j.get("finished_at") or j.get("started_at") or j.get("created_at")
                    ),
                    raw=j,
                )
            )
        return events

    async def list_recent_failed_runs(
        self, since_run_id: str | None, limit: int
    ) -> list[FailureEvent]:
        """DEPRECATED: kept for backward compatibility with callers expecting"""
        return await self.list_recent_failed_jobs(since_job_id=since_run_id, limit=limit)


    async def list_projects(
        self, *, membership: bool = True, search: str | None = None, per_page: int = 50
    ) -> list[dict]:
        params: dict[str, Any] = {
            "per_page": max(1, min(per_page, 100)),
            "order_by": "last_activity_at",
            "sort": "desc",
        }
        if membership:
            params["membership"] = "true"
        if search:
            params["search"] = search
        resp = await self._get("/api/v4/projects", params=params)
        return resp.json() or []

    async def get_project(self, project_id_or_path: str) -> dict | None:
        from urllib.parse import quote

        encoded = quote(str(project_id_or_path), safe="")
        try:
            resp = await self._get(f"/api/v4/projects/{encoded}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return resp.json()

    async def register_webhook(
        self,
        project_id: str,
        *,
        url: str,
        token: str,
    ) -> dict:
        for hook in await self.list_webhooks(project_id):
            if hook.get("url") == url:
                return hook

        payload = {
            "url": url,
            "token": token,
            "pipeline_events": True,
            "job_events": True,
            "push_events": False,
            "merge_requests_events": False,
            "enable_ssl_verification": url.lower().startswith("https://"),
        }
        resp = await self._post(
            f"/api/v4/projects/{project_id}/hooks",
            json=payload,
        )
        if resp.status_code >= 400:
            detail = resp.text[:500]
            log.warning(
                "gitlab.webhook_register_http_error",
                status=resp.status_code,
                detail=detail,
                url=url,
            )
            resp.raise_for_status()
        return resp.json()

    async def list_webhooks(self, project_id: str) -> list[dict]:
        """Return all project webhooks."""
        resp = await self._get(f"/api/v4/projects/{project_id}/hooks")
        return resp.json() or []

    async def delete_webhook(self, project_id: str, hook_id: str) -> bool:
        """Delete a project webhook and report whether GitLab actually"""
        resp = await self._delete(f"/api/v4/projects/{project_id}/hooks/{hook_id}")
        if resp.status_code == 404:
            return True
        if 200 <= resp.status_code < 300:
            return True
        resp.raise_for_status()
        return False

    async def list_pipeline_merge_requests(
        self, project_id: str, pipeline_id: str
    ) -> list[dict]:
        """Return the MRs associated with a pipeline (usually 0 or 1)."""
        path = f"/api/v4/projects/{project_id}/pipelines/{pipeline_id}/merge_requests"
        try:
            resp = await self._get(path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (403, 404):
                return []
            raise
        return resp.json() or []

    async def create_issue(
        self,
        project_id: str,
        *,
        title: str,
        description: str,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a new GitLab issue."""
        payload: dict[str, Any] = {"title": title, "description": description}
        if labels:
            payload["labels"] = ",".join(labels)
        resp = await self._post(f"/api/v4/projects/{project_id}/issues", json=payload)
        return resp.json()


    async def publish(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        policy: dict[str, bool] | None = None,
        analysis_id: str | None = None,
    ) -> dict | None:
        """Cascade: MR note -> commit comment -> Issue."""

        from app.services.ci.feedback_policy import resolve_feedback_policy

        if policy is None:
            policy = resolve_feedback_policy(None, None)

        body = render_gitlab_comment(event, analysis)

        if policy.get("mr_comment") and event.mr_iid and event.project_id:
            path = (
                f"/api/v4/projects/{event.project_id}"
                f"/merge_requests/{event.mr_iid}/notes"
            )
            resp = await self._post(path, json={"body": body})
            data = resp.json() or {}
            note_id = data.get("id")
            note_url = self._merge_request_note_url(event, note_id)
            return {
                "channel": "mr",
                "ref": f"mr_note:{note_id}" if note_id else "mr_note",
                "url": note_url,
            }

        if policy.get("commit_comment") and event.project_id and event.sha:
            path = (
                f"/api/v4/projects/{event.project_id}"
                f"/repository/commits/{event.sha}/comments"
            )
            resp = await self._post(path, data={"note": body})
            data = resp.json() or {}
            return {
                "channel": "commit",
                "ref": f"commit_comment:{event.sha}",
                "url": (
                    f"{event.project_web_url.rstrip('/')}/-/commit/{event.sha}"
                    if event.project_web_url
                    else None
                ),
            }

        if policy.get("issue") and event.project_id:
            title = (
                f"CI failure in {event.project_path or event.project_id}: "
                f"{event.ci_run_id}"
            )
            issue = await self.create_issue(
                event.project_id,
                title=title,
                description=self._issue_description(event, body),
                labels=[
                    "exlogare",
                    "ci-failure",
                    f"severity/{analysis.severity}",
                ],
            )
            iid = issue.get("iid")
            return {
                "channel": "issue",
                "ref": f"issue:{iid}" if iid else "issue",
                "url": issue.get("web_url"),
            }

        log.info(
            "gitlab.publish_skipped",
            reason="no_channel_available_or_allowed",
            run_id=event.ci_run_id,
            policy=policy,
            has_mr=bool(event.mr_iid),
            has_sha=bool(event.sha),
        )
        return None

    def _merge_request_note_url(
        self, event: FailureEvent, note_id: Any
    ) -> str | None:
        """Deep link to an MR note; GitLab's deterministic anchor pattern."""
        if not event.project_web_url or not event.mr_iid:
            return None
        base = event.project_web_url.rstrip("/")
        if note_id is None:
            return f"{base}/-/merge_requests/{event.mr_iid}"
        return f"{base}/-/merge_requests/{event.mr_iid}#note_{note_id}"

    @staticmethod
    def _issue_description(event: FailureEvent, body: str) -> str:
        """Augment the rendered RCA with a back-reference to the pipeline."""
        parts = [body]
        if event.pipeline_url:
            parts.append(f"\n\n---\nPipeline: {event.pipeline_url}")
        if event.ref:
            parts.append(f"\nBranch: `{event.ref}`")
        if event.sha:
            parts.append(f"\nCommit: `{event.sha[:10]}`")
        return "".join(parts)


def render_gitlab_comment(event: FailureEvent, analysis: AnalysisOutput) -> str:
    header = "### Exlogare AI - CI Failure Analysis"
    severity_badge = f"**Severity:** `{analysis.severity.upper()}`"
    confidence_badge = f"**Confidence:** `{analysis.confidence:.2f}`"
    link = f"[Open pipeline]({event.pipeline_url})" if event.pipeline_url else ""
    needs = ""
    if analysis.needs_more_context:
        needs = f"\n\n_More context needed:_ {analysis.missing_context_hint or 'insufficient data'}"
    return (
        f"{header}\n\n"
        f"{severity_badge} | {confidence_badge} {link}\n\n"
        f"**Root cause:** {analysis.root_cause}\n\n"
        f"**Explanation:**\n{analysis.explanation}\n\n"
        f"**Fix suggestion:**\n{analysis.fix_suggestion}"
        f"{needs}"
    )


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)
