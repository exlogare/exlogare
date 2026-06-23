"""Resolve the effective CI feedback policy for a (tenant, connection) pair."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.ci_connection import CIConnection
    from app.models.tenant import Tenant


CHANNELS: tuple[str, ...] = (
    "mr_comment",
    "commit_comment",
    "issue",
    "status_check",
)


def _default_policy() -> dict[str, bool]:
    return {k: True for k in CHANNELS}


def resolve_feedback_policy(
    tenant: "Tenant | None",
    connection: "CIConnection | None" = None,
) -> dict[str, bool]:
    """Compute the effective feedback policy."""

    defaults = _default_policy()
    if tenant is not None:
        raw = getattr(tenant, "feedback_defaults", None) or {}
        if isinstance(raw, dict):
            for k in CHANNELS:
                if k in raw:
                    defaults[k] = bool(raw[k])

    override: dict = {}
    if connection is not None:
        extra = getattr(connection, "extra", None) or {}
        if isinstance(extra, dict):
            maybe = extra.get("feedback")
            if isinstance(maybe, dict):
                override = maybe

    resolved: dict[str, bool] = {}
    for k in CHANNELS:
        tenant_flag = bool(defaults.get(k, True))
        if k in override:
            conn_flag = bool(override.get(k))
        else:
            conn_flag = tenant_flag
        resolved[k] = tenant_flag and conn_flag
    return resolved
