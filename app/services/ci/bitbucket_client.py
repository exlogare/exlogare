"""Bitbucket CI client (Cloud + Data Center)."""

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
    bitbucket_state,
    status_check_context,
    status_check_details_url,
    status_check_summary,
    status_check_title,
)
from app.services.oauth.bitbucket import is_bitbucket_cloud

log = get_logger(__name__)


def _cloud_api_base() -> str:
    return get_settings().bitbucket_api_base_url.rstrip("/")


def _dc_api_base(conn: CIConnection) -> str:
    """Bitbucket Data Center exposes everything under ``<base_url>``."""
    base = (conn.base_url or "").rstrip("/")
    return base


def _bearer_token(conn: CIConnection) -> str | None:
    if conn.oauth_access_token_enc:
        return decrypt_str(conn.oauth_access_token_enc)
    if conn.api_token_enc:
        return decrypt_str(conn.api_token_enc)
    return None


def _split_workspace_repo(value: str) -> tuple[str, str]:
    """Split ``workspace/repo_slug`` (Cloud) or ``PROJECT/repo`` (DC)."""
    name = (value or "").strip()
    if "/" in name:
        a, b = name.split("/", 1)
        return a.strip(), b.strip()
    raise ValueError(
        f"Bitbucket connection has no workspace/repo in identifier: {value!r}"
    )


def _cloud_repo_path_segment(repo_slug: str, repo_uuid: str | None) -> str:
    """Return the URL path segment to use for ``{repo_slug}`` on Cloud."""
    if repo_uuid:
        u = repo_uuid.strip()
        if not (u.startswith("{") and u.endswith("}")):
            u = "{" + u.strip("{}") + "}"
        return quote(u, safe="")
    return quote(repo_slug, safe="")


def _conn_workspace_repo(conn: CIConnection) -> tuple[str, str]:
    name = (conn.external_project_name or "").strip()
    if "/" in name:
        a, b = name.split("/", 1)
        return a.strip(), b.strip()
    extra = conn.extra or {}
    if isinstance(extra, dict):
        ws = extra.get("workspace") or extra.get("project_key") or ""
        slug = extra.get("repo_slug") or ""
        if ws and slug:
            return str(ws).strip(), str(slug).strip()
        full = extra.get("full_name") or extra.get("repo_full_name") or ""
        if isinstance(full, str) and "/" in full:
            a, b = full.split("/", 1)
            return a.strip(), b.strip()
    raise ValueError("Bitbucket connection has no workspace/repo identifier")


class BitbucketClient(CIProviderClient, FeedbackPublisher):
    """Unified Cloud + DC Bitbucket client."""

    def __init__(self, connection: CIConnection, timeout: float = 30.0) -> None:
        self.connection = connection
        self._is_cloud = is_bitbucket_cloud(connection.base_url)
        self._api_base = _cloud_api_base() if self._is_cloud else _dc_api_base(connection)
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            timeout=timeout,
            headers=_auth_headers(connection),
            follow_redirects=True,
        )

    @property
    def is_cloud(self) -> bool:
        return self._is_cloud

    async def __aenter__(self) -> BitbucketClient:
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

    async def fetch_job_log(self, event: FailureEvent) -> str:
        if not self._is_cloud:
            return ""
        return await self._cloud_fetch_pipeline_log(event)

    async def list_recent_failed_runs(
        self, since_run_id: str | None, limit: int
    ) -> list[FailureEvent]:
        if not self._is_cloud:
            return []
        return await self._cloud_list_recent_failed_pipelines(since_run_id, limit)

    async def list_workspaces(self) -> list[dict[str, Any]]:
        """Return workspaces visible to the OAuth user."""
        if not self._is_cloud:
            return []
        r = await self._get("/user/workspaces", params={"pagelen": 100})
        items = (r.json() or {}).get("values") or []
        workspaces: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            ws = entry.get("workspace")
            if isinstance(ws, dict) and (ws.get("slug") or ws.get("name")):
                workspaces.append(ws)
        return workspaces

    async def list_repos(
        self,
        *,
        workspace: str | None = None,
        search: str | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List repositories visible to the OAuth user."""
        if not self._is_cloud:
            return []
        pagelen = max(1, min(per_page, 100))
        base_params: dict[str, Any] = {"pagelen": pagelen, "sort": "-updated_on"}
        if search:
            base_params["q"] = f'name ~ "{search}"'

        if workspace:
            params = {**base_params, "role": "member"}
            r = await self._get(
                f"/repositories/{quote(workspace, safe='')}", params=params
            )
            return (r.json() or {}).get("values") or []

        workspaces = await self.list_workspaces()
        aggregated: list[dict[str, Any]] = []
        for ws in workspaces:
            slug = ws.get("slug") or ws.get("name") or ""
            if not slug:
                continue
            try:
                params = {**base_params, "role": "member"}
                r = await self._get(
                    f"/repositories/{quote(str(slug), safe='')}", params=params
                )
            except httpx.HTTPStatusError as exc:
                log.warning(
                    "bitbucket.list_repos_workspace_failed",
                    workspace=str(slug),
                    status=exc.response.status_code if exc.response else None,
                )
                continue
            aggregated.extend((r.json() or {}).get("values") or [])
            if len(aggregated) >= per_page:
                break
        return aggregated[:per_page]

    def _connection_repo_uuid(self) -> str | None:
        """Return the repo UUID we cached during ``/watch`` (with braces)."""
        extra = self.connection.extra if isinstance(self.connection.extra, dict) else None
        if extra:
            cached = extra.get("uuid")
            if isinstance(cached, str) and cached.strip():
                u = cached.strip()
                return u if (u.startswith("{") and u.endswith("}")) else "{" + u.strip("{}") + "}"
        epid = (self.connection.external_project_id or "").strip()
        if epid.startswith("{") and epid.endswith("}"):
            return epid
        return None

    async def _cloud_resolve_pipeline_uuid(
        self,
        ws: str,
        slug: str,
        run_id: str,
        *,
        repo_uuid: str | None = None,
    ) -> str | None:
        """Normalise a webhook ``run_id`` to a canonical ``{abc-...}`` UUID."""
        rid = (run_id or "").strip()
        if not rid:
            return None
        if rid.count("-") >= 2:
            return rid if (rid.startswith("{") and rid.endswith("}")) else "{" + rid.strip("{}") + "}"

        repo_seg = _cloud_repo_path_segment(slug, repo_uuid)
        try:
            r = await self._get(
                f"/repositories/{quote(ws, safe='')}/{repo_seg}"
                f"/pipelines/{quote(rid, safe='')}"
            )
        except httpx.HTTPStatusError as exc:
            log.warning(
                "bitbucket.pipeline_resolve_failed",
                workspace=ws,
                repo=slug,
                run_id=rid,
                status=exc.response.status_code if exc.response else None,
            )
            return None
        payload = r.json() or {}
        u = payload.get("uuid")
        if not isinstance(u, str) or not u:
            return None
        u = u.strip()
        return u if (u.startswith("{") and u.endswith("}")) else "{" + u.strip("{}") + "}"

    async def _cloud_fetch_pipeline_log(self, event: FailureEvent) -> str:
        """Fetch the raw log text for the failing step of a Cloud pipeline."""
        ws, slug = self._owner_repo_for_event(event)
        if not event.ci_run_id:
            log.warning(
                "bitbucket.fetch_log_no_run_id",
                workspace=ws,
                repo=slug,
                connection_id=str(self.connection.id),
            )
            return ""

        repo_uuid = self._connection_repo_uuid()
        pipeline_uuid = await self._cloud_resolve_pipeline_uuid(
            ws, slug, event.ci_run_id, repo_uuid=repo_uuid
        )
        if not pipeline_uuid:
            log.warning(
                "bitbucket.fetch_log_no_pipeline_uuid",
                workspace=ws,
                repo=slug,
                run_id=event.ci_run_id,
            )
            return ""
        encoded_pid = quote(pipeline_uuid, safe="")
        repo_seg = _cloud_repo_path_segment(slug, repo_uuid)

        step_uuid = (event.ci_job_id or "").strip()
        if step_uuid and step_uuid.count("-") >= 2 and not (
            step_uuid.startswith("{") and step_uuid.endswith("}")
        ):
            step_uuid = "{" + step_uuid.strip("{}") + "}"

        if not step_uuid:
            try:
                r = await self._get(
                    f"/repositories/{quote(ws, safe='')}/{repo_seg}"
                    f"/pipelines/{encoded_pid}/steps"
                )
            except httpx.HTTPStatusError as exc:
                log.warning(
                    "bitbucket.list_steps_failed",
                    workspace=ws,
                    repo=slug,
                    pipeline=pipeline_uuid,
                    status=exc.response.status_code if exc.response else None,
                )
                return ""
            steps = (r.json() or {}).get("values") or []
            failed = next(
                (
                    s
                    for s in steps
                    if (s.get("state") or {}).get("result", {}).get("name")
                    in ("FAILED", "ERROR", "STOPPED")
                ),
                None,
            ) or (steps[-1] if steps else None)
            if failed and failed.get("uuid"):
                step_uuid = str(failed["uuid"])
                event.ci_job_id = step_uuid
        if not step_uuid:
            log.warning(
                "bitbucket.fetch_log_no_step_uuid",
                workspace=ws,
                repo=slug,
                pipeline=pipeline_uuid,
            )
            return ""

        encoded_sid = quote(step_uuid, safe="")
        path = (
            f"/repositories/{quote(ws, safe='')}/{repo_seg}"
            f"/pipelines/{encoded_pid}/steps/{encoded_sid}/log"
        )
        try:
            r2 = await self._client.get(
                path, headers={"Accept": "text/plain, */*;q=0.5"}
            )
        except httpx.HTTPError as exc:
            log.warning(
                "bitbucket.fetch_log_request_failed",
                workspace=ws,
                repo=slug,
                pipeline=pipeline_uuid,
                step=step_uuid,
                error=str(exc),
            )
            return ""
        if r2.status_code == 404:
            log.warning(
                "bitbucket.fetch_log_not_found",
                workspace=ws,
                repo=slug,
                pipeline=pipeline_uuid,
                step=step_uuid,
            )
            return ""
        if r2.status_code >= 400:
            log.warning(
                "bitbucket.fetch_log_failed",
                workspace=ws,
                repo=slug,
                pipeline=pipeline_uuid,
                step=step_uuid,
                status=r2.status_code,
            )
            return ""
        return r2.text or ""

    async def get_latest_pipeline_uuid(self) -> str | None:
        """Latest pipeline UUID (any state) to prime the poller cursor."""
        if not self._is_cloud:
            return None
        try:
            ws, slug = _conn_workspace_repo(self.connection)
        except ValueError:
            return None
        r = await self._get(
            f"/repositories/{quote(ws, safe='')}/{quote(slug, safe='')}/pipelines",
            params={"sort": "-created_on", "pagelen": 1},
        )
        vs = (r.json() or {}).get("values") or []
        if not vs:
            return None
        u = vs[0].get("uuid")
        return str(u) if u else None

    async def _cloud_list_recent_failed_pipelines(
        self, since_run_uuid: str | None, limit: int
    ) -> list[FailureEvent]:
        try:
            ws, slug = _conn_workspace_repo(self.connection)
        except ValueError:
            return []
        r = await self._get(
            f"/repositories/{quote(ws, safe='')}/{quote(slug, safe='')}/pipelines",
            params={"sort": "-created_on", "pagelen": min(limit, 30)},
        )
        runs: list[dict] = (r.json() or {}).get("values") or []
        out: list[FailureEvent] = []
        for run in runs:
            state = (run.get("state") or {}).get("result", {}).get("name")
            if state not in ("FAILED", "STOPPED", "ERROR"):
                continue
            uuid_ = run.get("uuid")
            if not uuid_:
                continue
            if since_run_uuid and str(uuid_) == str(since_run_uuid):
                break
            target = run.get("target") or {}
            commit = target.get("commit") or {}
            ref = (
                target.get("ref_name")
                or target.get("branch")
                or target.get("destination_branch")
            )
            sha = commit.get("hash")
            html = (run.get("links") or {}).get("html") or {}
            pipeline_url = html.get("href") or ""
            out.append(
                FailureEvent(
                    tenant_id=self.connection.tenant_id,
                    ci_connection_id=self.connection.id,
                    provider="bitbucket",
                    source="bitbucket_poll",
                    ci_run_id=str(uuid_),
                    ci_job_id="",
                    project_id=str(self.connection.external_project_id or ""),
                    project_path=f"{ws}/{slug}",
                    project_web_url=f"https://bitbucket.org/{ws}/{slug}",
                    pipeline_url=pipeline_url,
                    ref=ref,
                    sha=sha,
                    status="failed",
                    raw=run,
                )
            )
            if len(out) >= limit:
                break
        return out

    async def get_repo(self, workspace: str, repo_slug: str) -> dict[str, Any]:
        """Fetch a single Cloud repo by ``{workspace}/{repo_slug}``."""
        if not self._is_cloud:
            raise NotImplementedError("get_repo is Cloud-only")
        r = await self._get(
            f"/repositories/{quote(workspace, safe='')}/{quote(repo_slug, safe='')}"
        )
        return r.json() or {}

    async def register_webhook(
        self,
        workspace_or_project: str,
        repo_slug: str,
        *,
        url: str,
        secret: str,
        repo_uuid: str | None = None,
    ) -> dict:
        """Register a webhook on the given repository."""
        if self._is_cloud:
            payload = {
                "description": "Exlogare RCA",
                "url": url,
                "active": True,
                "secret": secret,
                "events": ["repo:commit_status_updated"],
            }
            r = await self._post(
                f"/repositories/{quote(workspace_or_project, safe='')}"
                f"/{_cloud_repo_path_segment(repo_slug, repo_uuid)}/hooks",
                json=payload,
            )
            return r.json() or {}
        if not self.connection.api_token_enc:
            raise RuntimeError(
                "Bitbucket DC webhook registration requires a Personal Access Token; "
                "register the webhook manually using the URL+secret returned by /webhook/init"
            )
        payload = {
            "name": "Exlogare RCA",
            "url": url,
            "active": True,
            "events": ["repo:build_status_updated"],
            "configuration": {"secret": secret},
        }
        r = await self._post(
            f"/rest/api/1.0/projects/{quote(workspace_or_project, safe='')}"
            f"/repos/{quote(repo_slug, safe='')}/webhooks",
            json=payload,
        )
        return r.json() or {}

    async def list_webhooks(
        self, workspace_or_project: str, repo_slug: str
    ) -> list[dict[str, Any]]:
        if self._is_cloud:
            r = await self._get(
                f"/repositories/{quote(workspace_or_project, safe='')}"
                f"/{quote(repo_slug, safe='')}/hooks"
            )
            return (r.json() or {}).get("values") or []
        r = await self._get(
            f"/rest/api/1.0/projects/{quote(workspace_or_project, safe='')}"
            f"/repos/{quote(repo_slug, safe='')}/webhooks"
        )
        return (r.json() or {}).get("values") or []

    async def delete_webhook(
        self,
        workspace_or_project: str,
        repo_slug: str,
        hook_id: str,
        *,
        repo_uuid: str | None = None,
    ) -> bool:
        """Delete a webhook by its remote id."""
        if self._is_cloud:
            p = (
                f"/repositories/{quote(workspace_or_project, safe='')}"
                f"/{_cloud_repo_path_segment(repo_slug, repo_uuid)}/hooks/{hook_id}"
            )
        else:
            p = (
                f"/rest/api/1.0/projects/{quote(workspace_or_project, safe='')}"
                f"/repos/{quote(repo_slug, safe='')}/webhooks/{hook_id}"
            )
        r = await self._delete(p)
        if r.status_code == 404:
            return True
        r.raise_for_status()
        return 200 <= r.status_code < 300

    @staticmethod
    def _owner_repo_for_event_static(
        conn: CIConnection, event: FailureEvent
    ) -> tuple[str, str]:
        path = (event.project_path or conn.external_project_name or "").strip()
        if "/" in path:
            a, b = path.split("/", 1)
            return a.strip(), b.strip()
        return _conn_workspace_repo(conn)

    def _owner_repo_for_event(self, event: FailureEvent) -> tuple[str, str]:
        return self._owner_repo_for_event_static(self.connection, event)

    def _rca_body(self, event: FailureEvent, analysis: AnalysisOutput) -> str:
        return render_gitlab_comment(event, analysis)

    async def _post_build_status(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        ws: str,
        slug: str,
        analysis_id: str | None,
    ) -> dict | None:
        """Best-effort Bitbucket Build Status for the failing commit."""

        if not event.sha:
            return None
        url = status_check_details_url(
            analysis_id, fallback=event.pipeline_url or None
        ) or (event.pipeline_url or "")
        # Bitbucket requires a non-empty ``url``.
        if not url:
            return None
        key = status_check_context()
        if self._is_cloud:
            payload = {
                "key": key,
                "state": bitbucket_state(analysis),
                "name": status_check_title(analysis),
                "description": status_check_summary(analysis),
                "url": url,
            }
            try:
                r = await self._post(
                    f"/repositories/{quote(ws, safe='')}/{quote(slug, safe='')}"
                    f"/commit/{quote(event.sha, safe='')}/statuses/build",
                    json=payload,
                )
            except httpx.HTTPStatusError as exc:
                log.info(
                    "bitbucket.build_status_skipped",
                    status_code=exc.response.status_code,
                    sha=event.sha,
                )
                return None
            except Exception as exc:
                log.warning(
                    "bitbucket.build_status_failed",
                    error=str(exc),
                    sha=event.sha,
                )
                return None
            body = r.json() or {}
            links = (body.get("links") or {}).get("self") or {}
            return {
                "ref": f"bb_build_status:{key}",
                "url": links.get("href") or url,
            }
        # Data Center
        payload = {
            "state": bitbucket_state(analysis),
            "key": key,
            "name": status_check_title(analysis),
            "description": status_check_summary(analysis),
            "url": url,
        }
        try:
            await self._post(
                f"/rest/build-status/1.0/commits/{quote(event.sha, safe='')}",
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            log.info(
                "bitbucket.build_status_skipped",
                status_code=exc.response.status_code,
                sha=event.sha,
            )
            return None
        except Exception as exc:
            log.warning(
                "bitbucket.build_status_failed",
                error=str(exc),
                sha=event.sha,
            )
            return None
        return {"ref": f"bb_build_status:{key}", "url": url}

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
        ws, slug = self._owner_repo_for_event(event)
        body = self._rca_body(event, analysis)

        build_status_result: dict | None = None
        if policy.get("status_check") and event.sha:
            build_status_result = await self._post_build_status(
                event,
                analysis,
                ws=ws,
                slug=slug,
                analysis_id=analysis_id,
            )

        def _attach(result: dict) -> dict:
            if build_status_result:
                return {**result, "status_check": build_status_result}
            return result

        if policy.get("mr_comment") and event.mr_iid:
            try:
                num = int(str(event.mr_iid))
            except (TypeError, ValueError):
                num = None
            if num is not None:
                if self._is_cloud:
                    r = await self._post(
                        f"/repositories/{quote(ws, safe='')}/{quote(slug, safe='')}"
                        f"/pullrequests/{num}/comments",
                        json={"content": {"raw": body}},
                    )
                    c = r.json() or {}
                    links = (c.get("links") or {}).get("html") or {}
                    return _attach({
                        "channel": "mr",
                        "ref": f"pr_comment:{c.get('id')}",
                        "url": links.get("href"),
                    })
                # DC
                r = await self._post(
                    f"/rest/api/1.0/projects/{quote(ws, safe='')}"
                    f"/repos/{quote(slug, safe='')}/pull-requests/{num}/comments",
                    json={"text": body},
                )
                c = r.json() or {}
                return _attach({
                    "channel": "mr",
                    "ref": f"pr_comment:{c.get('id')}",
                    "url": (
                        f"{(self.connection.base_url or '').rstrip('/')}"
                        f"/projects/{ws}/repos/{slug}/pull-requests/{num}/overview"
                    ),
                })

        if policy.get("commit_comment") and event.sha:
            if self._is_cloud:
                r2 = await self._post(
                    f"/repositories/{quote(ws, safe='')}/{quote(slug, safe='')}"
                    f"/commit/{event.sha}/comments",
                    json={"content": {"raw": body}},
                )
                c2 = r2.json() or {}
                links = (c2.get("links") or {}).get("html") or {}
                return _attach({
                    "channel": "commit",
                    "ref": f"bb_commit:{c2.get('id')}",
                    "url": links.get("href"),
                })
            # DC
            r2 = await self._post(
                f"/rest/api/1.0/projects/{quote(ws, safe='')}"
                f"/repos/{quote(slug, safe='')}/commits/{event.sha}/comments",
                json={"text": body},
            )
            c2 = r2.json() or {}
            return _attach({
                "channel": "commit",
                "ref": f"bb_commit:{c2.get('id')}",
                "url": (
                    f"{(self.connection.base_url or '').rstrip('/')}"
                    f"/projects/{ws}/repos/{slug}/commits/{event.sha}"
                ),
            })

        if build_status_result:
            return {
                "channel": "status_check",
                "ref": build_status_result.get("ref", ""),
                "url": build_status_result.get("url"),
                "status_check": build_status_result,
            }
        log.info(
            "bitbucket.publish_skipped",
            run_id=event.ci_run_id,
            cloud=self._is_cloud,
        )
        return None


def _auth_headers(conn: CIConnection) -> dict[str, str]:
    t = _bearer_token(conn)
    if not t:
        return {"Accept": "application/json"}
    return {
        "Authorization": f"Bearer {t}",
        "Accept": "application/json",
    }
