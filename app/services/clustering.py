"""Failure clustering — fingerprint, UPSERT, and cost-saver policy."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.analysis_result import AnalysisResult, Severity
from app.models.failure_cluster import ClusterStatus, FailureCluster
from app.models.tenant import Tenant
from app.schemas.analysis import AnalysisOutput

log = get_logger(__name__)


_NORMALISE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ISO-8601-ish timestamps
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    # UUIDs
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE), "<UUID>"),
    # SHA / hex blobs (>= 8 hex chars, e.g. commit hashes, build ids)
    (re.compile(r"\b[0-9a-f]{8,64}\b", re.IGNORECASE), "<HEX>"),
    # Quoted paths / temp files
    (re.compile(r"/tmp/[^\s'\"]+"), "<TMP>"),
    # Long numbers (build ids, ports, durations)
    (re.compile(r"\b\d{3,}\b"), "<N>"),
)

_FINGERPRINT_TAIL_CHARS = 4000

_SERVICE_TAG_TAIL_CHARS = 2000

_FINGERPRINT_VERSION = "v2"

_SERVICE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Databases — most-specific (driver names) before generic.
    ("postgres", ("psycopg", "postgresql", "postgres", "pg_dump", "libpq")),
    ("mssql", ("pyodbc", "sqlserver", "mssql", "tds_")),
    ("oracle", (" ora-", "oracledb", "cx_oracle")),
    ("mysql", ("mysqldb", "mariadb", "mysql")),
    ("mongodb", ("pymongo", "mongo:", "mongodb")),
    ("redis", ("redis-cli", "redis_", " redis ", "redis:")),
    ("elasticsearch", ("elasticsearch", "opensearch")),
    ("kafka", ("kafkajs", "kafka.")),
    ("rabbitmq", ("rabbitmq", "amqp:")),
    # Containers / orchestration.
    ("kubernetes", ("kubectl", "kubernetes", " k8s ", "minikube")),
    ("docker", ("dockerfile", "docker:", " docker ", "containerd", "podman")),
    # Build / package managers.
    ("npm", ("npm err!", "npm error", " npm ")),
    ("yarn", (" yarn ", "yarn.lock")),
    ("pip", (" pip ", "pip install", "pypi.org")),
    ("poetry", ("poetry ", "pyproject.toml")),
    ("gradle", ("gradlew", " gradle ")),
    ("maven", (" mvn ", "maven-")),
    ("cargo", (" cargo ",)),
    ("go-modules", ("go mod ", "go.sum")),
    # Test frameworks.
    ("pytest", ("pytest",)),
    ("jest", (" jest ", "jest.config")),
    ("junit", ("junit",)),
    ("rspec", ("rspec",)),
    ("go-test", ("go test", "testing.t")),
)


def _normalise_text(text: str) -> str:
    """Strip volatile bits, lowercase, collapse whitespace."""
    text = (text or "").strip()
    for pattern, replacement in _NORMALISE_PATTERNS:
        text = pattern.sub(replacement, text)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text


def detect_service_tag(log_text: str | None) -> str:
    """Identify the top-level service / runtime mentioned in the failure."""
    if not log_text:
        return ""
    tail = log_text[-_SERVICE_TAG_TAIL_CHARS:].lower()
    for tag, keywords in _SERVICE_KEYWORDS:
        if any(kw in tail for kw in keywords):
            return tag
    return ""


def compute_lookup_fingerprint(
    log_excerpt: str,
    *,
    provider: str,
    service_tag: str,
) -> str:
    """Stable pre-LLM hash used as the cost-saver lookup key."""
    normalised = _normalise_text(log_excerpt)
    if len(normalised) > _FINGERPRINT_TAIL_CHARS:
        normalised = normalised[-_FINGERPRINT_TAIL_CHARS:]
    payload = "|".join(
        (_FINGERPRINT_VERSION, (provider or "").lower(), (service_tag or "").lower(), normalised)
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def compute_cluster_fingerprint(lookup_hash: str, *, severity: str) -> str:
    """Cluster-row identity hash. Adds severity on top of the lookup hash."""
    return hashlib.sha1(f"{lookup_hash}|{(severity or '').lower()}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClusterDecision:
    """Outcome of :func:`evaluate_cost_saver_decision`."""

    cluster: FailureCluster | None
    """The matching cluster row at decision time, if any."""

    reuse_analysis_id: uuid.UUID | None
    """When set, the pipeline MUST skip the LLM call and reuse this id."""

    reason: str
    """Short tag for structured logs / metrics. ``new``, ``cluster_stale``, ``reuse_within_ttl``, …"""


async def evaluate_cost_saver_decision(
    session: AsyncSession,
    tenant: Tenant,
    lookup_hash: str,
) -> ClusterDecision:
    """Should the pipeline reuse the most recent analysis for this lookup hash?"""
    cluster = await _load_cluster_by_lookup(session, tenant.id, lookup_hash)
    if cluster is None:
        return ClusterDecision(None, None, "new")

    if not bool(getattr(tenant, "cost_saver_enabled", True)):
        return ClusterDecision(cluster, None, "tenant_disabled")

    if cluster.last_analysis_id is None:
        return ClusterDecision(cluster, None, "no_last_analysis")

    if cluster.count < 1:
        return ClusterDecision(cluster, None, "single_occurrence")

    settings = get_settings()
    ttl = timedelta(hours=max(0, settings.cost_saver_ttl_hours))
    now = datetime.now(tz=timezone.utc)
    last_seen = cluster.last_seen_at
    if last_seen is None:
        return ClusterDecision(cluster, None, "no_last_seen")
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    if now - last_seen > ttl:
        return ClusterDecision(cluster, None, "cluster_stale")

    return ClusterDecision(
        cluster,
        cluster.last_analysis_id,
        "reuse_within_ttl",
    )


async def upsert_cluster(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    fingerprint_hash: str,
    lookup_hash: str,
    last_root_cause: str,
    last_severity: Severity,
    last_analysis_id: uuid.UUID,
    seen_at: datetime,
) -> FailureCluster:
    """Increment the cluster row for this fingerprint (or create it)."""
    stmt = (
        insert(FailureCluster)
        .values(
            tenant_id=tenant_id,
            fingerprint_hash=fingerprint_hash,
            lookup_hash=lookup_hash,
            last_root_cause=last_root_cause,
            last_severity=last_severity,
            count=1,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            last_analysis_id=last_analysis_id,
            status=ClusterStatus.ACTIVE,
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "fingerprint_hash"],
            set_={
                "count": FailureCluster.count + 1,
                "last_seen_at": seen_at,
                "lookup_hash": lookup_hash,
                "last_root_cause": last_root_cause,
                "last_severity": last_severity,
                "last_analysis_id": last_analysis_id,
                # Regression handling:
                "status": case(
                    (
                        FailureCluster.status == ClusterStatus.RESOLVED,
                        ClusterStatus.ACTIVE.value,
                    ),
                    else_=FailureCluster.status,
                ),
                "resolved_at": case(
                    (
                        FailureCluster.status == ClusterStatus.RESOLVED,
                        None,
                    ),
                    else_=FailureCluster.resolved_at,
                ),
                "updated_at": seen_at,
            },
        )
        .returning(FailureCluster.id)
    )
    row_id = (await session.execute(stmt)).scalar()
    refetched = await session.execute(
        select(FailureCluster).where(FailureCluster.id == row_id)
    )
    cluster = refetched.scalar_one()
    log.debug(
        "clustering.upsert",
        tenant_id=str(tenant_id),
        fingerprint=fingerprint_hash[:12],
        count=cluster.count,
        status=cluster.status.value,
    )
    return cluster


async def reuse_existing_analysis(
    session: AsyncSession, analysis_id: uuid.UUID
) -> AnalysisResult | None:
    """Re-fetch an analysis row referenced by a cluster."""
    refetched = await session.execute(
        select(AnalysisResult).where(AnalysisResult.id == analysis_id)
    )
    return refetched.scalar_one_or_none()


def output_from_analysis_row(row: AnalysisResult) -> AnalysisOutput:
    """Reconstruct an :class:`AnalysisOutput` from a persisted row."""
    raw = row.raw_response or {}
    severity = (
        row.severity.value if isinstance(row.severity, Severity) else row.severity
    )
    return AnalysisOutput(
        root_cause=row.root_cause,
        explanation=row.explanation,
        fix_suggestion=row.fix_suggestion,
        severity=severity,  # type: ignore[arg-type]
        confidence=row.confidence,
        needs_more_context=bool(raw.get("needs_more_context", False)),
        missing_context_hint=raw.get("missing_context_hint"),
    )


async def _load_cluster_by_lookup(
    session: AsyncSession, tenant_id: uuid.UUID, lookup_hash: str
) -> FailureCluster | None:
    """Fetch the most recent cluster row sharing this lookup hash."""
    row = await session.execute(
        select(FailureCluster)
        .where(
            FailureCluster.tenant_id == tenant_id,
            FailureCluster.lookup_hash == lookup_hash,
        )
        .order_by(FailureCluster.last_seen_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()
