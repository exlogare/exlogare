"""Failure clustering — recurring-issue tracker."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analysis_result import Severity
from app.models.base import Base, TimestampMixin, UUIDPKMixin


class ClusterStatus(str, enum.Enum):
    """Lifecycle of a failure cluster."""

    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class FailureCluster(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "failure_clusters"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "fingerprint_hash",
            name="uq_failure_clusters_tenant_fingerprint",
        ),
        # Drives the Clusters page (most-recent first).
        Index(
            "ix_failure_clusters_tenant_last_seen",
            "tenant_id",
            "last_seen_at",
        ),
        Index(
            "ix_failure_clusters_tenant_lookup",
            "tenant_id",
            "lookup_hash",
            "last_seen_at",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lookup_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    last_root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    last_severity: Mapped[Severity] = mapped_column(
        SAEnum(
            Severity,
            name="severity",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        ),
        nullable=False,
    )

    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    last_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_results.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[ClusterStatus] = mapped_column(
        SAEnum(
            ClusterStatus,
            name="failure_cluster_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ClusterStatus.ACTIVE,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
