"""Bitbucket Cloud polling ingestor."""

from __future__ import annotations

from app.core.logging import get_logger
from app.models.ci_connection import CIConnection
from app.schemas.failure_event import FailureEvent
from app.services.ci.bitbucket_client import BitbucketClient
from app.services.ingestion.base import PollingIngestor
from app.services.oauth.bitbucket import is_bitbucket_cloud

log = get_logger(__name__)


class BitbucketPollingIngestor(PollingIngestor):
    def __init__(self, connection: CIConnection) -> None:
        self.connection = connection

    async def pull(self, limit: int) -> list[FailureEvent]:
        if not is_bitbucket_cloud(self.connection.base_url):
            return []
        since = self.connection.last_seen_pipeline_id
        async with BitbucketClient(self.connection) as client:
            return await client.list_recent_failed_runs(
                since_run_id=since, limit=limit
            )
