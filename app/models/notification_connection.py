from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class NotificationChannel(str, enum.Enum):
    TELEGRAM = "telegram"
    SLACK = "slack"
    MATRIX = "matrix"


class NotificationConnection(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "notification_connections"
    __table_args__ = (Index("ix_notification_connections_tenant_id", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(
            NotificationChannel,
            name="notification_channel",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Shared secret/token storage (encrypted)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Matrix homeserver or Slack webhook URL if applicable
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)

    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
