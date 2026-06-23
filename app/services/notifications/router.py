from __future__ import annotations

import uuid
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.notification_connection import NotificationChannel, NotificationConnection
from app.models.tenant import Tenant
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.notifications.base import NotificationPublisher
from app.services.notifications.matrix import MatrixNotifier
from app.services.notifications.slack import SlackNotifier
from app.services.notifications.telegram import TelegramNotifier
from app.services.selfhost_policy import get_plan_spec, notifications_allowed

log = get_logger(__name__)


_PUBLISHERS: dict[NotificationChannel, NotificationPublisher] = {
    NotificationChannel.TELEGRAM: TelegramNotifier(),
    NotificationChannel.SLACK: SlackNotifier(),
    NotificationChannel.MATRIX: MatrixNotifier(),
}


class DispatchResult(TypedDict):
    connection_id: str
    channel: str
    ok: bool


class ChannelRouter:
    """Routes analysis results to enabled messenger channels for a tenant."""

    async def dispatch(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        event: FailureEvent,
        analysis: AnalysisOutput,
    ) -> list[DispatchResult]:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            log.warning(
                "notifications.dispatch_unknown_tenant",
                tenant_id=str(tenant_id),
            )
            return []
        spec = get_plan_spec(tenant)
        if not notifications_allowed(spec):
            log.info(
                "notifications.skipped_plan_disabled",
                tenant_id=str(tenant_id),
                plan=spec.code.value,
            )
            return []

        stmt = select(NotificationConnection).where(
            NotificationConnection.tenant_id == tenant_id,
            NotificationConnection.enabled.is_(True),
        )
        conns = (await session.execute(stmt)).scalars().all()
        results: list[DispatchResult] = []
        for conn in conns:
            publisher = _PUBLISHERS.get(conn.channel)
            if not publisher:
                continue
            try:
                ok = await publisher.send(conn, event, analysis)
            except Exception as exc:
                log.warning(
                    "notifications.dispatch_error",
                    channel=conn.channel.value,
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
                ok = False
            results.append(
                {
                    "connection_id": str(conn.id),
                    "channel": conn.channel.value,
                    "ok": ok,
                }
            )
        return results
