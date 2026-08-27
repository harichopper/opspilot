import asyncio

from backend.app.agents import OpsPilotOrchestrator
from backend.app.config.settings import Settings
from backend.app.models import RiskLevel
from backend.app.workflows.job_manager import JobStatus


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_orchestrator_start_backgrounded_job_immediately_returns_running_or_queued() -> None:
    settings = Settings()
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Quick sanity test with background.",
            project_id="bg-project",
            github_owner="opspilot",
            github_repo="demo-repo",
            auto_approve=True,
            background=True,
        )
        assert job.status in (JobStatus.queued, JobStatus.running)
        assert len(job.job_id) > 4
        orchestrator.job_manager().cancel(job.job_id, reason="test cleanup")
        latest = orchestrator.job_manager().get(job.job_id)
        assert latest is not None
        assert latest.status == JobStatus.cancelled

    _run(run())


def test_orchestrator_policy_engine_blocks_blocked_tools() -> None:
    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=True)
    decision = orchestrator.policy_engine().evaluate(
        "delete_production_database", RiskLevel.low, False, tool_args={}
    )
    assert decision.blocked is True
    assert decision.allowed is False


def test_modification_goal_is_not_read_only() -> None:
    goal = "Fix the auth validation bug and update the tests."
    assert OpsPilotOrchestrator._is_read_only_goal(goal) is False


def test_inspect_analyze_only_goal_remains_read_only() -> None:
    goal = "Inspect and analyze the repository only; do not modify files."
    assert OpsPilotOrchestrator._is_read_only_goal(goal) is True


def test_identify_and_fix_goal_is_not_read_only() -> None:
    goal = "Identify the root cause and fix the failing login flow."
    assert OpsPilotOrchestrator._is_read_only_goal(goal) is False


def test_explicit_no_branch_commit_pr_goal_remains_read_only() -> None:
    goal = "Inspect the repository and report findings; do not create a branch, do not commit, and do not open a PR."
    assert OpsPilotOrchestrator._is_read_only_goal(goal) is True
