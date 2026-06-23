from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class UsageEvent(UUIDPKMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "ci_run_id",
            "ci_job_id",
            name="uq_usage_events_tenant_provider_run_job",
        ),
        Index("ix_usage_events_tenant_id", "tenant_id"),
        Index("ix_usage_events_timestamp", "timestamp"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ci_job_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="analysis", server_default="analysis"
    )
