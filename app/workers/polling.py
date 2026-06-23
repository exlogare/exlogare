from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.models.ci_connection import (
    CIConnection,
    CIProvider,
    IntegrationMode,
)
from app.services.ci.bitbucket_client import BitbucketClient
from app.services.ci.github_client import GitHubClient
from app.services.ci.gitlab_client import GitLabClient
from app.services.ci.gitflic_client import GitFlicClient
from app.services.ingestion.bitbucket_polling import BitbucketPollingIngestor
from app.services.ingestion.github_polling import GitHubPollingIngestor
from app.services.ingestion.gitflic_polling import GitFlicPollingIngestor
from app.services.ingestion.gitlab_polling import GitLabPollingIngestor
from app.services.oauth.bitbucket import is_bitbucket_cloud
from app.services.oauth.gitlab import GitLabOAuthService
from app.services.oauth.gitlab_group import (
    group_key as _group_key_shared,
    propagate_tokens as _propagate_tokens_shared,
    refresh_group_tokens as _refresh_group_tokens_shared,
)
from app.services.pipeline import persist_ingestion_event
from app.workers._async import run_async, worker_session_scope

configure_logging()
log = get_logger("poller")

_EMPTY_PROJECT_WATERMARK = "0"


@celery_app.task(name="app.workers.polling.poll_all_oauth_tenants", acks_late=True)
def poll_all_oauth_tenants() -> dict:
    return run_async(_poll_all)


async def _prime_connection(conn: CIConnection) -> None:
    """Snapshot the current latest job id without emitting any events."""
    async with GitLabClient(conn) as client:
        latest = await client.get_latest_job_id()
    conn.last_seen_job_id = latest or _EMPTY_PROJECT_WATERMARK
    conn.last_polled_at = datetime.now(tz=timezone.utc)
    log.info(
        "poller.primed",
        connection_id=str(conn.id),
        project_id=conn.external_project_id,
        last_seen_job_id=conn.last_seen_job_id,
    )


async def _prime_github_connection(conn: CIConnection) -> None:
    async with GitHubClient(conn) as client:
        latest = await client.get_latest_workflow_run_id()
    conn.last_seen_pipeline_id = latest or "0"
    conn.last_polled_at = datetime.now(tz=timezone.utc)
    log.info(
        "poller.github_primed",
        connection_id=str(conn.id),
        last_seen_pipeline_id=conn.last_seen_pipeline_id,
    )


async def _prime_bitbucket_connection(conn: CIConnection) -> None:
    """Snapshot the latest Bitbucket Cloud pipeline UUID so we don't replay history."""
    async with BitbucketClient(conn) as client:
        latest = await client.get_latest_pipeline_uuid()
    conn.last_seen_pipeline_id = latest or _EMPTY_PROJECT_WATERMARK
    conn.last_polled_at = datetime.now(tz=timezone.utc)
    log.info(
        "poller.bitbucket_primed",
        connection_id=str(conn.id),
        last_seen_pipeline_id=conn.last_seen_pipeline_id,
    )


def _propagate_tokens(group: list[CIConnection], source: CIConnection) -> None:
    """Backwards-compatible alias for :func:`propagate_tokens`."""
    _propagate_tokens_shared(group, source)


async def _refresh_group_tokens(
    oauth: GitLabOAuthService, group: list[CIConnection]
) -> bool:
    """Refresh one credential group with the worker's semantics."""
    return await _refresh_group_tokens_shared(
        oauth, group, mark_error_on_exhaust=True
    )


def _group_key(c: CIConnection) -> tuple[uuid.UUID, str]:
    return _group_key_shared(c)


async def _poll_all() -> dict:
    settings = get_settings()
    total = 0
    discovered = 0
    primed = 0
    groups_total = 0
    groups_refreshed = 0
    async with worker_session_scope() as session:
        # Pollable connections: OAuth/hybrid projects that are enabled.
        poll_stmt = select(CIConnection).where(
            CIConnection.provider == CIProvider.GITLAB,
            CIConnection.enabled.is_(True),
            CIConnection.mode.in_([IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID]),
        )
        project_conns = (await session.execute(poll_stmt)).scalars().all()

        tenants = {c.tenant_id for c in project_conns}
        group_members: dict[tuple[uuid.UUID, str], list[CIConnection]] = defaultdict(list)
        if tenants:
            all_stmt = select(CIConnection).where(
                CIConnection.provider == CIProvider.GITLAB,
                CIConnection.tenant_id.in_(tenants),
            )
            for c in (await session.execute(all_stmt)).scalars().all():
                group_members[_group_key(c)].append(c)

        oauth = GitLabOAuthService()
        usable_groups: set[tuple[uuid.UUID, str]] = set()
        for key, members in group_members.items():
            groups_total += 1
            if await _refresh_group_tokens(oauth, members):
                usable_groups.add(key)
                groups_refreshed += 1

        for conn in project_conns:
            if _group_key(conn) not in usable_groups:
                continue
            try:
                if conn.last_seen_job_id is None:
                    await _prime_connection(conn)
                    primed += 1
                    total += 1
                    continue

                ingestor = GitLabPollingIngestor(conn)
                events = await ingestor.pull(limit=settings.poll_batch_size)
                total += 1
                log.info(
                    "poller.connection_polled",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    since_job_id=conn.last_seen_job_id,
                    events_returned=len(events),
                    event_job_ids=[e.ci_job_id for e in events],
                )
                if not events:
                    conn.last_polled_at = datetime.now(tz=timezone.utc)
                    continue
                max_job_seen = conn.last_seen_job_id
                max_pipeline_seen = conn.last_seen_pipeline_id
                for ev in events:
                    created = await persist_ingestion_event(session, ev)
                    log.info(
                        "poller.event_persist",
                        connection_id=str(conn.id),
                        ci_run_id=ev.ci_run_id,
                        ci_job_id=ev.ci_job_id,
                        created=bool(created),
                    )
                    if created:
                        discovered += 1
                        from app.workers.tasks import analyze_failure

                        analyze_failure.delay(ev.model_dump(mode="json"))
                    job_id = ev.ci_job_id or ""
                    if job_id.isdigit():
                        if (
                            max_job_seen is None
                            or max_job_seen.isdigit() is False
                            or int(job_id) > int(max_job_seen)
                        ):
                            max_job_seen = job_id
                    if ev.ci_run_id.isdigit():
                        if (
                            max_pipeline_seen is None
                            or max_pipeline_seen.isdigit() is False
                            or int(ev.ci_run_id) > int(max_pipeline_seen)
                        ):
                            max_pipeline_seen = ev.ci_run_id
                conn.last_seen_job_id = max_job_seen
                conn.last_seen_pipeline_id = max_pipeline_seen
                conn.last_polled_at = datetime.now(tz=timezone.utc)
            except Exception as exc:
                log.warning(
                    "poller.connection_failed",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

        gh_stmt = select(CIConnection).where(
            CIConnection.provider == CIProvider.GITHUB,
            CIConnection.enabled.is_(True),
            CIConnection.mode.in_(
                [IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID]
            ),
            CIConnection.external_project_id.isnot(None),
        )
        gh_conns = (await session.execute(gh_stmt)).scalars().all()
        for conn in gh_conns:
            try:
                if conn.last_seen_pipeline_id is None:
                    await _prime_github_connection(conn)
                    primed += 1
                    total += 1
                    continue

                gh_ing = GitHubPollingIngestor(conn)
                events = await gh_ing.pull(limit=settings.poll_batch_size)
                total += 1
                log.info(
                    "poller.github_connection_polled",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    since=conn.last_seen_pipeline_id,
                    events_returned=len(events),
                )
                if not events:
                    conn.last_polled_at = datetime.now(tz=timezone.utc)
                    continue
                max_run = conn.last_seen_pipeline_id
                for ev in events:
                    created = await persist_ingestion_event(session, ev)
                    if created:
                        discovered += 1
                        from app.workers.tasks import analyze_failure

                        analyze_failure.delay(ev.model_dump(mode="json"))
                    if not ev.ci_run_id.isdigit():
                        continue
                    n = int(ev.ci_run_id)
                    cur = int(max_run) if max_run and str(max_run).isdigit() else 0
                    if n > cur:
                        max_run = ev.ci_run_id
                conn.last_seen_pipeline_id = max_run
                conn.last_polled_at = datetime.now(tz=timezone.utc)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "poller.github_connection_failed",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

        bb_stmt = select(CIConnection).where(
            CIConnection.provider == CIProvider.BITBUCKET,
            CIConnection.enabled.is_(True),
            CIConnection.mode.in_(
                [IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID]
            ),
            CIConnection.external_project_id.isnot(None),
        )
        bb_conns = (await session.execute(bb_stmt)).scalars().all()
        for conn in bb_conns:
            if not is_bitbucket_cloud(conn.base_url):
                continue
            try:
                if conn.last_seen_pipeline_id is None:
                    await _prime_bitbucket_connection(conn)
                    primed += 1
                    total += 1
                    continue

                bb_ing = BitbucketPollingIngestor(conn)
                events = await bb_ing.pull(limit=settings.poll_batch_size)
                total += 1
                log.info(
                    "poller.bitbucket_connection_polled",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    since=conn.last_seen_pipeline_id,
                    events_returned=len(events),
                )
                if not events:
                    conn.last_polled_at = datetime.now(tz=timezone.utc)
                    continue
                for ev in events:
                    created = await persist_ingestion_event(session, ev)
                    if created:
                        discovered += 1
                        from app.workers.tasks import analyze_failure

                        analyze_failure.delay(ev.model_dump(mode="json"))
                if events:
                    conn.last_seen_pipeline_id = events[0].ci_run_id or conn.last_seen_pipeline_id
                conn.last_polled_at = datetime.now(tz=timezone.utc)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "poller.bitbucket_connection_failed",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
        gf_stmt = select(CIConnection).where(
            CIConnection.provider == CIProvider.GITFLIC,
            CIConnection.enabled.is_(True),
            CIConnection.mode.in_(
                [IntegrationMode.OAUTH_POLLING, IntegrationMode.HYBRID]
            ),
            CIConnection.external_project_id.isnot(None),
        )
        gf_conns = (await session.execute(gf_stmt)).scalars().all()
        for conn in gf_conns:
            try:
                if conn.last_seen_pipeline_id is None:
                    async with GitFlicClient(conn) as client:
                        recent = await client.list_recent_failed_runs(
                            since_run_id=None, limit=1
                        )
                    conn.last_seen_pipeline_id = (
                        recent[0].ci_run_id if recent else _EMPTY_PROJECT_WATERMARK
                    )
                    conn.last_polled_at = datetime.now(tz=timezone.utc)
                    primed += 1
                    total += 1
                    continue

                gf_ing = GitFlicPollingIngestor(conn)
                events = await gf_ing.pull(limit=settings.poll_batch_size)
                total += 1
                if not events:
                    conn.last_polled_at = datetime.now(tz=timezone.utc)
                    continue
                max_run = conn.last_seen_pipeline_id
                for ev in events:
                    created = await persist_ingestion_event(session, ev)
                    if created:
                        discovered += 1
                        from app.workers.tasks import analyze_failure

                        analyze_failure.delay(ev.model_dump(mode="json"))
                    if ev.ci_run_id.isdigit():
                        cur = (
                            int(max_run) if max_run and str(max_run).isdigit() else 0
                        )
                        if int(ev.ci_run_id) > cur:
                            max_run = ev.ci_run_id
                conn.last_seen_pipeline_id = max_run
                conn.last_polled_at = datetime.now(tz=timezone.utc)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "poller.gitflic_connection_failed",
                    connection_id=str(conn.id),
                    project_id=conn.external_project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

    log.info(
        "poller.cycle_done",
        connections=total,
        primed=primed,
        discovered=discovered,
        groups=groups_total,
        groups_refreshed=groups_refreshed,
    )
    return {
        "connections": total,
        "primed": primed,
        "new_failures": discovered,
        "groups": groups_total,
        "groups_refreshed": groups_refreshed,
    }
