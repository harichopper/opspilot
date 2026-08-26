from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from backend.app.config.logging import StructuredLogger
from backend.app.config.settings import Settings
from backend.app.memory import InMemoryStore, get_store
from backend.app.models import RiskLevel


DEFAULT_EXPIRY_SECONDS = 60 * 60 * 4


class ApprovalStatus(StrEnum):
    requested = "requested"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


@dataclass
class ApprovalRequest:
    approval_id: str
    job_id: str
    project_id: str
    tool_name: str
    risk: RiskLevel
    reason: str
    tool_args: dict[str, Any]
    status: ApprovalStatus
    requested_by: str
    approved_by: str | None
    rejected_by: str | None
    requested_at: str
    resolved_at: str | None
    expires_at: str
    resolution_note: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "tool_name": self.tool_name,
            "risk": str(self.risk),
            "reason": self.reason,
            "tool_args": self.tool_args,
            "status": str(self.status),
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "rejected_by": self.rejected_by,
            "requested_at": self.requested_at,
            "resolved_at": self.resolved_at,
            "expires_at": self.expires_at,
            "resolution_note": self.resolution_note,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        return cls(
            approval_id=data["approval_id"],
            job_id=data["job_id"],
            project_id=data["project_id"],
            tool_name=data["tool_name"],
            risk=RiskLevel(data["risk"]),
            reason=data["reason"],
            tool_args=data.get("tool_args", {}),
            status=ApprovalStatus(data["status"]),
            requested_by=data.get("requested_by", "opspilot-agent"),
            approved_by=data.get("approved_by"),
            rejected_by=data.get("rejected_by"),
            requested_at=data["requested_at"],
            resolved_at=data.get("resolved_at"),
            expires_at=data["expires_at"],
            resolution_note=data.get("resolution_note"),
            metadata=data.get("metadata", {}),
        )

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.requested

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return now >= expires


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_approval_id() -> str:
    return f"appr_{secrets.token_hex(8)}"


class ApprovalStore:
    COLLECTION = "opspilot_approvals"

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._store = store or get_store()

    def save(self, approval: ApprovalRequest) -> None:
        self._store.set(self.COLLECTION, approval.approval_id, approval.to_dict())

    def get(self, approval_id: str) -> ApprovalRequest | None:
        data = self._store.get(self.COLLECTION, approval_id)
        if not data:
            return None
        return ApprovalRequest.from_dict(data)

    def list_for_job(self, job_id: str) -> list[ApprovalRequest]:
        docs = self._store.query(
            self.COLLECTION,
            filters=[("job_id", "==", job_id)],
            order_by=("requested_at", "desc"),
        )
        return [ApprovalRequest.from_dict(d) for d in docs]

    def list_for_project(self, project_id: str, status_filter: list[ApprovalStatus] | None = None) -> list[ApprovalRequest]:
        filters: list[tuple[str, str, Any]] = [("project_id", "==", project_id)]
        docs = self._store.query(
            self.COLLECTION,
            filters=filters,
            order_by=("requested_at", "desc"),
        )
        approvals = [ApprovalRequest.from_dict(d) for d in docs]
        if status_filter:
            statuses = {str(s) for s in status_filter}
            approvals = [a for a in approvals if str(a.status) in statuses]
        return approvals

    def list_pending(self) -> list[ApprovalRequest]:
        docs = self._store.query(
            self.COLLECTION,
            filters=[("status", "==", str(ApprovalStatus.requested))],
            order_by=("requested_at", "asc"),
        )
        return [ApprovalRequest.from_dict(d) for d in docs]


class ApprovalService:
    """Create, approve, reject, expire, and validate approvals.

    The backend always re-checks `validate_execution_allowed` before actually
    calling a risky tool. Frontend buttons are not trusted.
    """

    def __init__(
        self,
        settings: Settings,
        store: ApprovalStore | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or ApprovalStore()
        self._logger = logger or StructuredLogger(settings, name="opspilot.approvals")

    def request(
        self,
        job_id: str,
        project_id: str,
        tool_name: str,
        risk: RiskLevel,
        reason: str,
        tool_args: dict[str, Any] | None = None,
        expires_seconds: int = DEFAULT_EXPIRY_SECONDS,
        requested_by: str = "opspilot-agent",
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=expires_seconds)).isoformat()
        approval = ApprovalRequest(
            approval_id=_make_approval_id(),
            job_id=job_id,
            project_id=project_id,
            tool_name=tool_name,
            risk=risk,
            reason=reason,
            tool_args=tool_args or {},
            status=ApprovalStatus.requested,
            requested_by=requested_by,
            approved_by=None,
            rejected_by=None,
            requested_at=now.isoformat(),
            resolved_at=None,
            expires_at=expires,
            metadata=metadata or {},
        )
        self._store.save(approval)
        self._logger.info(
            f"Approval requested for {tool_name} ({risk.value})",
            job_id=job_id,
            project_id=project_id,
            agent_step="approval.request",
            tool_name=tool_name,
            extra={"approval_id": approval.approval_id},
        )
        return approval

    def approve(self, approval_id: str, approver: str = "user", note: str | None = None) -> ApprovalRequest | None:
        approval = self._store.get(approval_id)
        if not approval:
            return None
        self._ensure_not_expired(approval)
        if approval.status == ApprovalStatus.requested:
            approval.status = ApprovalStatus.approved
            approval.approved_by = approver
            approval.resolved_at = _now_iso()
            approval.resolution_note = note
            self._store.save(approval)
            self._logger.info(
                f"Approval granted for {approval.tool_name}",
                job_id=approval.job_id,
                project_id=approval.project_id,
                agent_step="approval.approve",
                tool_name=approval.tool_name,
                extra={"approval_id": approval.approval_id, "approver": approver},
            )
        return approval

    def reject(self, approval_id: str, rejecter: str = "user", note: str | None = None) -> ApprovalRequest | None:
        approval = self._store.get(approval_id)
        if not approval:
            return None
        self._ensure_not_expired(approval)
        if approval.status == ApprovalStatus.requested:
            approval.status = ApprovalStatus.rejected
            approval.rejected_by = rejecter
            approval.resolved_at = _now_iso()
            approval.resolution_note = note
            self._store.save(approval)
            self._logger.warning(
                f"Approval rejected for {approval.tool_name}",
                job_id=approval.job_id,
                project_id=approval.project_id,
                agent_step="approval.reject",
                tool_name=approval.tool_name,
                extra={"approval_id": approval.approval_id, "rejecter": rejecter},
            )
        return approval

    def expire(self, approval_id: str) -> ApprovalRequest | None:
        approval = self._store.get(approval_id)
        if not approval:
            return None
        if approval.status == ApprovalStatus.requested:
            approval.status = ApprovalStatus.expired
            approval.resolved_at = _now_iso()
            self._store.save(approval)
        return approval

    def sweep_expired(self) -> list[ApprovalRequest]:
        pending = self._store.list_pending()
        swept: list[ApprovalRequest] = []
        for a in pending:
            if a.is_expired():
                expired = self.expire(a.approval_id)
                if expired is not None:
                    swept.append(expired)
        return swept

    def validate_execution_allowed(self, approval_id: str) -> tuple[bool, ApprovalRequest | None]:
        approval = self._store.get(approval_id)
        if not approval:
            return False, None
        self._ensure_not_expired(approval)
        if approval.status != ApprovalStatus.approved:
            return False, approval
        if approval.is_expired():
            return False, approval
        return True, approval

    def find_pending_for_tool(self, job_id: str, tool_name: str) -> list[ApprovalRequest]:
        matches: list[ApprovalRequest] = []
        for a in self._store.list_for_job(job_id):
            if a.tool_name == tool_name and a.status == ApprovalStatus.requested and not a.is_expired():
                matches.append(a)
        return matches

    def list_for_job(self, job_id: str) -> list[ApprovalRequest]:
        return self._store.list_for_job(job_id)

    def list_pending(self) -> list[ApprovalRequest]:
        self.sweep_expired()
        return self._store.list_pending()

    def list_for_project(self, project_id: str, status_filter: list[ApprovalStatus] | None = None) -> list[ApprovalRequest]:
        self.sweep_expired()
        return self._store.list_for_project(project_id, status_filter)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        approval = self._store.get(approval_id)
        if approval and approval.status == ApprovalStatus.requested and approval.is_expired():
            self.expire(approval_id)
            approval = self._store.get(approval_id)
        return approval

    @staticmethod
    def _ensure_not_expired(approval: ApprovalRequest) -> None:
        if approval.status == ApprovalStatus.requested and approval.is_expired():
            approval.status = ApprovalStatus.expired
            approval.resolved_at = _now_iso()
