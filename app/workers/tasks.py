from __future__ import annotations

import uuid as _uuid

import httpx

from app.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.schemas.failure_event import FailureEvent
from app.services.notifications.outbound_webhook import deliver_webhook_now
from app.services.pipeline import run_analysis_pipeline
from app.workers._async import run_async, worker_session_scope

configure_logging()
log = get_logger("worker")


@celery_app.task(
    name="app.workers.tasks.analyze_failure",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def analyze_failure(self, event_payload: dict) -> dict:
    async def _run() -> dict:
        event = FailureEvent.model_validate(event_payload)
        async with worker_session_scope() as session:
            result = await run_analysis_pipeline(session, event)
            analysis_id = str(result.id) if result else None
        return {
            "tenant_id": str(event.tenant_id),
            "ci_run_id": event.ci_run_id,
            "analysis_id": analysis_id,
            "provider": event.provider,
            "source": event.source,
        }

    log.info("worker.analyze_failure", run_id=event_payload.get("ci_run_id"))
    return run_async(_run)


@celery_app.task(
    name="app.workers.tasks.deliver_webhook",
    bind=True,
    autoretry_for=(httpx.HTTPError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def deliver_webhook(self, subscription_id: str, payload: dict) -> dict:
    async def _run() -> dict:
        async with worker_session_scope() as session:
            outcome = await deliver_webhook_now(
                session,
                _uuid.UUID(subscription_id),
                payload,
            )
            return {
                "subscription_id": subscription_id,
                "ok": outcome.ok,
                "status": outcome.status,
                "error": outcome.error,
            }

    return run_async(_run)
