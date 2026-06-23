from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.failure_event import FailureEvent


class WebhookIngestor(ABC):
    """Parses a webhook payload into zero or one FailureEvent."""

    @abstractmethod
    async def parse(self, tenant_id, ci_connection_id, payload: dict) -> FailureEvent | None: ...


class PollingIngestor(ABC):
    """Pulls recent failures from a provider API for polling-based ingestion."""

    @abstractmethod
    async def pull(self, limit: int) -> list[FailureEvent]: ...
