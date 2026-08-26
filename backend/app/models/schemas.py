from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"
    blocked = "BLOCKED"


class ToolStatus(StrEnum):
    success = "success"
    error = "error"


class ToolResult(BaseModel):
    tool_name: str
    risk: RiskLevel
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    version: str = "0.2.0"


class AgentExecutionRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    github_owner: str | None = Field(default=None, min_length=1, max_length=100)
    github_repo: str | None = Field(default=None, min_length=1, max_length=100)
    demo_mode: bool = False
    auto_approve: bool = False


class AgentStep(BaseModel):
    name: str
    status: str
    detail: str


class AgentExecutionResponse(BaseModel):
    status: str
    goal: str
    agent_name: str
    model: str
    adk_available: bool
    steps: list[AgentStep]
    tools_used: list[ToolResult]
    plan: list[str]
    report: str


class JobStatusValue(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    needs_approval = "needs_approval"
    needs_attention = "needs_attention"
    cancelled = "cancelled"
    partially_completed = "partially_completed"
    completed = "completed"
    failed = "failed"


class ApprovalStatusValue(StrEnum):
    requested = "requested"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


class ProjectCreateRequest(BaseModel):
    github_owner: str = Field(min_length=1, max_length=100)
    github_repo: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    project_id: str
    github_owner: str
    github_repo: str
    display_name: str
    created_at: str
    last_job_status: str | None = None


class JobCreateRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    github_owner: str | None = Field(default=None, min_length=1, max_length=100)
    github_repo: str | None = Field(default=None, min_length=1, max_length=100)
    demo_mode: bool = False
    auto_approve: bool = False


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatusValue
    project_id: str
    goal: str


class JobEventResponse(BaseModel):
    event_id: str
    event_type: str
    message: str
    timestamp: str
    step_index: int | None = None
    tool_result: dict[str, Any] | None = None


class JobResponse(BaseModel):
    job_id: str
    project_id: str
    goal: str
    status: JobStatusValue
    github_owner: str | None
    github_repo: str | None
    current_step: str | None
    steps: list[dict[str, Any]]
    tools_used: list[dict[str, Any]]
    plan: list[str]
    events: list[JobEventResponse]
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
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    checkpoints: dict[str, Any] = Field(default_factory=dict)


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    count: int


class JobEventsListResponse(BaseModel):
    job_id: str
    events: list[JobEventResponse]
    count: int


class JobCancelResponse(BaseModel):
    job_id: str
    status: JobStatusValue
    cancelled: bool


class ApprovalRequestResponse(BaseModel):
    approval_id: str
    job_id: str
    project_id: str
    tool_name: str
    risk: RiskLevel
    reason: str
    status: ApprovalStatusValue
    requested_by: str
    approved_by: str | None
    rejected_by: str | None
    requested_at: str
    resolved_at: str | None
    expires_at: str
    resolution_note: str | None = None


class ApprovalActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    actor: str = Field(default="user", max_length=100)


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalRequestResponse]
    count: int


class MemoryWriteRequest(BaseModel):
    memory_type: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=5000)
    source: str = Field(default="api", max_length=100)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class MemoryEntryResponse(BaseModel):
    memory_id: str
    project_id: str
    type: str
    content: str
    source: str
    confidence: float
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryListResponse(BaseModel):
    project_id: str
    entries: list[MemoryEntryResponse]
    count: int


class ToolSpec(BaseModel):
    name: str
    risk: RiskLevel
    needs_approval: bool


class ToolListResponse(BaseModel):
    tools: list[ToolSpec]
    count: int


class DemoStartResponse(BaseModel):
    job_id: str
    demo_mode: bool
    seeded_issues: int
    seeded_tests_failing: int
    goal: str
