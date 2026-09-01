from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_admin
from app.models.ci_connection import CIProvider
from app.schemas.analysis import AnalysisOutput
from app.schemas.failure_event import FailureEvent
from app.services.ai import get_analyzer
from app.services.notifications.router import ChannelRouter
from app.services.processing import process_log_for_llm

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoRequest(BaseModel):
    scenario: str = "npm_install_eacces"


class DemoNotificationResult(BaseModel):
    connection_id: str
    channel: str
    ok: bool


class DemoResponse(BaseModel):
    analysis: dict
    notifications: list[DemoNotificationResult]


SCENARIOS: dict[str, str] = {
    "npm_install_eacces": """\
$ npm ci
npm ERR! code EACCES
npm ERR! syscall open
npm ERR! path /usr/local/lib/node_modules
npm ERR! errno -13
npm ERR! Error: EACCES: permission denied, open '/usr/local/lib/node_modules/.package-lock.json'
npm ERR! 
npm ERR!     sudo -H -u root npm install -g
ERROR: Job failed: exit code 1
""",
    "test_assertion": """\
FAIL tests/test_user.py::test_login_sets_session
    assert response.status_code == 200
E   assert 500 == 200
E    +  where 500 = <Response [500]>.status_code

================= 1 failed, 42 passed in 2.15s =================
ERROR: Job failed: command terminated with exit code 1
""",
    "pip_timeout": """\
Collecting pandas==2.2.2
  Downloading pandas-2.2.2.tar.gz (4.3 MB)
     --- 0.0/4.3 MB ---- 0 bytes/s eta --:--:--
WARNING: Retrying (Retry(total=4)) after connection broken by 'TimeoutError
ERROR: Could not install packages due to an OSError: HTTPSConnectionPool(host='pypi.org', port=443): Read timed out.
ERROR: Job failed: exit code 1
""",
}


@router.post("/rca", response_model=DemoResponse)
async def demo_rca(
    body: DemoRequest,
    principal: CurrentPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> DemoResponse:
    """Runs the full RCA pipeline on a canned CI log and dispatches the summary"""
    raw_log = SCENARIOS.get(body.scenario) or SCENARIOS["npm_install_eacces"]
    excerpt = process_log_for_llm(raw_log)
    analyzer = get_analyzer()
    analysis: AnalysisOutput = await analyzer.analyze("demo/project", excerpt)

    demo_event = FailureEvent(
        tenant_id=principal.tenant.id,
        ci_connection_id=None,
        provider="gitlab",
        source="manual",
        ci_run_id=f"demo-{uuid.uuid4().hex[:8]}",
        project_path="demo/project",
        pipeline_url=None,
        status="failed",
        occurred_at=datetime.now(tz=timezone.utc),
    )
    notifications: list = []
    try:
        notifications = await ChannelRouter().dispatch(
            session, principal.tenant.id, demo_event, analysis
        )
    except Exception:
        notifications = []

    _ = CIProvider  # keep import
    return DemoResponse(
        analysis=analysis.model_dump(mode="json"),
        notifications=[DemoNotificationResult(**n) for n in notifications],
    )
