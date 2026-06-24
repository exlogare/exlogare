from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "exlogare-selfhost",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks",
        "app.workers.polling",
        "app.workers.retention",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.analyze_failure": {"queue": "analysis"},
        "app.workers.tasks.deliver_webhook": {"queue": "notifications"},
        "app.workers.polling.poll_all_oauth_tenants": {"queue": "default"},
        "app.workers.retention.enforce_retention": {"queue": "default"},
    },
    beat_schedule={
        "poll-oauth-tenants": {
            "task": "app.workers.polling.poll_all_oauth_tenants",
            "schedule": max(settings.poll_interval_seconds, 15),
        },
        "enforce-retention-daily": {
            "task": "app.workers.retention.enforce_retention",
            "schedule": crontab(hour=3, minute=17),
        },
    },
)
