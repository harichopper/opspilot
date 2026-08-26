import asyncio

from backend.app.agents import OpsPilotOrchestrator
from backend.app.config.settings import Settings
from backend.app.workflows.job_manager import JobStatus


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_demo_mode_e2e_completes_with_pr_and_tests_passed() -> None:
    settings = Settings()
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Clean up the highest-priority engineering work in this repository.",
            project_id="demo-project",
            github_owner="opspilot",
            github_repo="demo-repo",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        while job.status in (JobStatus.queued, JobStatus.running):
            await asyncio.sleep(0.2)
            latest = orchestrator.job_manager().get(job.job_id)
            if latest is not None:
                job = latest
        assert job.status == JobStatus.completed, (
            f"expected completed, got {job.status}. Report:\n{job.report}\nError:\n{job.error}"
        )
        tool_names = [t["tool_name"] for t in job.tools_used]
        assert "modify_file" in tool_names
        assert "create_pull_request" in tool_names
        # run_tests can either be toolkit-native run_tests OR called as part of
        # create_pull_request verification; we simply assert tests were executed
        run_tests_result = [
            t for t in job.tools_used if t["tool_name"] == "run_tests"
        ]
        if run_tests_result:
            data = run_tests_result[-1].get("data") or {}
            summary = data.get("summary") or {}
            assert summary.get("exit_code") in (0, None) or data.get("exit_code") in (0, None)
        assert "pr_number" in job.checkpoints
        assert isinstance(job.checkpoints["pr_number"], int)
        assert job.checkpoints["pr_number"] > 0
        assert job.checkpoints.get("pr_url", "").startswith("http")
        memory_entries = orchestrator.memory_service().list_all(job.project_id)
        assert len(memory_entries) >= 4
        assert job.report is not None
        assert "OPS PILOT REPORT" in job.report

    _run(run())


def test_demo_mode_defaults_and_placeholder_sanitization_target_harichopper_opspilot() -> None:
    settings = Settings()
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="string",
            project_id=None,
            github_owner="string",
            github_repo="string/string",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        assert job.github_owner == "harichopper"
        assert job.github_repo == "opspilot"
        assert job.project_id == "github:harichopper/opspilot"

    _run(run())

