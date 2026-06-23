from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class IngestionSource(str, enum.Enum):
    GITLAB_WEBHOOK = "gitlab_webhook"
    GITLAB_POLL = "gitlab_poll"
    GITHUB_WEBHOOK = "github_webhook"
    GITHUB_POLL = "github_poll"
    BITBUCKET_WEBHOOK = "bitbucket_webhook"
    BITBUCKET_POLL = "bitbucket_poll"
    GITFLIC_WEBHOOK = "gitflic_webhook"
    GITFLIC_POLL = "gitflic_poll"
    JENKINS_WEBHOOK = "jenkins_webhook"
    JENKINS_INGEST = "jenkins_ingest"
    CIRCLECI_INGEST = "circleci_ingest"
    TEAMCITY_INGEST = "teamcity_ingest"
    DRONE_INGEST = "drone_ingest"
    GENERIC_INGEST = "generic_ingest"
    MANUAL = "manual"


class IngestionEvent(UUIDPKMixin, Base):
    __tablename__ = "ingestion_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "ci_run_id",
            name="uq_ingestion_events_tenant_provider_run",
        ),
        Index("ix_ingestion_events_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ci_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ci_connections.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[IngestionSource] = mapped_column(
        Enum(
            IngestionSource,
            name="ingestion_source",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ci_job_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default="received", nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
