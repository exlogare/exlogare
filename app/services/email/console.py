from __future__ import annotations

from app.core.logging import get_logger
from app.services.email.base import EmailSender

log = get_logger(__name__)


class ConsoleEmailSender(EmailSender):
    """Prints the email to stdout - for local dev only."""

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        reply_to: str | None = None,
    ) -> None:
        separator = "=" * 60
        reply_to_line = f"REPLY-TO={reply_to}\n" if reply_to else ""
        message = (
            f"\n{separator}\n"
            f"[email:console] TO={to}\n"
            f"{reply_to_line}"
            f"SUBJECT={subject}\n"
            f"--- TEXT ---\n{text_body}\n"
            f"{separator}\n"
        )
        print(message, flush=True)
        log.info("email.console_sent", to=to, subject=subject, reply_to=reply_to)
