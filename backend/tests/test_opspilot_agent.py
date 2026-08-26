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
