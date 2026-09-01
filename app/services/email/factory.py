from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.services.email.base import EmailSender
from app.services.email.console import ConsoleEmailSender
from app.services.email.smtp import SMTPEmailSender


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    """Pick the concrete email sender based on settings."""
    settings = get_settings()
    has_split = bool(settings.smtp_host and settings.smtp_username)
    has_url = bool(settings.smtp_url)

    if settings.email_provider == "smtp":
        return SMTPEmailSender()
    if settings.email_provider == "console":
        return ConsoleEmailSender()
    # auto
    if has_split or has_url:
        return SMTPEmailSender()
    return ConsoleEmailSender()
