from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.core.outbound_http import slack_client
from app.models.notification_connection import NotificationConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.notifications.base import NotificationPublisher, render_slack_payload

log = get_logger(__name__)


class SlackNotifier(NotificationPublisher):
    """Sends RCA summaries to Slack via incoming webhook URL."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def send(
        self,
        connection: NotificationConnection,
        event: FailureEvent,
        analysis: AnalysisOutput,
    ) -> bool:
        payload = render_slack_payload(event, analysis)
        async with slack_client() as client:
            if connection.endpoint:
                resp = await client.post(connection.endpoint, json=payload)
            else:
                token = decrypt_str(connection.token_enc)
                if not token or not connection.target:
                    log.warning("slack.missing_config", tenant_id=str(connection.tenant_id))
                    return False
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": connection.target, **payload},
                )
        if resp.status_code >= 400:
            log.warning("slack.send_failed", status=resp.status_code, body=resp.text[:200])
            return False
        return True
