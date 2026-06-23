from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, get_current_principal
from app.models.api_token import ApiToken
from app.models.ci_connection import CIConnection, CIProvider
from app.services.selfhost_policy import get_capabilities as base_caps

router = APIRouter(prefix="/api/plan", tags=["plan"])


class PlanCapabilitiesOut(BaseModel):
    plan: str
    effective_plan: str
    gitlab_modes: list[str]
    gitlab_oauth_redirect_uri: str
    max_gitlab_repos: int | None
    current_gitlab_repos: int
    github_modes: list[str]
    max_github_repos: int | None
    current_github_repos: int
    github_oauth_redirect_uri: str
    bitbucket_modes: list[str]
    max_bitbucket_repos: int | None
    current_bitbucket_repos: int
    bitbucket_oauth_redirect_uri: str
    gitflic_modes: list[str]
    max_gitflic_repos: int | None
    current_gitflic_repos: int
    gitflic_oauth_redirect_uri: str
    hybrid_allowed: bool
    api_keys_allowed: bool
    max_api_keys: int | None
    current_api_keys: int
    notifications_enabled: bool
    outbound_webhooks_enabled: bool
    history_retention_days: int
    support_level: str
    quota: dict


async def _count_repos(session: AsyncSession, tenant_id, provider: CIProvider) -> int:
    stmt = select(func.count(CIConnection.id)).where(
        CIConnection.tenant_id == tenant_id,
        CIConnection.provider == provider,
        CIConnection.external_project_id.isnot(None),
        CIConnection.enabled.is_(True),
    )
    return int((await session.execute(stmt)).scalar() or 0)


@router.get("/capabilities", response_model=PlanCapabilitiesOut)
async def get_plan_capabilities(
    principal: CurrentPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_db),
) -> PlanCapabilitiesOut:
    settings = get_settings()
    caps = base_caps()
    tenant_id = principal.tenant.id
    current_tokens = int(
        (
            await session.execute(
                select(func.count(ApiToken.id)).where(
                    ApiToken.tenant_id == tenant_id,
                    ApiToken.revoked_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    return PlanCapabilitiesOut(
        plan="community",
        effective_plan="community",
        gitlab_modes=caps["gitlab_modes_allowed"],
        gitlab_oauth_redirect_uri=settings.gitlab_oauth_redirect_uri,
        max_gitlab_repos=None,
        current_gitlab_repos=await _count_repos(session, tenant_id, CIProvider.GITLAB),
        github_modes=["webhook", "oauth_polling", "hybrid"],
        max_github_repos=None,
        current_github_repos=await _count_repos(session, tenant_id, CIProvider.GITHUB),
        github_oauth_redirect_uri=settings.github_oauth_redirect_uri,
        bitbucket_modes=["webhook", "oauth_polling", "hybrid"],
        max_bitbucket_repos=None,
        current_bitbucket_repos=await _count_repos(session, tenant_id, CIProvider.BITBUCKET),
        bitbucket_oauth_redirect_uri=settings.bitbucket_oauth_redirect_uri,
        gitflic_modes=["webhook", "oauth_polling", "hybrid"],
        max_gitflic_repos=None,
        current_gitflic_repos=await _count_repos(session, tenant_id, CIProvider.GITFLIC),
        gitflic_oauth_redirect_uri=settings.gitflic_oauth_redirect_uri,
        hybrid_allowed=True,
        api_keys_allowed=True,
        max_api_keys=None,
        current_api_keys=current_tokens,
        notifications_enabled=True,
        outbound_webhooks_enabled=True,
        history_retention_days=settings.retention_days,
        support_level="community",
        quota={
            "can_run_analysis": True,
            "block_reason": None,
            "prepaid_analyses_remaining": 0,
            "lifetime_analyses_used": 0,
            "monthly_analyses_used": 0,
            "unlimited": True,
        },
    )
