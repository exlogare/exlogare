from __future__ import annotations

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.crypto import decrypt_str
from app.core.logging import get_logger
from app.core.outbound_http import telegram_client
from app.models.notification_connection import NotificationConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.notifications.base import NotificationPublisher, render_telegram_html

log = get_logger(__name__)


class TelegramNotifier(NotificationPublisher):
    """Sends RCA summaries to a Telegram chat via the Bot API."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=6))
    async def send(
        self,
        connection: NotificationConnection,
        event: FailureEvent,
        analysis: AnalysisOutput,
    ) -> bool:
        token = decrypt_str(connection.token_enc)
        if not token or not connection.target:
            log.warning("telegram.missing_config", tenant_id=str(connection.tenant_id))
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = render_telegram_html(event, analysis)
        async with telegram_client() as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": connection.target,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code >= 400:
            log.warning("telegram.send_failed", status=resp.status_code, body=resp.text[:200])
            return False
        return True
