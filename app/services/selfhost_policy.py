from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.models.tenant import Tenant

_ALL_MODES = frozenset({"webhook", "oauth_polling", "hybrid"})


def can_enqueue_analysis(_tenant_id: object) -> tuple[bool, str]:
    return True, ""


def get_capabilities() -> dict:
    modes = sorted(_ALL_MODES)
    return {
        "notifications_enabled": True,
        "outbound_webhooks_enabled": True,
        "api_keys_allowed": True,
        "max_api_keys": None,
        "hybrid_mode_allowed": True,
        "max_gitlab_repos": None,
        "max_github_repos": None,
        "max_bitbucket_repos": None,
        "gitlab_modes_allowed": modes,
    }


def get_plan_spec(_tenant: Tenant | None = None) -> _SelfhostPlanSpec:
    return _SelfhostPlanSpec()


class _SelfhostPlanSpec:
    notifications_enabled = True
    api_keys_allowed = True
    max_api_keys = None
    hybrid_mode_allowed = True
    max_gitlab_repos = None
    max_github_repos = None
    max_bitbucket_repos = None

    @property
    def history_retention_days(self) -> int:
        return get_settings().retention_days


def notifications_allowed(_spec: object) -> bool:
    return True


def outbound_webhooks_allowed(_spec: object) -> bool:
    return True


def api_keys_allowed(_spec: object) -> bool:
    return True


def max_api_keys_for(_spec: object) -> int | None:
    return None


def gitlab_modes_allowed(_tenant: Tenant | None, _spec: object) -> frozenset[str]:
    return _ALL_MODES


def github_modes_allowed(_tenant: Tenant | None, _spec: object) -> frozenset[str]:
    return _ALL_MODES


def bitbucket_modes_allowed(_tenant: Tenant | None, _spec: object) -> frozenset[str]:
    return _ALL_MODES


def gitflic_modes_allowed(_tenant: Tenant | None, _spec: object) -> frozenset[str]:
    return _ALL_MODES


def effective_max_github_repos(_spec: object) -> int | None:
    return None


def effective_max_bitbucket_repos(_spec: object) -> int | None:
    return None


def effective_max_gitflic_repos(_spec: object) -> int | None:
    return None


def enforce_repo_caps_for_tenant(*_args: object, **_kwargs: object) -> None:
    return None


def history_retention_days(_tenant: Tenant | None = None) -> int:
    return get_settings().retention_days
