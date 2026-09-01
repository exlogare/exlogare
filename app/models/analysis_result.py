from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnalysisResult(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "analysis_results"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "ci_run_id",
            "ci_job_id",
            name="uq_analysis_results_tenant_provider_run_job",
        ),
        Index("ix_analysis_results_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    ci_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ci_connections.id", ondelete="SET NULL"), nullable=True
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ci_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ci_job_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    project_web_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pipeline_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    job_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mr_iid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    fix_suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(
            Severity,
            name="severity",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    raw_response: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    feedback_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
