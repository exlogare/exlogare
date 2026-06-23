"""Periodic worker heartbeat."""
from __future__ import annotations

import time

from app.celery_app import celery_app
from app.core.logging import configure_logging, get_logger
from app.workers._async import run_async

configure_logging()
log = get_logger("heartbeat")

_WORKER_HEARTBEAT_KEY = "heartbeat:worker"
_WORKER_HEARTBEAT_TTL_SECS = 120


@celery_app.task(
    name="app.workers.heartbeat.write_worker_heartbeat",
    acks_late=False,
    max_retries=0,
)
def write_worker_heartbeat() -> dict:
    return run_async(_write_worker_heartbeat)


async def _write_worker_heartbeat() -> dict:
    from app.core.redis import get_redis

    ts = time.time()
    try:
        redis = get_redis()
        await redis.set(_WORKER_HEARTBEAT_KEY, f"{ts:.3f}", ex=_WORKER_HEARTBEAT_TTL_SECS)
        return {"ok": True, "ts": ts}
    except Exception as exc:  # noqa: BLE001
        log.warning("heartbeat.write_failed", error=str(exc))
        return {"ok": False, "error": str(exc)}
