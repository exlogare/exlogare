"""Provider dispatch for log fetch and feedback publish."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from app.models.ci_connection import CIConnection, CIProvider
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.ci.bitbucket_client import BitbucketClient
from app.services.ci.github_client import GitHubClient
from app.services.ci.gitflic_client import GitFlicClient
from app.services.ci.gitlab_client import GitLabClient

C = TypeVar("C", GitLabClient, GitHubClient, BitbucketClient, GitFlicClient)


@asynccontextmanager
async def _gitlab_ctx(conn: CIConnection) -> AsyncIterator[GitLabClient]:
    async with GitLabClient(conn) as client:
        yield client


@asynccontextmanager
async def _github_ctx(conn: CIConnection) -> AsyncIterator[GitHubClient]:
    async with GitHubClient(conn) as client:
        yield client


@asynccontextmanager
async def _bitbucket_ctx(conn: CIConnection) -> AsyncIterator[BitbucketClient]:
    async with BitbucketClient(conn) as client:
        yield client


@asynccontextmanager
async def _gitflic_ctx(conn: CIConnection) -> AsyncIterator[GitFlicClient]:
    async with GitFlicClient(conn) as client:
        yield client


_CLIENT_CTX: dict[CIProvider, Any] = {
    CIProvider.GITLAB: _gitlab_ctx,
    CIProvider.GITHUB: _github_ctx,
    CIProvider.BITBUCKET: _bitbucket_ctx,
    CIProvider.GITFLIC: _gitflic_ctx,
}


async def fetch_job_log_for_connection(
    connection: CIConnection, event: FailureEvent
) -> str:
    ctx = _CLIENT_CTX.get(connection.provider)
    if ctx is None:
        raise ValueError(f"Unsupported CI provider for log fetch: {connection.provider!r}")
    async with ctx(connection) as client:
        return await client.fetch_job_log(event)


async def publish_feedback_for_connection(
    connection: CIConnection,
    event: FailureEvent,
    analysis: AnalysisOutput,
    policy: dict[str, bool] | None,
    *,
    analysis_id: str | None = None,
) -> dict | None:
    ctx = _CLIENT_CTX.get(connection.provider)
    if ctx is None:
        return None
    async with ctx(connection) as client:
        return await client.publish(
            event, analysis, policy=policy, analysis_id=analysis_id
        )
