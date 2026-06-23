from __future__ import annotations

from app.core.logging import get_logger
from app.models.ci_connection import CIConnection
from app.schemas.failure_event import FailureEvent
from app.services.ci.gitlab_client import GitLabClient
from app.services.ingestion.base import PollingIngestor

log = get_logger(__name__)


class GitLabPollingIngestor(PollingIngestor):
    """Uses GitLab Jobs API to detect failed jobs since the last watermark."""

    def __init__(self, connection: CIConnection) -> None:
        self.connection = connection

    async def pull(self, limit: int) -> list[FailureEvent]:
        since = self.connection.last_seen_job_id
        async with GitLabClient(self.connection) as client:
            events = await client.list_recent_failed_jobs(since_job_id=since, limit=limit)
        return events
