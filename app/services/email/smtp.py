from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.email.base import EmailSender

log = get_logger(__name__)


@dataclass(frozen=True)
class _SmtpConn:
    host: str
    port: int
    username: str
    password: str
    mode: str
    from_addr: str


class SMTPEmailSender(EmailSender):
    """Sends email via SMTP."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def _resolve_conn(self) -> _SmtpConn | None:
        s = self.settings
        if s.smtp_host and s.smtp_username:
            if s.smtp_port == 465:
                mode = "ssl"
            elif s.smtp_starttls:
                mode = "starttls"
            else:
                mode = "plain"
            return _SmtpConn(
                host=s.smtp_host,
                port=s.smtp_port,
                username=s.smtp_username,
                password=s.smtp_password,
                mode=mode,
                from_addr=s.smtp_from or s.from_email,
            )
        if s.smtp_url:
            parsed = urlparse(s.smtp_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (465 if parsed.scheme == "smtps" else 25)
            mode = "ssl" if parsed.scheme == "smtps" else (
                "starttls" if (parsed.username and port != 465) else "plain"
            )
            return _SmtpConn(
                host=host,
                port=port,
                username=parsed.username or "",
                password=parsed.password or "",
                mode=mode,
                from_addr=s.smtp_from or s.from_email,
            )
        return None

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        reply_to: str | None = None,
    ) -> None:
        conn_cfg = self._resolve_conn()
        if conn_cfg is None:
            log.warning("email.smtp_no_config")
            return

        msg = EmailMessage()
        msg["From"] = conn_cfg.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        def _send_sync() -> None:
            if conn_cfg.mode == "ssl":
                conn = smtplib.SMTP_SSL(conn_cfg.host, conn_cfg.port, timeout=15)
            else:
                conn = smtplib.SMTP(conn_cfg.host, conn_cfg.port, timeout=15)
            try:
                conn.ehlo()
                if conn_cfg.mode == "starttls":
                    conn.starttls()
                    conn.ehlo()
                if conn_cfg.username:
                    conn.login(conn_cfg.username, conn_cfg.password)
                conn.send_message(msg)
            finally:
                try:
                    conn.quit()
                except Exception:
                    pass

        try:
            await asyncio.to_thread(_send_sync)
            log.info(
                "email.smtp_sent",
                to=to,
                subject=subject,
                host=conn_cfg.host,
                port=conn_cfg.port,
                mode=conn_cfg.mode,
            )
        except Exception as exc:
            log.warning(
                "email.smtp_failed",
                error=str(exc),
                host=conn_cfg.host,
                port=conn_cfg.port,
                mode=conn_cfg.mode,
            )
