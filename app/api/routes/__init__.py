from app.api.routes.analyses import router as analyses_router
from app.api.routes.analyze import router as analyze_router
from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.auth_gitlab import router as auth_gitlab_router
from app.api.routes.capabilities import router as plan_router
from app.api.routes.clusters import (
    public_router as clusters_public_router,
    router as clusters_router,
)
from app.api.routes.demo import router as demo_router
from app.api.routes.health import router as health_router
from app.api.routes.ingest_circleci import router as ingest_circleci_router
from app.api.routes.ingest_drone import router as ingest_drone_router
from app.api.routes.ingest_generic import router as ingest_generic_router
from app.api.routes.ingest_jenkins import router as ingest_jenkins_router
from app.api.routes.ingest_teamcity import router as ingest_teamcity_router
from app.api.routes.integrations_bitbucket import router as integrations_bitbucket_router
from app.api.routes.integrations_gitflic import router as integrations_gitflic_router
from app.api.routes.integrations_gitlab import router as integrations_gitlab_router
from app.api.routes.integrations_github import router as integrations_github_router
from app.api.routes.integrations_messengers import router as integrations_messengers_router
from app.api.routes.integrations_outbound_webhooks import (
    router as integrations_outbound_webhooks_router,
)
from app.api.routes.public import router as public_router
from app.api.routes.public_api import router as public_api_router
from app.api.routes.stats import router as stats_router
from app.api.routes.tenants import router as tenants_router
from app.api.routes.tokens import router as tokens_router
from app.api.routes.webhook import router as webhook_router

__all__ = [
    "analyses_router",
    "analyze_router",
    "audit_router",
    "auth_router",
    "auth_gitlab_router",
    "clusters_public_router",
    "clusters_router",
    "demo_router",
    "health_router",
    "integrations_bitbucket_router",
    "integrations_gitflic_router",
    "integrations_gitlab_router",
    "integrations_github_router",
    "integrations_messengers_router",
    "integrations_outbound_webhooks_router",
    "plan_router",
    "public_router",
    "public_api_router",
    "stats_router",
    "tenants_router",
    "tokens_router",
    "webhook_router",
    "ingest_jenkins_router",
    "ingest_circleci_router",
    "ingest_teamcity_router",
    "ingest_drone_router",
    "ingest_generic_router",
]
