from __future__ import annotations

from abc import ABC, abstractmethod
from html import escape
from typing import Any

from app.models.notification_connection import NotificationConnection
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent


class NotificationPublisher(ABC):
    @abstractmethod
    async def send(
        self,
        connection: NotificationConnection,
        event: FailureEvent,
        analysis: AnalysisOutput,
    ) -> bool: ...


_SEVERITY_EMOJI: dict[str, str] = {
    "low": "\U0001F7E2",  # green circle
    "medium": "\U0001F7E1",  # yellow circle
    "high": "\U0001F534",  # red circle
}


def _severity_emoji(severity: str) -> str:
    return _SEVERITY_EMOJI.get(severity.lower(), "\u26A0")  # warning sign


def _project_label(event: FailureEvent) -> str:
    return event.project_path or event.project_id or "project"


def _short_sha(sha: str | None) -> str | None:
    if not sha:
        return None
    return sha[:8]


def _truncate(text: str, limit: int) -> str:
    """Cut to ``limit`` chars on a word boundary, append an ellipsis if truncated."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "\u2026"  # …


def render_summary(event: FailureEvent, analysis: AnalysisOutput) -> str:
    """Plain-text one-screen summary - last-resort fallback for any client."""
    project = _project_label(event)
    link = event.pipeline_url or ""
    return (
        f"[Exlogare] CI failure in {project}\n"
        f"Severity: {analysis.severity.upper()} | Confidence: {analysis.confidence:.2f}\n"
        f"Root cause: {analysis.root_cause}\n"
        f"Fix: {analysis.fix_suggestion}\n"
        f"{link}"
    ).rstrip()


def render_telegram_html(event: FailureEvent, analysis: AnalysisOutput) -> str:
    """Compose a Telegram-formatted CI failure card."""
    project = escape(_project_label(event))
    severity = analysis.severity.upper()
    emoji = _severity_emoji(analysis.severity)
    confidence = f"{analysis.confidence:.0%}"

    project_link = event.project_web_url
    title_line = (
        f'{emoji} <b>CI failure in <a href="{escape(project_link)}">{project}</a></b>'
        if project_link
        else f"{emoji} <b>CI failure in {project}</b>"
    )

    meta_parts: list[str] = [
        f"<b>Severity:</b> {escape(severity)}",
        f"<b>Confidence:</b> {confidence}",
    ]
    if event.ref:
        meta_parts.append(f"<b>Branch:</b> <code>{escape(event.ref)}</code>")
    short = _short_sha(event.sha)
    if short:
        meta_parts.append(f"<b>Commit:</b> <code>{escape(short)}</code>")
    meta_line = " | ".join(meta_parts)

    root_cause = escape(_truncate(analysis.root_cause, 600))
    fix = escape(_truncate(analysis.fix_suggestion, 1500))

    sections: list[str] = [
        title_line,
        meta_line,
        "",
        f"<b>\U0001F50D Root cause</b>\n{root_cause}",
        "",
        f"<b>\U0001F527 Fix</b>\n{fix}",
    ]

    if analysis.needs_more_context and analysis.missing_context_hint:
        sections.append("")
        sections.append(
            f"<i>More context needed:</i> {escape(analysis.missing_context_hint)}"
        )

    footer_links: list[str] = []
    if event.pipeline_url:
        footer_links.append(
            f'<a href="{escape(event.pipeline_url)}">Open pipeline</a>'
        )
    if event.job_url and event.job_url != event.pipeline_url:
        footer_links.append(f'<a href="{escape(event.job_url)}">Job log</a>')
    if footer_links:
        sections.append("")
        sections.append(" \u2022 ".join(footer_links))

    return "\n".join(sections)


def render_slack_payload(
    event: FailureEvent, analysis: AnalysisOutput
) -> dict[str, Any]:
    """Return the JSON body for either incoming webhooks or chat.postMessage."""
    project = _project_label(event)
    severity = analysis.severity.upper()
    emoji = _severity_emoji(analysis.severity)
    confidence = f"{analysis.confidence:.0%}"

    short = _short_sha(event.sha)
    fields: list[dict[str, Any]] = [
        {"type": "mrkdwn", "text": f"*Severity*\n{severity}"},
        {"type": "mrkdwn", "text": f"*Confidence*\n{confidence}"},
    ]
    if event.ref:
        fields.append({"type": "mrkdwn", "text": f"*Branch*\n`{event.ref}`"})
    if short:
        fields.append({"type": "mrkdwn", "text": f"*Commit*\n`{short}`"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} CI failure in {project}",
                "emoji": True,
            },
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*\U0001F50D Root cause*\n"
                    f"{_truncate(analysis.root_cause, 800)}"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*\U0001F527 Fix*\n"
                    f"{_truncate(analysis.fix_suggestion, 1500)}"
                ),
            },
        },
    ]

    if analysis.needs_more_context and analysis.missing_context_hint:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f":information_source: _More context needed:_ "
                            f"{analysis.missing_context_hint}"
                        ),
                    }
                ],
            }
        )

    actions: list[dict[str, Any]] = []
    if event.pipeline_url:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open pipeline", "emoji": True},
                "url": event.pipeline_url,
            }
        )
    if event.job_url and event.job_url != event.pipeline_url:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Job log", "emoji": True},
                "url": event.job_url,
            }
        )
    if actions:
        blocks.append({"type": "actions", "elements": actions})

    return {
        "text": render_summary(event, analysis),
        "blocks": blocks,
    }


def render_matrix_payload(
    event: FailureEvent, analysis: AnalysisOutput
) -> dict[str, Any]:
    return {
        "msgtype": "m.text",
        "body": render_summary(event, analysis),
        "format": "org.matrix.custom.html",
        "formatted_body": render_telegram_html(event, analysis),
    }
