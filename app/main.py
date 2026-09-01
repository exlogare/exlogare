from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    analyses_router,
    analyze_router,
    audit_router,
    auth_gitlab_router,
    auth_oidc_router,
    auth_router,
    clusters_public_router,
    clusters_router,
    demo_router,
    health_router,
    ingest_circleci_router,
    ingest_drone_router,
    ingest_generic_router,
    ingest_jenkins_router,
    ingest_teamcity_router,
    integrations_bitbucket_router,
    integrations_gitflic_router,
    integrations_gitlab_router,
    integrations_github_router,
    integrations_messengers_router,
    integrations_outbound_webhooks_router,
    capabilities_router,
    llm_router,
    public_api_router,
    public_router,
    stats_router,
    tenants_router,
    tokens_router,
    webhook_router,
)
from app.core.config import get_app_version, get_settings
from app.core.csrf import CSRFMiddleware
from app.core.logging import configure_logging, get_logger
from app.services.oauth.gitlab import GitLabOAuthRefreshFailed

_HEALTH_BYPASS_PATHS: frozenset[str] = frozenset({"/health", "/healthz"})


def _expand_localhost_aliases(host: str) -> set[str]:
    """localhost and 127.0.0.1 are interchangeable for local self-host installs."""
    if not host:
        return set()
    out = {host}
    if host == "localhost":
        out.add("127.0.0.1")
    elif host == "127.0.0.1":
        out.add("localhost")
    return out


def _host_from_url(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def _install_trusted_host_with_health_bypass(app: FastAPI, allowed: list[str]) -> None:
    import re

    host_patterns: list[re.Pattern[str]] = []
    for host in allowed:
        if host == "*":
            host_patterns.append(re.compile(r".*"))
            continue
        if host.startswith("*."):
            suffix = re.escape(host[2:])
            host_patterns.append(re.compile(rf"^(?:.+\.)?{suffix}$"))
        else:
            host_patterns.append(re.compile(rf"^{re.escape(host)}$"))

    @app.middleware("http")
    async def _trusted_host(request: Request, call_next):  # noqa: ANN202
        if request.url.path in _HEALTH_BYPASS_PATHS:
            return await call_next(request)
        host = request.headers.get("host", "").split(":")[0]
        if any(p.match(host) for p in host_patterns):
            return await call_next(request)
        return Response("Invalid host header", status_code=400)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ANN202, ARG001
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="Exlogare Community Edition",
        version=get_app_version(),
        description="Self-hosted Community Edition — CI/CD failure analyzer with OpenAI-compatible LLM",
        docs_url="/docs" if settings.app_env != "prod" else None,
        lifespan=_lifespan,
    )

    app.add_middleware(CSRFMiddleware)

    cors_origins: list[str] = [settings.web_base_url]
    if settings.app_env in ("dev", "test"):
        cors_origins.extend(
            [
                "http://localhost:5180",
                "http://127.0.0.1:5180",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:3000",
            ]
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(set(cors_origins)),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-Id",
            "X-CSRF-Token",
            "X-Tenant-ID",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )

    if settings.app_env == "prod":
        trusted: set[str] = set()
        for entry in settings.allowed_hosts:
            trusted.update(_expand_localhost_aliases(entry.split(":", 1)[0]))
        if not trusted:
            for url in (settings.web_base_url, settings.public_base_url):
                trusted.update(_expand_localhost_aliases(_host_from_url(url)))
        if trusted:
            trusted.add("api")
            _install_trusted_host_with_health_bypass(app, sorted(trusted))

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next) -> Response:
        corr_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=corr_id, path=request.url.path)
        response = await call_next(request)
        response.headers["x-request-id"] = corr_id
        structlog.contextvars.clear_contextvars()
        return response

    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(ingest_jenkins_router)
    app.include_router(ingest_circleci_router)
    app.include_router(ingest_teamcity_router)
    app.include_router(ingest_drone_router)
    app.include_router(ingest_generic_router)
    app.include_router(analyze_router)
    app.include_router(auth_router)
    app.include_router(auth_oidc_router)
    app.include_router(auth_gitlab_router)
    app.include_router(tenants_router)
    app.include_router(tokens_router)
    app.include_router(integrations_gitlab_router)
    app.include_router(integrations_github_router)
    app.include_router(integrations_bitbucket_router)
    app.include_router(integrations_gitflic_router)
    app.include_router(integrations_messengers_router)
    app.include_router(integrations_outbound_webhooks_router)
    app.include_router(capabilities_router)
    app.include_router(llm_router)
    app.include_router(public_router)
    app.include_router(public_api_router)
    app.include_router(stats_router)
    app.include_router(audit_router)
    app.include_router(analyses_router)
    app.include_router(clusters_router)
    app.include_router(clusters_public_router)
    app.include_router(demo_router)

    @app.exception_handler(GitLabOAuthRefreshFailed)
    async def _gitlab_oauth_refresh_failed(
        request: Request, exc: GitLabOAuthRefreshFailed
    ) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": exc.detail})

    log = get_logger("startup")
    log.info("app.ready", env=settings.app_env, selfhost=True)
    return app


app = create_app()
