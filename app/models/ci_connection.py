from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class CIProvider(str, enum.Enum):
    GITLAB = "gitlab"
    GITHUB = "github"
    BITBUCKET = "bitbucket"
    GITFLIC = "gitflic"
    JENKINS = "jenkins"


class IntegrationMode(str, enum.Enum):
    WEBHOOK = "webhook"
    OAUTH_POLLING = "oauth_polling"
    HYBRID = "hybrid"


class ConnectionStatus(str, enum.Enum):
    PENDING_MANUAL = "pending_manual"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class CIConnection(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "ci_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_project_id",
            name="uq_ci_connections_tenant_provider_project",
        ),
        Index("ix_ci_connections_tenant_id", "tenant_id"),
        Index("ix_ci_connections_provider", "provider"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[CIProvider] = mapped_column(
        Enum(
            CIProvider,
            name="ci_provider",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    mode: Mapped[IntegrationMode] = mapped_column(
        Enum(
            IntegrationMode,
            name="integration_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=IntegrationMode.WEBHOOK,
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(
            ConnectionStatus,
            name="connection_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ConnectionStatus.PENDING_MANUAL,
    )

    base_url: Mapped[str] = mapped_column(String(512), nullable=False, default="https://gitlab.com")

    external_project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_project_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_project_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    webhook_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_id_remote: Mapped[str | None] = mapped_column(String(128), nullable=True)

    oauth_client_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    oauth_scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gitlab_user_info: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    api_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_pipeline_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_seen_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
