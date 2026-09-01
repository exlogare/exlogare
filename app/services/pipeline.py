from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.analysis_result import AnalysisResult, Severity
from app.models.ci_connection import CIConnection, CIProvider
from app.models.failure_cluster import FailureCluster
from app.models.ingestion_event import IngestionEvent, IngestionSource
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.ai import get_analyzer
from app.models.tenant import Tenant
from app.services.audit import record_audit
from app.services.usage_stats import UsageStatsService
from app.services.ci.dispatch import (
    fetch_job_log_for_connection,
    publish_feedback_for_connection,
)
from app.services.ci.feedback_policy import resolve_feedback_policy
from app.services.ci.gitlab_client import GitLabClient
from app.services.clustering import (
    compute_cluster_fingerprint,
    compute_lookup_fingerprint,
    detect_service_tag,
    evaluate_cost_saver_decision,
    output_from_analysis_row,
    reuse_existing_analysis,
    upsert_cluster,
)
from app.services.notifications.outbound_webhook import schedule_webhook_fanout
from app.services.notifications.router import ChannelRouter
from app.services.processing import process_log_for_llm

log = get_logger(__name__)


async def persist_ingestion_event(
    session: AsyncSession, event: FailureEvent
) -> IngestionEvent | None:
    """Idempotently persist a raw ingestion event. Returns the row if newly created."""
    stmt = (
        insert(IngestionEvent)
        .values(
            tenant_id=event.tenant_id,
            ci_connection_id=event.ci_connection_id,
            source=IngestionSource(event.source),
            provider=event.provider,
            ci_run_id=event.ci_run_id,
            ci_job_id=event.ci_job_id or "",
            status="received",
            payload=event.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "provider", "ci_run_id"]
        )
        .returning(IngestionEvent.id)
    )
    row_id = (await session.execute(stmt)).scalar()
    if row_id is None:
        return None
    refetched = await session.execute(
        select(IngestionEvent).where(IngestionEvent.id == row_id)
    )
    return refetched.scalar_one()


def _build_outbound_payload(
    event: FailureEvent, analysis_row: AnalysisResult, analysis: AnalysisOutput
) -> dict:
    """Wire-shape ``analysis`` payload for outbound webhook subscribers."""
    return {
        "id": str(analysis_row.id),
        "provider": event.provider,
        "source": event.source,
        "ci_run_id": event.ci_run_id,
        "ci_job_id": event.ci_job_id or None,
        "project_id": event.project_id,
        "project_path": event.project_path,
        "project_web_url": event.project_web_url,
        "pipeline_url": event.pipeline_url,
        "job_url": analysis_row.job_url,
        "mr_iid": event.mr_iid,
        "root_cause": analysis.root_cause,
        "explanation": analysis.explanation,
        "fix_suggestion": analysis.fix_suggestion,
        "severity": analysis.severity,
        "confidence": analysis.confidence,
        "needs_more_context": analysis.needs_more_context,
        "missing_context_hint": analysis.missing_context_hint,
        "created_at": analysis_row.created_at.isoformat()
        if analysis_row.created_at
        else None,
    }


async def _fanout_outbound_webhooks(
    session: AsyncSession,
    event: FailureEvent,
    analysis_row: AnalysisResult,
    analysis: AnalysisOutput,
) -> None:
    """Schedule ``analysis.completed`` deliveries for the tenant's webhooks."""
    try:
        payload = _build_outbound_payload(event, analysis_row, analysis)
        await schedule_webhook_fanout(
            session, event.tenant_id, "analysis.completed", payload
        )
    except Exception as exc:
        log.warning(
            "pipeline.outbound_webhook_failed",
            error=str(exc),
            tenant_id=str(event.tenant_id),
        )


async def _resolve_analysis_with_clustering(
    session: AsyncSession,
    event: FailureEvent,
    log_excerpt: str,
    tenant: Tenant | None,
) -> tuple[AnalysisResult, AnalysisOutput, bool, FailureCluster | None]:
    """LLM call (or cluster reuse) + cluster bookkeeping in one place."""
    if not log_excerpt.strip() or tenant is None:
        analyzer = get_analyzer()
        analysis = await analyzer.analyze(event.project_path, log_excerpt)
        analysis_row = await persist_analysis(session, event, analysis)
        return analysis_row, analysis, False, None

    service_tag = detect_service_tag(log_excerpt)
    lookup_hash = compute_lookup_fingerprint(
        log_excerpt,
        provider=event.provider,
        service_tag=service_tag,
    )
    decision = await evaluate_cost_saver_decision(session, tenant, lookup_hash)

    reused = False
    analysis_row: AnalysisResult | None = None
    analysis: AnalysisOutput | None = None

    if decision.reuse_analysis_id is not None:
        prior = await reuse_existing_analysis(session, decision.reuse_analysis_id)
        if prior is not None:
            analysis = output_from_analysis_row(prior)
            analysis_row = prior
            reused = True
            log.info(
                "pipeline.cost_saver_reuse",
                tenant_id=str(event.tenant_id),
                lookup_hash=lookup_hash[:12],
                service_tag=service_tag,
                reused_analysis_id=str(prior.id),
                cluster_count=decision.cluster.count if decision.cluster else None,
            )

    if not reused:
        analyzer = get_analyzer()
        analysis = await analyzer.analyze(event.project_path, log_excerpt)
        analysis_row = await persist_analysis(session, event, analysis)
        log.debug(
            "pipeline.fresh_analysis",
            tenant_id=str(event.tenant_id),
            lookup_hash=lookup_hash[:12],
            service_tag=service_tag,
            decision=decision.reason,
        )

    assert analysis_row is not None and analysis is not None  # narrow for mypy

    cluster_hash = compute_cluster_fingerprint(
        lookup_hash, severity=analysis.severity
    )
    cluster = await upsert_cluster(
        session,
        tenant_id=event.tenant_id,
        fingerprint_hash=cluster_hash,
        lookup_hash=lookup_hash,
        last_root_cause=analysis.root_cause,
        last_severity=Severity(analysis.severity),
        last_analysis_id=analysis_row.id,
        seen_at=datetime.now(tz=timezone.utc),
    )

    return analysis_row, analysis, reused, cluster


async def run_analysis_pipeline(
    session: AsyncSession, event: FailureEvent
) -> AnalysisResult | None:
    """Unified processing pipeline used by webhook + polling ingestion."""

    connection = await _load_connection(session, event.ci_connection_id)
    log_text = ""
    try:
        log_text = await fetch_job_log_for_connection(connection, event)
    except Exception as exc:
        log.warning(
            "pipeline.log_fetch_failed",
            tenant_id=str(event.tenant_id),
            run_id=event.ci_run_id,
            error=str(exc),
        )

    excerpt = process_log_for_llm(log_text)

    tenant = await _load_tenant(session, event.tenant_id)
    analysis_row, analysis, reused, _cluster = await _resolve_analysis_with_clustering(
        session, event, excerpt, tenant
    )

    if analysis_row.feedback_ref:
        log.info(
            "pipeline.feedback_already_published",
            tenant_id=str(event.tenant_id),
            run_id=event.ci_run_id,
            ref=analysis_row.feedback_ref,
        )
    else:
        policy = resolve_feedback_policy(tenant, connection)
        try:
            if (
                connection.provider == CIProvider.GITLAB
                and event.provider == "gitlab"
            ):
                async with GitLabClient(connection) as client:
                    if (
                        not event.mr_iid
                        and event.project_id
                        and event.ci_run_id
                        and event.ci_run_id.isdigit()
                        and policy.get("mr_comment")
                    ):
                        try:
                            mrs = await client.list_pipeline_merge_requests(
                                event.project_id, event.ci_run_id
                            )
                            if mrs:
                                first_iid = mrs[0].get("iid")
                                if first_iid is not None:
                                    event.mr_iid = str(first_iid)
                        except Exception as exc:
                            log.info(
                                "pipeline.mr_lookup_failed",
                                tenant_id=str(event.tenant_id),
                                run_id=event.ci_run_id,
                                error=str(exc),
                            )
            result = await publish_feedback_for_connection(
                connection,
                event,
                analysis,
                policy,
                analysis_id=str(analysis_row.id),
            )
        except Exception as exc:
            result = None
            log.warning(
                "pipeline.feedback_failed",
                tenant_id=str(event.tenant_id),
                run_id=event.ci_run_id,
                error=str(exc),
            )

        if result and result.get("ref"):
            analysis_row.feedback_ref = str(result["ref"])
            session.add(analysis_row)
            await session.flush()
            status_check_meta = result.get("status_check") if isinstance(result, dict) else None
            await record_audit(
                session,
                tenant_id=event.tenant_id,
                action="feedback_published",
                target=f"{event.provider}:{event.ci_run_id}:{event.ci_job_id or '-'}",
                meta={
                    "channel": result.get("channel"),
                    "ref": result.get("ref"),
                    "url": result.get("url"),
                    "status_check": status_check_meta,
                },
            )
        else:
            await record_audit(
                session,
                tenant_id=event.tenant_id,
                action="feedback_skipped",
                target=f"{event.provider}:{event.ci_run_id}:{event.ci_job_id or '-'}",
                meta={
                    "policy": policy,
                    "has_mr": bool(event.mr_iid),
                    "has_sha": bool(event.sha),
                },
            )

    try:
        await ChannelRouter().dispatch(session, event.tenant_id, event, analysis)
    except Exception as exc:
        log.warning(
            "pipeline.notifications_failed", error=str(exc), tenant_id=str(event.tenant_id)
        )

    await _fanout_outbound_webhooks(session, event, analysis_row, analysis)

    usage_kind = "clustered_reuse" if reused else "analysis"
    usage = await UsageStatsService().record_run(
        session,
        tenant_id=event.tenant_id,
        provider=event.provider,
        ci_run_id=event.ci_run_id,
        ci_job_id=event.ci_job_id or "",
        kind=usage_kind,
    )
    await record_audit(
        session,
        tenant_id=event.tenant_id,
        action="ai_analysis_completed",
        target=f"{event.provider}:{event.ci_run_id}:{event.ci_job_id or '-'}",
        meta={
            "severity": analysis.severity,
            "confidence": analysis.confidence,
            "needs_more_context": analysis.needs_more_context,
            "usage_event_id": str(usage.id),
            "clustered_reuse": reused,
        },
    )
    return analysis_row


async def run_external_ingest(
    session: AsyncSession, event: FailureEvent, log_text: str
) -> AnalysisResult:
    """Generic ingest pipeline shared by all CI ingest endpoints."""
    excerpt = process_log_for_llm(log_text)

    tenant = await _load_tenant(session, event.tenant_id)
    analysis_row, analysis, reused, _cluster = await _resolve_analysis_with_clustering(
        session, event, excerpt, tenant
    )

    try:
        await ChannelRouter().dispatch(session, event.tenant_id, event, analysis)
    except Exception as exc:
        log.warning(
            "pipeline.notifications_failed",
            error=str(exc),
            tenant_id=str(event.tenant_id),
        )

    await _fanout_outbound_webhooks(session, event, analysis_row, analysis)

    usage_kind = "clustered_reuse" if reused else "analysis"
    usage = await UsageStatsService().record_run(
        session,
        tenant_id=event.tenant_id,
        provider=event.provider,
        ci_run_id=event.ci_run_id,
        ci_job_id=event.ci_job_id or "",
        kind=usage_kind,
    )
    await record_audit(
        session,
        tenant_id=event.tenant_id,
        action="ai_analysis_completed",
        target=f"{event.provider}:{event.ci_run_id}:{event.ci_job_id or '-'}",
        meta={
            "severity": analysis.severity,
            "confidence": analysis.confidence,
            "needs_more_context": analysis.needs_more_context,
            "usage_event_id": str(usage.id),
            "source": event.source,
            "clustered_reuse": reused,
        },
    )
    return analysis_row


def _derive_job_url(event: FailureEvent) -> str | None:
    """Best-effort deep link to a single job's console/logs page."""
    if event.job_url:
        return event.job_url
    if not event.ci_job_id:
        return None
    if event.provider == "gitlab" and event.project_web_url:
        return f"{event.project_web_url.rstrip('/')}/-/jobs/{event.ci_job_id}"
    return None


async def persist_analysis(
    session: AsyncSession, event: FailureEvent, analysis: AnalysisOutput
) -> AnalysisResult:
    job_url = _derive_job_url(event)
    stmt = (
        insert(AnalysisResult)
        .values(
            tenant_id=event.tenant_id,
            ci_connection_id=event.ci_connection_id,
            provider=event.provider,
            source=event.source,
            ci_run_id=event.ci_run_id,
            ci_job_id=event.ci_job_id or "",
            project_id=event.project_id,
            project_path=event.project_path,
            project_web_url=event.project_web_url,
            pipeline_url=event.pipeline_url,
            job_url=job_url,
            mr_iid=event.mr_iid,
            root_cause=analysis.root_cause,
            explanation=analysis.explanation,
            fix_suggestion=analysis.fix_suggestion,
            severity=Severity(analysis.severity),
            confidence=analysis.confidence,
            status="completed",
            raw_response=analysis.model_dump(mode="json"),
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "provider", "ci_run_id", "ci_job_id"],
            set_={
                "source": event.source,
                "project_path": event.project_path,
                "project_web_url": event.project_web_url,
                "pipeline_url": event.pipeline_url,
                "job_url": job_url,
                "root_cause": analysis.root_cause,
                "explanation": analysis.explanation,
                "fix_suggestion": analysis.fix_suggestion,
                "severity": Severity(analysis.severity),
                "confidence": analysis.confidence,
                "status": "completed",
                "raw_response": analysis.model_dump(mode="json"),
            },
        )
        .returning(AnalysisResult.id)
    )
    row_id = (await session.execute(stmt)).scalar()
    refetched = await session.execute(
        select(AnalysisResult).where(AnalysisResult.id == row_id)
    )
    return refetched.scalar_one()


async def _load_tenant(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Tenant | None:
    """Fetch the tenant row for feedback-policy resolution; None on miss."""
    row = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    return row.scalar_one_or_none()


async def _load_connection(
    session: AsyncSession, ci_connection_id: uuid.UUID | None
) -> CIConnection:
    if ci_connection_id is None:
        raise ValueError("ci_connection_id is required to run analysis pipeline")
    row = await session.execute(
        select(CIConnection).where(CIConnection.id == ci_connection_id)
    )
    conn = row.scalar_one_or_none()
    if conn is None:
        raise ValueError(f"CIConnection {ci_connection_id} not found")
    return conn
