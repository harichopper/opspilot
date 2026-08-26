from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from backend.app.memory import InMemoryStore, get_store
from backend.app.models import AgentExecutionRequest, AgentStep, ToolResult


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    needs_approval = "needs_approval"
    needs_attention = "needs_attention"
    cancelled = "cancelled"
    partially_completed = "partially_completed"
    completed = "completed"
    failed = "failed"


TERMINAL_STATUSES = {
    JobStatus.cancelled,
    JobStatus.completed,
    JobStatus.partially_completed,
    JobStatus.failed,
}


def _make_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobEvent:
    event_id: str
    job_id: str
    event_type: str
    message: str
    timestamp: str
    step_index: int | None = None
    tool_result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "job_id": self.job_id,
            "event_type": self.event_type,
            "message": self.message,
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "tool_result": self.tool_result,
            "metadata": self.metadata,
        }


@dataclass
class JobRecord:
    job_id: str
    project_id: str
    goal: str
    status: JobStatus
    github_owner: str | None
    github_repo: str | None
    request: dict[str, Any]
    current_step: str | None
    steps: list[dict[str, Any]]
    tools_used: list[dict[str, Any]]
    plan: list[str]
    events: list[JobEvent]
    report: str
    memory_updated: list[str]
    approval_requested_ids: list[str]
    approval_resolved_ids: list[str]
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    retry_count: int = 0
    checkpoint: dict[str, Any] = field(default_factory=dict)

    @property
    def checkpoints(self) -> dict[str, Any]:
        return self.checkpoint

    @checkpoints.setter
    def checkpoints(self, value: dict[str, Any]) -> None:
        self.checkpoint = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "goal": self.goal,
            "status": str(self.status),
            "github_owner": self.github_owner,
            "github_repo": self.github_repo,
            "request": self.request,
            "current_step": self.current_step,
            "steps": self.steps,
            "tools_used": self.tools_used,
            "plan": self.plan,
            "events": [e.to_dict() for e in self.events],
            "report": self.report,
            "memory_updated": self.memory_updated,
            "approval_requested_ids": self.approval_requested_ids,
            "approval_resolved_ids": self.approval_resolved_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "retry_count": self.retry_count,
            "checkpoint": self.checkpoint,
            "checkpoints": self.checkpoint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        cp = data.get("checkpoint") or data.get("checkpoints") or {}
        return cls(
            job_id=data["job_id"],
            project_id=data["project_id"],
            goal=data["goal"],
            status=JobStatus(data["status"]),
            github_owner=data.get("github_owner"),
            github_repo=data.get("github_repo"),
            request=data.get("request", {}),
            current_step=data.get("current_step"),
            steps=list(data.get("steps", [])),
            tools_used=list(data.get("tools_used", [])),
            plan=list(data.get("plan", [])),
            events=[
                JobEvent(
                    event_id=e["event_id"],
                    job_id=e["job_id"],
                    event_type=e["event_type"],
                    message=e["message"],
                    timestamp=e["timestamp"],
                    step_index=e.get("step_index"),
                    tool_result=e.get("tool_result"),
                    metadata=e.get("metadata", {}),
                )
                for e in data.get("events", [])
            ],
            report=data.get("report", ""),
            memory_updated=list(data.get("memory_updated", [])),
            approval_requested_ids=list(data.get("approval_requested_ids", [])),
            approval_resolved_ids=list(data.get("approval_resolved_ids", [])),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            retry_count=int(data.get("retry_count", 0)),
            checkpoint=dict(cp),
        )


class JobStore:
    """Firestore-compatible persistence layer for Jobs using InMemoryStore."""

    COLLECTION_JOBS = "opspilot_jobs"
    COLLECTION_EVENTS = "opspilot_job_events"

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self._store = store or get_store()

    def save(self, record: JobRecord) -> None:
        record.updated_at = _now_iso()
        self._store.set(self.COLLECTION_JOBS, record.job_id, record.to_dict())

    def get(self, job_id: str) -> JobRecord | None:
        data = self._store.get(self.COLLECTION_JOBS, job_id)
        if not data:
            return None
        return JobRecord.from_dict(data)

    def list_for_project(self, project_id: str, limit: int = 50) -> list[JobRecord]:
        docs = self._store.query(
            self.COLLECTION_JOBS,
            filters=[("project_id", "==", project_id)],
            order_by=("created_at", "desc"),
            limit=limit,
        )
        return [JobRecord.from_dict(d) for d in docs]

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        docs = self._store.query(
            self.COLLECTION_JOBS,
            order_by=("created_at", "desc"),
            limit=limit,
        )
        return [JobRecord.from_dict(d) for d in docs]

    def list_by_status(self, statuses: list[JobStatus]) -> list[JobRecord]:
        docs = self._store.query(
            self.COLLECTION_JOBS,
            filters=[("status", "in", [str(s) for s in statuses])],
            order_by=("created_at", "asc"),
        )
        return [JobRecord.from_dict(d) for d in docs]


class JobManager:
    """Job creation, state-machine transitions, and event logging."""

    def __init__(self, store: JobStore | None = None) -> None:
        self._store = store or JobStore()

    @staticmethod
    def project_id(owner: str | None, repo: str | None) -> str:
        if owner and repo:
            return f"github:{owner.lower()}/{repo.lower()}"
        return f"adhoc:{secrets.token_hex(6)}"

    def create(
        self,
        request: AgentExecutionRequest,
    ) -> JobRecord:
        job_id = _make_job_id()
        now = _now_iso()
        project_id = self.project_id(request.github_owner, request.github_repo)
        record = JobRecord(
            job_id=job_id,
            project_id=project_id,
            goal=request.goal,
            status=JobStatus.queued,
            github_owner=request.github_owner,
            github_repo=request.github_repo,
            request={
                "goal": request.goal,
                "github_owner": request.github_owner,
                "github_repo": request.github_repo,
            },
            current_step=None,
            steps=[],
            tools_used=[],
            plan=[],
            events=[],
            report="",
            memory_updated=[],
            approval_requested_ids=[],
            approval_resolved_ids=[],
            created_at=now,
            updated_at=now,
        )
        self._emit(record, "job.created", f"Job created with goal: {request.goal[:80]}")
        self._store.save(record)
        return record

    def transition(self, job: JobRecord, new_status: JobStatus, reason: str = "") -> JobRecord:
        old = job.status
        if old == new_status:
            return job
        if not self._is_valid_transition(old, new_status):
            raise ValueError(f"Invalid job state transition: {old} -> {new_status} (job_id={job.job_id}). Reason: {reason or 'none provided'}")
        job.status = new_status
        if new_status == JobStatus.running and job.started_at is None:
            job.started_at = _now_iso()
        if new_status in TERMINAL_STATUSES:
            job.completed_at = _now_iso()
        self._emit(job, "job.status_change", f"Status {old} -> {new_status}. {reason}".strip(), metadata={"from": str(old), "to": str(new_status)})
        self._store.save(job)
        return job

    def set_current_step(self, job: JobRecord, step_name: str) -> JobRecord:
        job.current_step = step_name
        self._emit(job, "step.begin", f"Step '{step_name}' started")
        self._store.save(job)
        return job

    def append_step(self, job: JobRecord, step: AgentStep) -> JobRecord:
        job.steps.append({"name": step.name, "status": step.status, "detail": step.detail})
        self._emit(job, "step.completed", f"[{step.status}] {step.name}: {step.detail}", step_index=len(job.steps) - 1)
        self._store.save(job)
        return job

    def append_tool_result(self, job: JobRecord, tool: ToolResult) -> JobRecord:
        entry = {
            "tool_name": tool.tool_name,
            "risk": str(tool.risk),
            "status": str(tool.status),
            "data": tool.data,
            "error": tool.error,
            "duration_ms": tool.duration_ms,
        }
        job.tools_used.append(entry)
        self._emit(
            job,
            "tool.executed",
            f"Tool {tool.tool_name}: {tool.status}",
            tool_result=entry,
            metadata={"tool": tool.tool_name, "tool_status": str(tool.status)},
        )
        self._store.save(job)
        return job

    def set_plan(self, job: JobRecord, plan: list[str]) -> JobRecord:
        job.plan = list(plan)
        self._emit(job, "plan.created", f"Plan with {len(plan)} steps created")
        self._store.save(job)
        return job

    def set_report(self, job: JobRecord, report: str) -> JobRecord:
        job.report = report
        self._emit(job, "report.updated", "Execution report updated")
        self._store.save(job)
        return job

    def set_checkpoint(self, job: JobRecord, key: str, value: Any) -> JobRecord:
        job.checkpoint[key] = value
        self._store.save(job)
        return job

    def write_checkpoint(self, job: JobRecord, key: str, value: Any) -> JobRecord:
        return self.set_checkpoint(job, key, value)

    def increment_retry(self, job: JobRecord, reason: str) -> JobRecord:
        job.retry_count += 1
        self._emit(job, "job.retry", f"Retry #{job.retry_count}: {reason}", metadata={"retries": job.retry_count})
        self._store.save(job)
        return job

    def set_error(self, job: JobRecord, error: str) -> JobRecord:
        job.error = error
        self._emit(job, "job.error", error)
        self._store.save(job)
        return job

    def add_memory_update(self, job: JobRecord, memory_id: str) -> JobRecord:
        if memory_id not in job.memory_updated:
            job.memory_updated.append(memory_id)
            self._store.save(job)
        return job

    def add_approval_requested(self, job: JobRecord, approval_id: str) -> JobRecord:
        if approval_id not in job.approval_requested_ids:
            job.approval_requested_ids.append(approval_id)
            self._store.save(job)
        return job

    def add_approval_resolved(self, job: JobRecord, approval_id: str) -> JobRecord:
        if approval_id not in job.approval_resolved_ids:
            job.approval_resolved_ids.append(approval_id)
            self._store.save(job)
        return job

    def cancel(self, job_or_id: JobRecord | str, reason: str = "User cancelled") -> JobRecord:
        if isinstance(job_or_id, JobRecord):
            job = job_or_id
        else:
            job = self._store.get(job_or_id)
            if job is None:
                raise ValueError(f"Job not found: {job_or_id}")
        return self.transition(job, JobStatus.cancelled, reason)

    def emit_event(
        self,
        job: JobRecord,
        event_type: str,
        message: str,
        step_index: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobEvent:
        self._emit(job, event_type, message, step_index=step_index, tool_result=data)
        self._store.save(job)
        return job.events[-1]

    def get(self, job_id: str) -> JobRecord | None:
        return self._store.get(job_id)

    def list(self, project_id: str | None = None, limit: int = 50) -> list[JobRecord]:
        if project_id is None:
            return self._store.list_recent(limit)
        return self._store.list_for_project(project_id, limit)

    def list_for_project(self, project_id: str, limit: int = 50) -> list[JobRecord]:
        return self._store.list_for_project(project_id, limit)

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        return self._store.list_recent(limit)

    @staticmethod
    def _is_valid_transition(old: JobStatus, new: JobStatus) -> bool:
        if old == new:
            return True
        if old in TERMINAL_STATUSES:
            return False
        if old == JobStatus.queued:
            return new in {JobStatus.running, JobStatus.cancelled, JobStatus.failed}
        if old == JobStatus.running:
            return new in {
                JobStatus.paused,
                JobStatus.needs_approval,
                JobStatus.needs_attention,
                JobStatus.cancelled,
                JobStatus.partially_completed,
                JobStatus.completed,
                JobStatus.failed,
            }
        if old == JobStatus.paused:
            return new in {JobStatus.running, JobStatus.cancelled, JobStatus.failed}
        if old == JobStatus.needs_approval:
            return new in {JobStatus.running, JobStatus.cancelled, JobStatus.failed}
        if old == JobStatus.needs_attention:
            return new in {JobStatus.running, JobStatus.cancelled, JobStatus.failed, JobStatus.partially_completed}
        return False

    @staticmethod
    def _emit(
        job: JobRecord,
        event_type: str,
        message: str,
        step_index: int | None = None,
        tool_result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = JobEvent(
            event_id=f"evt_{secrets.token_hex(8)}",
            job_id=job.job_id,
            event_type=event_type,
            message=message,
            timestamp=_now_iso(),
            step_index=step_index,
            tool_result=tool_result,
            metadata=metadata or {},
        )
        job.events.append(event)
