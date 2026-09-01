from __future__ import annotations

from app.core.logging import get_logger
from app.models.ci_connection import CIConnection
from app.schemas.failure_event import FailureEvent
from app.services.ci.gitflic_client import GitFlicClient
from app.services.ingestion.base import PollingIngestor

log = get_logger(__name__)


class GitFlicPollingIngestor(PollingIngestor):
    """Polls GitFlic's pipeline list for failed runs since the watermark."""

    def __init__(self, connection: CIConnection) -> None:
        self.connection = connection

    async def pull(self, limit: int) -> list[FailureEvent]:
        since = self.connection.last_seen_pipeline_id
        async with GitFlicClient(self.connection) as client:
            return await client.list_recent_failed_runs(
                since_run_id=since, limit=limit
            )
