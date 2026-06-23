from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FailureEvent(BaseModel):
    """Unified failure event envelope used by every ingestion source."""

    tenant_id: uuid.UUID
    ci_connection_id: uuid.UUID | None = None

    provider: Literal[
        "gitlab",
        "github",
        "bitbucket",
        "gitflic",
        "jenkins",
        "circleci",
        "teamcity",
        "drone",
        "generic",
    ]
    source: Literal[
        "gitlab_webhook",
        "gitlab_poll",
        "github_webhook",
        "github_poll",
        "bitbucket_webhook",
        "bitbucket_poll",
        "gitflic_webhook",
        "gitflic_poll",
        "jenkins_webhook",
        "jenkins_ingest",
        "circleci_ingest",
        "teamcity_ingest",
        "drone_ingest",
        "generic_ingest",
        "manual",
    ]

    ci_run_id: str
    ci_job_id: str | None = None
    project_id: str | None = None
    project_path: str | None = None
    project_web_url: str | None = None

    pipeline_url: str | None = None
    job_url: str | None = None
    mr_iid: str | None = None

    status: str = "failed"
    ref: str | None = None
    sha: str | None = None
    actor: str | None = None

    occurred_at: datetime = Field(default_factory=datetime.utcnow)

    raw: dict = Field(default_factory=dict)
