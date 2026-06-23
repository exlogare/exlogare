"""Helpers for posting status checks (GitHub Check Runs / Bitbucket Build Statuses)."""

from __future__ import annotations

from app.core.config import get_settings
from app.schemas.analysis import AnalysisOutput

_STATUS_CONTEXT = "exlogare/rca"
_STATUS_DESCRIPTION_LIMIT = 140


def status_check_context() -> str:
    """The "name" / "key" used to identify our check across runs."""

    return _STATUS_CONTEXT


def status_check_title(analysis: AnalysisOutput) -> str:
    return f"Exlogare RCA - {analysis.severity.upper()} severity"


def status_check_summary(analysis: AnalysisOutput) -> str:
    """Single-line description for the commit-level UI."""

    raw = (analysis.root_cause or "").strip().splitlines()
    summary = raw[0] if raw else "CI failure analysed by Exlogare"
    if len(summary) > _STATUS_DESCRIPTION_LIMIT:
        summary = summary[: _STATUS_DESCRIPTION_LIMIT - 3].rstrip() + "..."
    return summary


def status_check_long_summary(analysis: AnalysisOutput) -> str:
    """Multi-line summary used for GitHub Check Run ``output.summary``."""

    parts: list[str] = []
    if analysis.root_cause:
        parts.append(f"**Root cause:** {analysis.root_cause.strip()}")
    if analysis.fix_suggestion:
        parts.append(f"**Fix suggestion:** {analysis.fix_suggestion.strip()}")
    if analysis.needs_more_context and analysis.missing_context_hint:
        parts.append(
            f"_More context needed:_ {analysis.missing_context_hint.strip()}"
        )
    return "\n\n".join(parts) or "CI failure analysed by Exlogare."


def status_check_details_url(
    analysis_id: str | None,
    *,
    fallback: str | None = None,
) -> str | None:
    """Return the URL the status check entry should link to."""

    if analysis_id:
        base = (get_settings().web_base_url or "").rstrip("/")
        if base:
            return f"{base}/dashboard/analyses/{analysis_id}"
    return fallback


def github_conclusion(analysis: AnalysisOutput) -> str:
    """GitHub Check Run conclusion bucket."""

    sev = (analysis.severity or "").lower()
    if sev == "high":
        return "failure"
    if sev == "medium":
        return "action_required"
    return "neutral"


def bitbucket_state(analysis: AnalysisOutput) -> str:
    """Bitbucket Build Status state."""

    sev = (analysis.severity or "").lower()
    if sev == "high":
        return "FAILED"
    return "STOPPED"
