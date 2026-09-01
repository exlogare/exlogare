from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import engine, session_scope
from app.core.logging import configure_logging, get_logger
from app.models import Base
from app.models.membership import Membership, MembershipRole
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth.password import hash_password

log = get_logger("init_db")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug[:100] or "default"


async def init_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema()
    log.info("init_db.schema_ready")


async def ensure_schema() -> None:
    """Apply additive schema tweaks that create_all cannot do on existing DBs."""
    from sqlalchemy import inspect, text

    async with engine.begin() as conn:
        dialect = conn.dialect.name

        def _user_columns(sync_conn) -> set[str]:
            insp = inspect(sync_conn)
            if not insp.has_table("users"):
                return set()
            return {c["name"] for c in insp.get_columns("users")}

        cols = await conn.run_sync(_user_columns)
        if not cols:
            return

        if "oidc_sub" not in cols:
            if dialect == "postgresql":
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_sub VARCHAR(255)")
                )
            else:
                await conn.execute(text("ALTER TABLE users ADD COLUMN oidc_sub VARCHAR(255)"))
            log.info("init_db.added_column", table="users", column="oidc_sub")

        if dialect == "postgresql":
            await conn.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_oidc_sub "
                    "ON users (oidc_sub) WHERE oidc_sub IS NOT NULL"
                )
            )
        elif dialect == "sqlite":
            # SQLite create_all on fresh DBs already matches the model; skip ALTER.
            pass


async def bootstrap_admin() -> None:
    settings = get_settings()
    async with session_scope() as session:
        count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        if count > 0:
            log.info("init_db.bootstrap_skipped", reason="users_exist")
            return

        email = (settings.admin_email or "").strip().lower()
        password = settings.admin_password
        if not email or not password:
            log.error(
                "init_db.bootstrap_failed",
                reason="ADMIN_EMAIL and ADMIN_PASSWORD required on first boot",
            )
            raise SystemExit(
                "First boot: set ADMIN_EMAIL and ADMIN_PASSWORD in .env, then restart."
            )

        tenant_name = settings.admin_tenant_name or "Default"
        tenant = Tenant(
            name=tenant_name,
            slug=_slugify(tenant_name),
        )
        session.add(tenant)
        await session.flush()

        user = User(
            email=email,
            display_name=email.split("@", 1)[0],
            password_hash=hash_password(password),
            email_verified_at=datetime.now(tz=UTC),
        )
        session.add(user)
        await session.flush()

        session.add(
            Membership(
                user_id=user.id,
                tenant_id=tenant.id,
                role=MembershipRole.OWNER,
            )
        )
        log.info("init_db.bootstrap_done", email=email, tenant=tenant.slug)


async def run_init() -> None:
    configure_logging()
    await init_database()
    await bootstrap_admin()


def main() -> None:
    asyncio.run(run_init())


if __name__ == "__main__":
    main()
    sys.exit(0)
