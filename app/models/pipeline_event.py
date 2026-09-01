from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class PipelineEvent(UUIDPKMixin, Base):
    """All pipeline outcomes (success/failed/canceled) for stats and failure-rate charts."""

    __tablename__ = "pipeline_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "ci_run_id", name="uq_pipeline_events_tenant_provider_run"
        ),
        Index("ix_pipeline_events_tenant_id", "tenant_id"),
        Index("ix_pipeline_events_occurred_at", "occurred_at"),
        Index("ix_pipeline_events_project_id", "project_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ci_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ci_connections.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
