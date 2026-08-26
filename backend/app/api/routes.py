from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.agents import OpsPilotOrchestrator
from backend.app.config.settings import Settings, get_settings
from backend.app.memory import VALID_MEMORY_TYPES, MemoryEntry, MemoryService
from backend.app.models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    ApprovalActionRequest,
    ApprovalListResponse,
    ApprovalRequestResponse,
    ApprovalStatusValue,
    DemoStartResponse,
    HealthResponse,
    JobCancelResponse,
    JobCreatedResponse,
    JobCreateRequest,
    JobEventResponse,
    JobEventsListResponse,
    JobListResponse,
    JobResponse,
    JobStatusValue,
    MemoryEntryResponse,
    MemoryListResponse,
    MemoryWriteRequest,
    ProjectCreateRequest,
    ProjectResponse,
    ToolListResponse,
    ToolSpec,
)
from backend.app.policies import PolicyEngine
from backend.app.tools import GitHubToolkit
from backend.app.workflows import ApprovalRequest, ApprovalService, JobRecord
from backend.app.workflows.demo import (
    DEMO_SEEDED_FAILURES_COUNT,
    DEMO_SEEDED_ISSUES_COUNT,
)


router = APIRouter()


# Module-level singletons — created once per process, shared across all requests.
# lru_cache cannot be used here because Pydantic Settings objects are not hashable.
_settings_singleton: Settings | None = None
_orchestrator_singleton: OpsPilotOrchestrator | None = None
_policy_singleton: PolicyEngine | None = None


def _get_settings_singleton() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = get_settings()
    return _settings_singleton


def get_orchestrator(settings: Settings = Depends(get_settings)) -> OpsPilotOrchestrator:
    global _orchestrator_singleton
    if _orchestrator_singleton is None:
        _orchestrator_singleton = OpsPilotOrchestrator(settings)
    return _orchestrator_singleton


def get_memory_service(settings: Settings = Depends(get_settings)) -> MemoryService:
    return get_orchestrator(settings).memory_service()


def get_approval_service(settings: Settings = Depends(get_settings)) -> ApprovalService:
    return get_orchestrator(settings).approval_service()


def get_policy_engine() -> PolicyEngine:
    global _policy_singleton
    if _policy_singleton is None:
        _policy_singleton = PolicyEngine()
    return _policy_singleton


def _job_to_response(record: JobRecord) -> JobResponse:
    cp = dict(record.checkpoint)
    return JobResponse(
        job_id=record.job_id,
        project_id=record.project_id,
        goal=record.goal,
        status=JobStatusValue(str(record.status)),
        github_owner=record.github_owner,
        github_repo=record.github_repo,
        current_step=record.current_step,
        steps=list(record.steps),
        tools_used=list(record.tools_used),
        plan=list(record.plan),
        events=[
            JobEventResponse(
                event_id=e.event_id,
                event_type=e.event_type,
                message=e.message,
                timestamp=e.timestamp,
                step_index=e.step_index,
                tool_result=e.tool_result,
            )
            for e in record.events
        ],
        report=record.report,
        memory_updated=list(record.memory_updated),
        approval_requested_ids=list(record.approval_requested_ids),
        approval_resolved_ids=list(record.approval_resolved_ids),
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=record.error,
        retry_count=record.retry_count,
        checkpoint=cp,
        checkpoints=cp,
    )


def _approval_to_response(a: ApprovalRequest) -> ApprovalRequestResponse:
    return ApprovalRequestResponse(
        approval_id=a.approval_id,
        job_id=a.job_id,
        project_id=a.project_id,
        tool_name=a.tool_name,
        risk=a.risk,
        reason=a.reason,
        status=ApprovalStatusValue(str(a.status)),
        requested_by=a.requested_by,
        approved_by=a.approved_by,
        rejected_by=a.rejected_by,
        requested_at=a.requested_at,
        resolved_at=a.resolved_at,
        expires_at=a.expires_at,
        resolution_note=a.resolution_note,
    )


def _memory_to_response(m: MemoryEntry) -> MemoryEntryResponse:
    return MemoryEntryResponse(
        memory_id=m.memory_id,
        project_id=m.project_id,
        type=m.type,
        content=m.content,
        source=m.source,
        confidence=m.confidence,
        created_at=m.created_at,
        updated_at=m.updated_at,
        metadata=m.metadata,
    )


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        environment=settings.environment,
        version="0.2.0",
    )


@router.post("/api/agent/execute", response_model=AgentExecutionResponse)
async def execute_agent(
    request: AgentExecutionRequest,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> AgentExecutionResponse:
    return await orchestrator.execute_agent_endpoint(request)


@router.post("/api/projects", response_model=ProjectResponse)
async def create_project(
    request: ProjectCreateRequest,
    memory: MemoryService = Depends(get_memory_service),
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> ProjectResponse:
    project_id = memory.project_id_for(request.github_owner, request.github_repo)
    display_name = request.display_name or f"{request.github_owner}/{request.github_repo}"
    created_at = datetime.now(timezone.utc).isoformat()
    memory.write(
        project_id,
        "project_convention",
        f"Project display name: {display_name}.",
        source="project_create",
        confidence=0.9,
    )
    last_job = None
    jobs = orchestrator.list_jobs_for_project(request.github_owner, request.github_repo, limit=1)
    if jobs:
        last_job = str(jobs[0].status)
    return ProjectResponse(
        project_id=project_id,
        github_owner=request.github_owner,
        github_repo=request.github_repo,
        display_name=display_name,
        created_at=created_at,
        last_job_status=last_job,
    )


@router.get("/api/projects/{project_id:path}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    memory: MemoryService = Depends(get_memory_service),
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> ProjectResponse:
    entries = memory.list_all(project_id)
    if project_id.startswith("github:"):
        rest = project_id[len("github:"):]
        if "/" in rest:
            owner, repo = rest.split("/", 1)
        else:
            owner, repo = rest, ""
    else:
        raise HTTPException(status_code=404, detail="Project not found.")
    last_status: str | None = None
    jobs = orchestrator.list_jobs_for_project(owner, repo, limit=1)
    if jobs:
        last_status = str(jobs[0].status)
    created = entries[-1].created_at if entries else datetime.now(timezone.utc).isoformat()
    display = f"{owner}/{repo}"
    for e in entries:
        if "display name" in e.content and "Project" in e.content:
            pass
    return ProjectResponse(
        project_id=project_id,
        github_owner=owner,
        github_repo=repo,
        display_name=display,
        created_at=created,
        last_job_status=last_status,
    )


@router.post("/api/jobs", response_model=JobCreatedResponse, status_code=202)
async def create_job(
    request: "JobCreateRequest",
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> JobCreatedResponse:
    goal = request.goal
    owner = request.github_owner
    repo = request.github_repo
    if request.project_id and not owner:
        if request.project_id.startswith("github:"):
            rest = request.project_id[len("github:"):]
            if "/" in rest:
                owner, repo = rest.split("/", 1)
    agent_req = AgentExecutionRequest(
        goal=goal,
        github_owner=owner,
        github_repo=repo,
        demo_mode=bool(request.demo_mode),
        auto_approve=bool(request.auto_approve),
    )
    job = await orchestrator.start_job(agent_req, background=True)
    return JobCreatedResponse(
        job_id=job.job_id,
        status=JobStatusValue(str(job.status)),
        project_id=job.project_id,
        goal=job.goal,
    )


@router.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> JobResponse:
    job = orchestrator.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_response(job)


@router.post("/api/jobs/{job_id}/cancel", response_model=JobCancelResponse)
async def cancel_job(
    job_id: str,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> JobCancelResponse:
    job = orchestrator.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobCancelResponse(
        job_id=job.job_id,
        status=JobStatusValue(str(job.status)),
        cancelled=job.status == "cancelled",
    )


@router.get("/api/jobs/{job_id}/events", response_model=JobEventsListResponse)
async def get_job_events(
    job_id: str,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> JobEventsListResponse:
    job = orchestrator.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    events = [
        JobEventResponse(
            event_id=e.event_id,
            event_type=e.event_type,
            message=e.message,
            timestamp=e.timestamp,
            step_index=e.step_index,
            tool_result=e.tool_result,
        )
        for e in job.events
    ]
    return JobEventsListResponse(job_id=job.job_id, events=events, count=len(events))


@router.get("/api/jobs", response_model=JobListResponse)
async def list_jobs(
    project_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> JobListResponse:
    if project_id and project_id.startswith("github:"):
        rest = project_id[len("github:"):]
        if "/" in rest:
            owner, repo = rest.split("/", 1)
            jobs = orchestrator.list_jobs_for_project(owner, repo, limit=limit)
        else:
            jobs = orchestrator.list_recent_jobs(limit=limit)
    else:
        jobs = orchestrator.list_recent_jobs(limit=limit)
    return JobListResponse(jobs=[_job_to_response(j) for j in jobs], count=len(jobs))


@router.get("/api/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> ApprovalListResponse:
    service = orchestrator.approval_service()
    status_filter = None
    if status:
        status_filter = [ApprovalStatusValue(status)]
    if project_id:
        approvals = service.list_for_project(project_id, status_filter=status_filter)
    else:
        approvals = service.list_pending()
    return ApprovalListResponse(
        approvals=[_approval_to_response(a) for a in approvals],
        count=len(approvals),
    )


@router.post("/api/approvals/{approval_id}/approve", response_model=ApprovalRequestResponse)
async def approve_approval(
    approval_id: str,
    payload: ApprovalActionRequest,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> ApprovalRequestResponse:
    service = orchestrator.approval_service()
    result = service.approve(approval_id, approver=payload.actor, note=payload.note)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    job = orchestrator.get_job(result.job_id)
    if job is not None:
        orchestrator.job_manager().add_approval_resolved(job, result.approval_id)
        if str(job.status) == str(JobStatusValue.needs_approval):
            from backend.app.workflows.job_manager import JobStatus as _JS
            orchestrator.job_manager().transition(job, _JS.running, f"Approval {approval_id} granted; resuming.")
    return _approval_to_response(result)


@router.post("/api/approvals/{approval_id}/reject", response_model=ApprovalRequestResponse)
async def reject_approval(
    approval_id: str,
    payload: ApprovalActionRequest,
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> ApprovalRequestResponse:
    service = orchestrator.approval_service()
    result = service.reject(approval_id, rejecter=payload.actor, note=payload.note)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    job = orchestrator.get_job(result.job_id)
    if job is not None:
        orchestrator.job_manager().add_approval_resolved(job, result.approval_id)
    return _approval_to_response(result)


@router.get("/api/memory/{project_id:path}", response_model=MemoryListResponse)
async def list_memory(
    project_id: str,
    memory_type: str | None = Query(default=None),
    query: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
    memory_service: MemoryService = Depends(get_memory_service),
) -> MemoryListResponse:
    types: list[str] | None = None
    if memory_type:
        if memory_type not in VALID_MEMORY_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid memory_type. Allowed: {sorted(VALID_MEMORY_TYPES)}")
        types = [memory_type]
    entries = memory_service.retrieve(
        project_id,
        memory_types=types,
        query=query,
        min_confidence=min_confidence,
        limit=limit,
    )
    return MemoryListResponse(
        project_id=project_id,
        entries=[_memory_to_response(e) for e in entries],
        count=len(entries),
    )


@router.post("/api/memory/{project_id:path}", response_model=MemoryEntryResponse, status_code=201)
async def write_memory(
    project_id: str,
    payload: MemoryWriteRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> MemoryEntryResponse:
    if payload.memory_type not in VALID_MEMORY_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid memory_type. Allowed: {sorted(VALID_MEMORY_TYPES)}")
    entry = memory_service.write(
        project_id,
        payload.memory_type,
        payload.content,
        payload.source,
        confidence=payload.confidence,
    )
    return _memory_to_response(entry)


@router.get("/api/tools", response_model=ToolListResponse)
async def list_tools(
    settings: Settings = Depends(get_settings),
) -> ToolListResponse:
    toolkit = GitHubToolkit(settings)
    specs = [
        ToolSpec(name=s["name"], risk=s["risk"], needs_approval=s["needs_approval"])
        for s in toolkit.tool_specs
    ]
    return ToolListResponse(tools=specs, count=len(specs))


@router.post("/api/demo/start", response_model=DemoStartResponse, status_code=202)
async def start_demo_job(
    orchestrator: OpsPilotOrchestrator = Depends(get_orchestrator),
) -> DemoStartResponse:
    goal = "Clean up my highest-priority engineering work."
    request = AgentExecutionRequest(
        goal=goal,
        demo_mode=True,
        auto_approve=True,
    )
    job = await orchestrator.start_job(request, background=True)
    stats = orchestrator.demo_stats
    return DemoStartResponse(
        job_id=job.job_id,
        demo_mode=True,
        seeded_issues=stats["seeded_issues"],
        seeded_tests_failing=stats["seeded_tests_failing"],
        goal=goal,
    )
