from __future__ import annotations

import secrets
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.models.notification_connection import NotificationConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.notifications.base import NotificationPublisher, render_matrix_payload

log = get_logger(__name__)


class MatrixNotifier(NotificationPublisher):
    """Sends RCA summaries to an Element/Matrix room via Client-Server API."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def send(
        self,
        connection: NotificationConnection,
        event: FailureEvent,
        analysis: AnalysisOutput,
    ) -> bool:
        token = decrypt_str(connection.token_enc)
        if not token or not connection.target or not connection.endpoint:
            log.warning("matrix.missing_config", tenant_id=str(connection.tenant_id))
            return False
        txn_id = secrets.token_hex(12)
        room_id = quote(connection.target, safe="")
        url = (
            f"{connection.endpoint.rstrip('/')}"
            f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
        )
        body = render_matrix_payload(event, analysis)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        if resp.status_code >= 400:
            log.warning("matrix.send_failed", status=resp.status_code, body=resp.text[:200])
            return False
        return True
