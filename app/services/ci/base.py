from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent


class CIProviderClient(ABC):
    """Provider-agnostic CI client interface."""

    @abstractmethod
    async def fetch_job_log(self, event: FailureEvent) -> str: ...

    @abstractmethod
    async def list_recent_failed_runs(self, since_run_id: str | None, limit: int) -> list[FailureEvent]:
        """List failed pipelines/jobs for polling-based ingestion."""


class FeedbackPublisher(ABC):
    """Publishes the RCA back into the developer's context (MR comment, build output, etc.)."""

    @abstractmethod
    async def publish(
        self,
        event: FailureEvent,
        analysis: AnalysisOutput,
        *,
        policy: dict[str, bool] | None = None,
        analysis_id: str | None = None,
    ) -> dict | None:
        """Publish feedback via the first enabled channel of the cascade."""
