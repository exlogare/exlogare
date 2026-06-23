from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.models.ci_connection import CIConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.ci.base import CIProviderClient, FeedbackPublisher
from app.services.ci.gitlab_client import render_gitlab_comment
from app.services.ci.status_check import (
    github_conclusion,
    status_check_context,
    status_check_details_url,
    status_check_long_summary,
    status_check_summary,
    status_check_title,
)

log = get_logger(__name__)
_settings = get_settings()


def _github_api_base(conn: CIConnection) -> str:
    s = get_settings()
    u = (conn.base_url or s.github_base_url or "https://github.com").rstrip("/").lower()
    if u in ("https://github.com", "http://github.com"):
        return s.github_api_base_url.rstrip("/")
    return f"{(conn.base_url or s.github_base_url).rstrip('/')}/api/v3"


def _bearer_token(conn: CIConnection) -> str | None:
    if conn.oauth_access_token_enc:
        return decrypt_str(conn.oauth_access_token_enc)
    if conn.api_token_enc:
        return decrypt_str(conn.api_token_enc)
    return None


def _owner_repo(conn: CIConnection) -> tuple[str, str]:
    name = (conn.external_project_name or "").strip()
    if "/" in name:
        a, b = name.split("/", 1)
        return a.strip(), b.strip()
    extra = conn.extra or {}
    slug = (extra.get("full_name") or extra.get("repo_full_name") or "") if isinstance(extra, dict) else ""
    if isinstance(slug, str) and "/" in slug:
        a, b = slug.split("/", 1)
        return a.strip(), b.strip()
    raise ValueError("GitHub connection has no owner/repo in external_project_name")


class GitHubClient(CIProviderClient, FeedbackPublisher):
    def __init__(self, connection: CIConnection, timeout: float = 30.0) -> None:
        self.connection = connection
        self._api = _github_api_base(connection)
        self._client = httpx.AsyncClient(
            base_url=self._api,
            timeout=timeout,
            headers=_auth_headers(connection),
            follow_redirects=True,
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _post(
        self, path: str, json: dict | None = None, data: dict | None = None
    ) -> httpx.Response:
        resp = await self._client.post(path, json=json, data=data)
        resp.raise_for_status()
        return resp

    async def _delete(self, path: str) -> httpx.Response:
        return await self._client.delete(path)

    @staticmethod
    def _owner_repo_for_event(conn: CIConnection, event: FailureEvent) -> tuple[str, str]:
        path = (event.project_path or conn.external_project_name or "").strip()
        if "/" in path:
            a, b = path.split("/", 1)
            return a.strip(), b.strip()
        return _owner_repo(conn)

    async def fetch_job_log(self, event: FailureEvent) -> str:
        owner, repo = self._owner_repo_for_event(self.connection, event)
        run_id = event.ci_run_id
        if not event.ci_job_id and run_id:
            r = await self._get(
                f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
                params={"per_page": 100},
            )
            jobs = (r.json() or {}).get("jobs") or []
            failed = next(
                (j for j in jobs if j.get("conclusion") == "failure"), None
            ) or (jobs[0] if jobs else None)
            if failed and failed.get("id") is not None:
                event.ci_job_id = str(failed["id"])
        if not event.ci_job_id:
            return ""
        jid = event.ci_job_id
        # logs endpoint
        r2 = await self._client.get(f"/repos/{owner}/{repo}/actions/jobs/{jid}/logs")
        if r2.status_code >= 400:
            r2.raise_for_status()
        return r2.text

    async def get_latest_workflow_run_id(self) -> str | None:
        """Latest run id (any status) to prime the poller cursor."""
        try:
            owner, repo = _owner_repo(self.connection)
        except ValueError:
            return None
        resp = await self._get(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"per_page": 1},
        )
        wfs = (resp.json() or {}).get("workflow_runs") or []
        if not wfs:
            return None
        wid = wfs[0].get("id")
        return str(wid) if wid is not None else None

    async def list_recent_failed_runs(
        self, since_run_id: str | None, limit: int
    ) -> list[FailureEvent]:
        try:
            owner, repo = _owner_repo(self.connection)
        except ValueError:
            return []
        r = await self._get(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"status": "failure", "per_page": min(limit, 30)},
        )
        runs: list[dict] = (r.json() or {}).get("workflow_runs") or []
        out: list[FailureEvent] = []
        since_n = int(since_run_id) if since_run_id and since_run_id.isdigit() else 0
        for run in runs:
            rid = run.get("id")
            if rid is None:
                continue
            n = int(rid)
            if since_n and n <= since_n:
                continue
            repo_d = run.get("repository") or {}
            prs = run.get("pull_requests") or []
            mr: str | None = None
            if prs and prs[0].get("number") is not None:
                mr = str(prs[0]["number"])
            head = (run.get("head_commit") or {}) if isinstance(run.get("head_commit"), dict) else {}
            sha = run.get("head_sha") or head.get("id")
            out.append(
                FailureEvent(
                    tenant_id=self.connection.tenant_id,
                    ci_connection_id=self.connection.id,
                    provider="github",
                    source="github_poll",
                    ci_run_id=str(rid),
                    ci_job_id="",
                    project_id=str(
                        self.connection.external_project_id
                        or repo_d.get("id")
                        or ""
                    ),
                    project_path=repo_d.get("full_name") or f"{owner}/{repo}",
                    project_web_url=repo_d.get("html_url")
                    or f"{_settings.github_base_url.rstrip('/')}/{owner}/{repo}",
                    pipeline_url=run.get("html_url") or run.get("url") or "",
                    ref=run.get("head_branch") or run.get("display_title"),
                    sha=sha,
                    status="failed",
                    mr_iid=mr,
                    raw=run,
                )
            )
            if len(out) >= limit:
                break
        return out

    async def list_repos(
        self, search: str | None = None, per_page: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "per_page": per_page,
            "affiliation": "owner,collaborator,organization_member",
            "sort": "pushed",
        }
        if search:
            q = f"{search} in:name user:@me"
            r = await self._get("/search/repositories", params={"q": q, "per_page": per_page})
            return (r.json() or {}).get("items") or []
        r2 = await self._get("/user/repos", params=params)
        return r2.json() or []

    async def get_repository_by_id(self, repo_id: str) -> dict | None:
        try:
            r = await self._get(f"/repositories/{repo_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return r.json()

    async def get_repo(self, full_name: str) -> dict | None:
        enc = quote(full_name, safe="")
        try:
            r = await self._get(f"/repos/{enc}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return r.json()

    async def register_webhook(self, full_name: str, *, url: str, token: str) -> dict:
        a, b = full_name.split("/", 1) if "/" in full_name else ("", "")
        payload = {
            "name": "web",
            "active": True,
            "events": ["workflow_run"],
            "config": {
                "url": url,
                "content_type": "json",
                "secret": token,
                "insecure_ssl": "0",
            },
        }
        r = await self._post(f"/repos/{quote(a, safe='')}/{quote(b, safe='')}/hooks", json=payload)
        return r.json() or {}

    async def list_webhooks(self, full_name: str) -> list[dict[str, Any]]:
        a, b = full_name.split("/", 1)
        r = await self._get(
            f"/repos/{quote(a, safe='')}/{quote(b, safe='')}/hooks",
        )
        return r.json() or []

    async def delete_webhook(self, full_name: str, hook_id: str) -> bool:
        a, b = full_name.split("/", 1)
        p = f"/repos/{quote(a, safe='')}/{quote(b, safe='')}/hooks/{hook_id}"
        r = await self._delete(p)
        if r.status_code == 404:
            return True
        r.raise_for_status()
        return 200 <= r.status_code < 300

    def _rca_body(self, event: FailureEvent, analysis: AnalysisOutput) -> str:
        return render_gitlab_comment(event, analysis)

    async def _post_check_run(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        owner: str,
        repo: str,
        analysis_id: str | None,
    ) -> dict | None:
        """Best-effort GitHub Check Run for the failing commit."""

        if not event.sha:
            return None
        details_url = status_check_details_url(
            analysis_id, fallback=event.pipeline_url or None
        )
        payload: dict[str, Any] = {
            "name": status_check_context(),
            "head_sha": event.sha,
            "status": "completed",
            "conclusion": github_conclusion(analysis),
            "output": {
                "title": status_check_title(analysis),
                "summary": status_check_long_summary(analysis),
            },
        }
        if details_url:
            payload["details_url"] = details_url
        try:
            r = await self._post(
                f"/repos/{owner}/{repo}/check-runs", json=payload
            )
        except httpx.HTTPStatusError as exc:
            log.info(
                "github.check_run_skipped",
                status_code=exc.response.status_code,
                sha=event.sha,
            )
            return None
        except Exception as exc:
            log.warning(
                "github.check_run_failed",
                error=str(exc),
                sha=event.sha,
            )
            return None
        body = r.json() or {}
        return {
            "ref": f"gh_check_run:{body.get('id')}",
            "url": body.get("html_url"),
        }

    async def publish(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        policy: dict[str, bool] | None = None,
        analysis_id: str | None = None,
    ) -> dict | None:
        from app.services.ci.feedback_policy import resolve_feedback_policy

        if policy is None:
            policy = resolve_feedback_policy(None, None)
        owner, repo = self._owner_repo_for_event(self.connection, event)
        body = self._rca_body(event, analysis)

        check_run_result: dict | None = None
        if policy.get("status_check") and event.sha:
            check_run_result = await self._post_check_run(
                event,
                analysis,
                owner=owner,
                repo=repo,
                analysis_id=analysis_id,
            )

        def _attach(result: dict) -> dict:
            if check_run_result:
                return {**result, "status_check": check_run_result}
            return result

        if (
            policy.get("mr_comment")
            and event.mr_iid
        ):
            num = int(event.mr_iid) if str(event.mr_iid).isdigit() else None
            if num is not None:
                r = await self._post(
                    f"/repos/{owner}/{repo}/issues/{num}/comments",
                    json={"body": body},
                )
                c = r.json() or {}
                return _attach({
                    "channel": "mr",
                    "ref": f"pr_comment:{c.get('id')}",
                    "url": c.get("html_url"),
                })

        if policy.get("commit_comment") and event.sha:
            r2 = await self._post(
                f"/repos/{owner}/{repo}/commits/{event.sha}/comments",
                json={"body": body},
            )
            c2 = r2.json() or {}
            return _attach({
                "channel": "commit",
                "ref": f"gh_commit:{c2.get('id')}",
                "url": c2.get("html_url"),
            })

        if policy.get("issue"):
            title = f"CI failure: {event.project_path or repo} - run {event.ci_run_id}"
            iss = f"{body}\n\n"
            if event.pipeline_url:
                iss += f"Run: {event.pipeline_url}\n"
            r3 = await self._post(
                f"/repos/{owner}/{repo}/issues",
                json={"title": title, "body": iss},
            )
            c3 = r3.json() or {}
            return _attach({
                "channel": "issue",
                "ref": f"gh_issue:{c3.get('number')}",
                "url": c3.get("html_url"),
            })

        if check_run_result:
            return {
                "channel": "status_check",
                "ref": check_run_result.get("ref", ""),
                "url": check_run_result.get("url"),
                "status_check": check_run_result,
            }
        log.info(
            "github.publish_skipped",
            run_id=event.ci_run_id,
        )
        return None


def _auth_headers(conn: CIConnection) -> dict[str, str]:
    t = _bearer_token(conn)
    if not t:
        return {}
    return {
        "Authorization": f"Bearer {t}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


