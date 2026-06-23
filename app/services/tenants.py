from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ci_connection import CIConnection, CIProvider


async def resolve_gitlab_connection(
    session: AsyncSession,
    *,
    project_id: str | None,
    explicit_tenant_id: uuid.UUID | None = None,
) -> CIConnection | None:
    """Find the GitLab CIConnection for a given external project."""
    if not project_id:
        return None
    stmt = select(CIConnection).where(
        CIConnection.provider == CIProvider.GITLAB,
        CIConnection.external_project_id == str(project_id),
        CIConnection.enabled.is_(True),
    )
    if explicit_tenant_id:
        stmt = stmt.where(CIConnection.tenant_id == explicit_tenant_id)
    result = await session.execute(stmt)
    return result.scalars().first()


async def resolve_github_connection(
    session: AsyncSession,
    *,
    repository_id: str | None,
) -> CIConnection | None:
    if not repository_id:
        return None
    stmt = select(CIConnection).where(
        CIConnection.provider == CIProvider.GITHUB,
        CIConnection.external_project_id == str(repository_id),
        CIConnection.enabled.is_(True),
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def resolve_bitbucket_connection(
    session: AsyncSession,
    *,
    repository_uuid: str | None = None,
    full_name: str | None = None,
) -> CIConnection | None:
    """Find a Bitbucket CIConnection by repository UUID or workspace/slug."""
    if repository_uuid:
        stmt = select(CIConnection).where(
            CIConnection.provider == CIProvider.BITBUCKET,
            CIConnection.external_project_id == str(repository_uuid),
            CIConnection.enabled.is_(True),
        )
        result = await session.execute(stmt)
        conn = result.scalars().first()
        if conn is not None:
            return conn
    if full_name:
        stmt2 = select(CIConnection).where(
            CIConnection.provider == CIProvider.BITBUCKET,
            CIConnection.external_project_name == str(full_name),
            CIConnection.enabled.is_(True),
        )
        result2 = await session.execute(stmt2)
        return result2.scalars().first()
    return None


async def resolve_gitflic_connection(
    session: AsyncSession,
    *,
    project_id: str | None,
) -> CIConnection | None:
    """Resolve a GitFlic CIConnection by project UUID."""
    if not project_id:
        return None
    stmt = select(CIConnection).where(
        CIConnection.provider == CIProvider.GITFLIC,
        CIConnection.external_project_id == str(project_id),
        CIConnection.enabled.is_(True),
    )
    return (await session.execute(stmt)).scalars().first()
