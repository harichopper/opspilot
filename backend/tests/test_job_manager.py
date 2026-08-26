import uuid

import pytest

from backend.app.models.schemas import AgentExecutionRequest, AgentStep
from backend.app.workflows.job_manager import (
    JobEvent,
    JobManager,
    JobRecord,
    JobStatus,
    JobStore,
)


@pytest.fixture
def jm() -> JobManager:
    return JobManager(JobStore())


def _new_request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        goal="Test goal",
        github_owner="octocat",
        github_repo="Hello-World",
        demo_mode=False,
        auto_approve=False,
    )


def test_job_manager_creates_records(jm: JobManager) -> None:
    req = _new_request()
    saved = jm.create(req)
    assert saved.job_id.startswith("job_")
    assert saved.status == JobStatus.queued
    got = jm.get(saved.job_id)
    assert got is not None
    assert got.job_id == saved.job_id


def test_job_manager_valid_transition_running(jm: JobManager) -> None:
    rec = jm.create(_new_request())
    jm.transition(rec, JobStatus.running, "Starting")
    assert rec.status == JobStatus.running
    assert rec.events
    assert rec.events[-1].event_type == "job.status_change"


def test_job_manager_invalid_transition_from_completed(jm: JobManager) -> None:
    rec = jm.create(_new_request())
    jm.transition(rec, JobStatus.running, "start")
    jm.transition(rec, JobStatus.completed, "done")
    with pytest.raises(ValueError):
        jm.transition(rec, JobStatus.running, "retry-from-terminal-not-allowed")


def test_job_manager_list_by_project(jm: JobManager) -> None:
    r1 = AgentExecutionRequest(goal="xyz", github_owner="px", github_repo="a")
    r2 = AgentExecutionRequest(goal="xyz", github_owner="py", github_repo="b")
    r3 = AgentExecutionRequest(goal="xyz", github_owner="px", github_repo="c")
    jm.create(r1)
    jm.create(r2)
    jm.create(r3)
    only_px = jm.list(project_id="github:px/a")
    assert len(only_px) >= 1
    by_project = jm.list_for_project("github:px/a")
    assert len(by_project) >= 1


def test_job_manager_add_step_and_checkpoint(jm: JobManager) -> None:
    rec = jm.create(_new_request())
    jm.append_step(rec, AgentStep(name="repo_scan", status="completed", detail="scanned ok"))
    jm.append_step(rec, AgentStep(name="fix_tests", status="running", detail="patching files"))
    assert len(rec.steps) == 2
    jm.write_checkpoint(rec, "pr_number", 142)
    jm.write_checkpoint(rec, "pr_url", "https://github.com/x/y/pull/142")
    assert rec.checkpoints["pr_number"] == 142
    assert rec.checkpoint["pr_url"].startswith("http")


def test_job_manager_cancel_queued_allowed(jm: JobManager) -> None:
    rec = jm.create(_new_request())
    cancelled = jm.cancel(rec.job_id, reason="user")
    assert cancelled.status == JobStatus.cancelled


def test_job_manager_retry_count_and_event_emit(jm: JobManager) -> None:
    rec = jm.create(_new_request())
    jm.increment_retry(rec, "tool_x failed")
    assert rec.retry_count == 1
    before = len(rec.events)
    jm.emit_event(
        rec, event_type="tool_call", message="tool get_repository called",
        data={"tool": "get_repository"},
    )
    assert len(rec.events) == before + 1
    assert isinstance(rec.events[-1], JobEvent)
    assert rec.events[-1].event_type == "tool_call"
