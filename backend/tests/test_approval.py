import time
import uuid

import pytest

from backend.app.config.settings import Settings
from backend.app.workflows.approval import (
    ApprovalRequest,
    ApprovalService,
    ApprovalStatus,
)
from backend.app.models import RiskLevel


@pytest.fixture
def svc() -> ApprovalService:
    return ApprovalService(Settings())


def _req(ttl_seconds: int = 300, risk=RiskLevel.medium) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=f"appr_{uuid.uuid4().hex[:10]}",
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        tool_args={"path": "app/settings.py"},
        risk=risk,
        reason="Modify app settings",
        requested_by="opspilot-orchestrator",
        requested_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:00:00+00:00",
        status=ApprovalStatus.requested,
        resolved_at=None,
        approved_by=None,
        rejected_by=None,
        resolution_note=None,
    )


def test_approval_request_and_approve(svc: ApprovalService) -> None:
    saved = svc.request(
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Modify app settings",
        tool_args={"path": "app/settings.py"},
    )
    assert saved.status == ApprovalStatus.requested
    approved = svc.approve(saved.approval_id, "judge", note="ok")
    assert approved is not None
    assert approved.status == ApprovalStatus.approved
    ok, appr = svc.validate_execution_allowed(saved.approval_id)
    assert ok is True
    assert appr is not None
    assert appr.status == ApprovalStatus.approved


def test_approval_reject(svc: ApprovalService) -> None:
    req = svc.request(
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Reject test",
    )
    svc.reject(req.approval_id, "judge", note="nope")
    ok, _ = svc.validate_execution_allowed(req.approval_id)
    assert ok is False


def test_approval_expiry_blocks_execution(svc: ApprovalService) -> None:
    req = svc.request(
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Expiry test",
        expires_seconds=0,
    )
    time.sleep(0.01)
    svc.approve(req.approval_id, "judge")
    ok, _ = svc.validate_execution_allowed(req.approval_id)
    assert ok is False


def test_approval_sweep_expired(svc: ApprovalService) -> None:
    old = svc.request(
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Sweep old",
        expires_seconds=0,
    )
    time.sleep(0.01)
    fresh = svc.request(
        job_id="job_x",
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Sweep fresh",
        expires_seconds=300,
    )
    expired = svc.sweep_expired()
    ids = {e.approval_id for e in expired}
    assert old.approval_id in ids
    assert fresh.approval_id not in ids


def test_find_pending_for_tool(svc: ApprovalService) -> None:
    job_id = "job_find_pending"
    r1 = svc.request(
        job_id=job_id,
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Pending 1",
    )
    r2 = svc.request(
        job_id=job_id,
        project_id="proj_x",
        tool_name="modify_file",
        risk=RiskLevel.medium,
        reason="Pending 2",
    )
    svc.approve(r2.approval_id, "judge")
    pending = svc.find_pending_for_tool(job_id, "modify_file")
    assert isinstance(pending, list)
    assert len(pending) == 1
    assert pending[0].approval_id == r1.approval_id
