import asyncio

import backend.app.workflows.demo as demo_module
from backend.app.agents import OpsPilotOrchestrator
from backend.app.config.settings import Settings
from backend.app.models import RiskLevel, ToolResult, ToolStatus
from backend.app.tools import GitHubToolkit
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


def test_explicit_user_task_with_zero_issues_continues_to_execution(monkeypatch) -> None:
    """When a user provides an explicit engineering goal and the repo has 0 issues,
    the agent should synthesize a task and proceed through branch_and_fix -> create_pr."""
    # Patch DEMO_ISSUES to empty list so the DemoGitHubTransport returns zero issues.
    monkeypatch.setattr(demo_module, "DEMO_ISSUES", [])

    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Fix authentication validation bug. Investigate token logic and implement fix.",
            project_id="test-explicit",
            github_owner="harichopper",
            github_repo="opspilot",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        assert job.status == JobStatus.completed, (
            f"Expected completed, got {job.status}. Error: {job.error}\nReport:\n{job.report}"
        )
        step_names = [s["name"] for s in job.steps]
        assert "prioritize_tasks" in step_names
        # Agent uses create_branch + modify_file steps (not a single 'branch_and_fix' step)
        assert "create_branch" in step_names or "modify_file" in step_names
        assert "create_pr" in step_names
        assert job.checkpoints.get("pr_url") is not None

    _run(run())


def test_explicit_read_only_task_with_zero_issues_remains_read_only(monkeypatch) -> None:
    """A read-only goal with 0 issues must investigate but must NOT modify files or create PRs."""
    monkeypatch.setattr(demo_module, "DEMO_ISSUES", [])

    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Inspect authentication validation module and report findings (read-only audit).",
            project_id="test-readonly",
            github_owner="harichopper",
            github_repo="opspilot",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        assert job.status == JobStatus.completed, (
            f"Expected completed, got {job.status}. Error: {job.error}\nReport:\n{job.report}"
        )
        step_names = [s["name"] for s in job.steps]
        assert "read_only_mode" in step_names
        tool_names = [t["tool_name"] for t in job.tools_used]
        assert "modify_file" not in tool_names
        assert "create_pull_request" not in tool_names
        assert "pr_url" not in job.checkpoints

    _run(run())


def test_modification_goal_is_not_read_only() -> None:
    goal = (
        "Fix the autonomous modification workflow so successful modify_file "
        "operations are actually persisted to the created branch and included "
        "in the subsequent pull request. Create a separate branch, implement "
        "the improvement, add or update tests, run the existing test suite, "
        "and create a pull request."
    )

    assert OpsPilotOrchestrator._is_read_only_goal(goal) is False


def test_inspect_analyze_only_goal_remains_read_only() -> None:
    goal = (
        "Inspect and analyze the repository only; do not modify files."
    )

    assert OpsPilotOrchestrator._is_read_only_goal(goal) is True


def test_identify_and_fix_goal_is_not_read_only() -> None:
    goal = "Identify the issue and fix it without changing the public API."

    assert OpsPilotOrchestrator._is_read_only_goal(goal) is False


def test_verified_search_hits_are_preferred_for_auth_patches() -> None:
    from backend.app.agents.opspilot_agent import _AgentContext
    from backend.app.workflows.job_manager import JobRecord, JobStatus

    job = JobRecord(
        job_id="job_verified_search",
        project_id="proj",
        goal="Fix authentication validation bug.",
        status=JobStatus.running,
        github_owner="harichopper",
        github_repo="opspilot",
        request={},
        current_step="investigate",
        steps=[],
        tools_used=[],
        plan=[],
        events=[],
        report="",
        memory_updated=[],
        approval_requested_ids=[],
        approval_resolved_ids=[],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    ctx = _AgentContext(
        job=job,
        owner="harichopper",
        repo="opspilot",
        demo_mode=False,
        auto_approve=True,
        goal="Fix authentication validation bug.",
    )
    ctx.verified_file_paths.add("demo_project/auth/token.py")

    issue = {
        "number": 101,
        "title": "Authentication test suite is flaky",
        "body": "JWT validation tests fail due to small clock skew issues.",
    }

    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=False)
    patches = orchestrator._resolve_patches(ctx, issue)
    assert patches
    assert patches[0][0] == "demo_project/auth/token.py"


def test_explicit_no_branch_commit_pr_goal_remains_read_only() -> None:
    goal = (
        "Inspect the repository and identify the problem. "
        "Do not create a branch, do not create commits, "
        "and do not create a pull request."
    )

    assert OpsPilotOrchestrator._is_read_only_goal(goal) is True


def test_generic_triage_goal_with_zero_issues_completes_clean_scan(monkeypatch) -> None:
    """A generic triage goal with 0 issues and 0 PRs should complete cleanly with no task synthesized."""
    monkeypatch.setattr(demo_module, "DEMO_ISSUES", [])
    monkeypatch.setattr(demo_module, "DEMO_PRS", [])

    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Clean up my highest-priority engineering work.",
            project_id="test-clean",
            github_owner="harichopper",
            github_repo="opspilot",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        assert job.status == JobStatus.completed, (
            f"Expected completed, got {job.status}. Error: {job.error}\nReport:\n{job.report}"
        )
        assert "Repository clean" in job.report or "nothing to do" in job.report.lower()

    _run(run())



# ---------------------------------------------------------------------------
# Regression tests — autonomous file-modification safety guard
# Reproduce the exact production failure: get_file("src/main.py") → 404,
# followed by modify_file("src/main.py") that created a placeholder file
# (commit 44fd113a on branch opspilot/task-inspect-the-repository-...).
# ---------------------------------------------------------------------------


def test_get_file_404_blocks_modify_file_safety_guard(monkeypatch) -> None:
    """Reproduce the exact production bug: get_file returns 404 (error) for a
    candidate path, then modify_file must be REJECTED — not called at all.

    Before the fix _apply_fix would call modify_file with current_sha=None,
    which causes the GitHub API to create a new file with placeholder content
    ("# Applied targeted resolution for user goal").  This test proves that
    path is now impossible.
    """
    import unittest.mock as mock
    from backend.app.services.github_app_service import GitHubAppService

    # Fully mock token exchange so _resolve_github_toolkit doesn't fail.
    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_safety_guard_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    # Track every tool call made by the orchestrator.
    calls: dict[str, list] = {"get_file": [], "modify_file": [], "create_branch": []}

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository",
            risk=RiskLevel.low,
            status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": []}, duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "abc1234567890"}], "count": 1}, duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 0, "items": []}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        calls["get_file"].append(path)
        # Every path returns 404 — nothing exists in this repo.
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        calls["modify_file"].append(path)
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "bad_commit_should_not_happen"}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        calls["create_branch"].append(branch)
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_safety_test",
        GITHUB_APP_ID="app_safety_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=False)

    # Exact goal from the production incident.
    autonomous_goal = (
        "Inspect this repository and identify one small, safe improvement that can be "
        "implemented without changing the public API. Create a separate branch, implement "
        "the improvement, add or update tests, run the existing test suite, and create a "
        "pull request if all verification passes. Do not merge the pull request."
    )

    async def run() -> None:
        job = await orchestrator.start_job(
            goal=autonomous_goal,
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_safety_test",
            auto_approve=True,
            background=False,
        )
        return job

    job = _run(run())

    # 1. get_file must have been called (the agent tried to verify paths).
    assert len(calls["get_file"]) >= 1, "get_file must be called during investigation"

    # 2. THE CRITICAL INVARIANT: modify_file must NEVER be called after every
    #    get_file returned 404.  This is the exact regression.
    assert calls["modify_file"] == [], (
        f"modify_file was called for path(s) {calls['modify_file']} even though every "
        "get_file returned 404. This is the production bug — placeholder files must not "
        "be created when the target path does not exist in the repository."
    )

    # 3. Specifically: "src/main.py" must never appear as a modify_file argument.
    assert "src/main.py" not in calls["modify_file"], (
        "src/main.py must not be passed to modify_file — it was the bogus file "
        "created in commit 44fd113a."
    )

    # 4. The job must stop safely, not crash and not succeed with a ghost commit.
    assert job.status in (
        JobStatus.needs_attention,
        JobStatus.partially_completed,
        JobStatus.failed,
    ), f"Expected a safe-stop status, got: {job.status}. Error: {job.error}"

    # 5. The step log must contain either file_verification_failed or no_verified_targets,
    #    proving the guard fired.
    step_names = {s["name"] for s in job.steps}
    assert (
        "file_verification_failed" in step_names or "no_verified_targets" in step_names
    ), (
        f"Expected safety-guard step in job steps, got: {sorted(step_names)}. "
        "This means the guard did not fire."
    )


def test_verified_existing_file_can_be_modified(monkeypatch) -> None:
    """A file that is successfully returned by get_file during investigation
    must be allowed through modify_file — the guard must not over-block.
    """
    from backend.app.services.github_app_service import GitHubAppService

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_verified_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    EXISTING_PATH = "requirements.txt"
    EXISTING_CONTENT = "httpx==0.25.0\npytest==7.4.0\n"
    EXISTING_SHA = "deadbeef1234"

    modify_calls: list[str] = []

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        # Return an issue whose title contains "dependency" so _resolve_patches picks requirements.txt
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": [
                {"number": 7, "title": "Upgrade httpx dependency", "body": "httpx is outdated",
                 "labels": [], "comments": 0, "priority_score": 60}
            ]},
            duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "abc1234567890"}], "count": 1}, duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 1, "items": [{"path": EXISTING_PATH}]}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path == EXISTING_PATH:
            return ToolResult(
                tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                data={"content": EXISTING_CONTENT, "sha": EXISTING_SHA, "is_directory": False},
                duration_ms=1,
            )
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        modify_calls.append(path)
        assert sha == EXISTING_SHA, (
            f"modify_file called with sha={sha!r}, expected {EXISTING_SHA!r}. "
            "The guard must pass through the verified sha."
        )
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "fix_commit_ok"}, duration_ms=1,
        )

    async def mock_create_pr(self, owner, repo, title, head, base, body=None, draft=False, **_kw):
        return ToolResult(
            tool_name="create_pull_request", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"number": 8, "html_url": f"https://github.com/{owner}/{repo}/pull/8"},
            duration_ms=1,
        )

    from backend.app.tools.testing import LocalTestRunner

    async def mock_run_tests(self, command="pytest", owner="", repo="", workspace_override=None):
        return ToolResult(
            tool_name="run_tests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"command": command, "summary": {"exit_code": 0, "passed": 4, "failed": 0,
                                                   "total": 4, "success": True},
                  "stdout_tail": "4 passed", "stderr_tail": ""},
            duration_ms=50,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)
    monkeypatch.setattr(GitHubToolkit, "create_pull_request", mock_create_pr)
    monkeypatch.setattr(LocalTestRunner, "run", mock_run_tests)

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_verified_test",
        GITHUB_APP_ID="app_verified_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=False)

    async def run():
        return await orchestrator.start_job(
            goal="Upgrade httpx dependency to the latest stable version.",
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_verified_test",
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    # The verified file (requirements.txt) must have been modified.
    assert EXISTING_PATH in modify_calls, (
        f"Expected modify_file to be called for {EXISTING_PATH!r} (verified-existing file), "
        f"but modify_calls = {modify_calls}. The guard must not block verified files."
    )
    # And the job must complete (or at least not fail due to the guard).
    assert job.status == JobStatus.completed, (
        f"Expected completed, got {job.status}. Error: {job.error}\nSteps: "
        f"{[s['name'] for s in job.steps]}"
    )


def test_agent_recovers_from_bad_candidate_by_selecting_verified_file(monkeypatch) -> None:
    """When investigation probes several guessed paths and only one exists (README.md),
    and no keyword-matched patches apply, the agent must NOT fall back to writing a
    one-line placeholder into README.md.

    This is the exact pattern that caused commit 37fb68f9: README.md (214 lines) was
    reduced to a single line '# Autonomous improvement placeholder for goal: …'.

    After the fix, _resolve_patches returns an empty list, _apply_fix detects the empty
    list, transitions the job to needs_attention, and never calls modify_file at all.
    The job must end in a safe stop status (needs_attention / partially_completed /
    failed) with the no_verified_targets step recorded.
    """
    from backend.app.services.github_app_service import GitHubAppService

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_recovery_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    # The ONLY file that actually exists in this repo.
    REAL_FILE = "README.md"
    REAL_SHA = "readme_sha_42"

    modify_calls: list[str] = []

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": []}, duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "basesha1234567"}], "count": 1}, duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 0, "items": []}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path == REAL_FILE:
            return ToolResult(
                tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                data={"content": "# My Repo\n", "sha": REAL_SHA, "is_directory": False},
                duration_ms=1,
            )
        # All other guessed paths (src/auth.py, src/main.py, etc.) return 404.
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        modify_calls.append(path)
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "readme_fix_commit"}, duration_ms=1,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_recovery_test",
        GITHUB_APP_ID="app_recovery_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=False)

    async def run():
        return await orchestrator.start_job(
            goal=(
                "Inspect this repository and identify one small, safe improvement. "
                "Create a branch, implement it, add tests, and open a pull request. "
                "Do not merge the pull request."
            ),
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_recovery_test",
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    # ── PRIMARY REGRESSION INVARIANT ─────────────────────────────────────────
    # modify_file must NEVER be called with README.md (or any file) when the only
    # available content would be a generic one-line placeholder.
    # This is the exact pattern that destroyed README.md in commit 37fb68f9.
    assert "README.md" not in modify_calls, (
        "README.md must NOT be passed to modify_file with a one-line placeholder — "
        "that is the production bug (commit 37fb68f9) where 214 lines were deleted. "
        f"modify_calls = {modify_calls}"
    )
    assert "src/main.py" not in modify_calls, (
        "src/main.py must never be passed to modify_file — it was the bogus production file "
        f"(commit 44fd113a).  modify_calls = {modify_calls}"
    )
    assert modify_calls == [], (
        f"modify_file must not be called at all when no valid patches exist. "
        f"modify_calls = {modify_calls}"
    )

    # ── SAFE STOP ─────────────────────────────────────────────────────────────
    # The job must stop safely rather than writing destructive content.
    assert job.status in (
        JobStatus.needs_attention,
        JobStatus.partially_completed,
        JobStatus.failed,
    ), (
        f"Expected a safe-stop status, got: {job.status}. Error: {job.error}\n"
        f"Steps: {[s['name'] for s in job.steps]}"
    )

    # ── SAFETY GUARD STEP RECORDED ────────────────────────────────────────────
    step_names = {s["name"] for s in job.steps}
    assert (
        "no_verified_targets" in step_names
        or "file_verification_failed" in step_names
        or "destructive_diff_rejected" in step_names
    ), (
        f"Expected a safety-guard step in job steps, got: {sorted(step_names)}. "
        "The agent must record why it stopped rather than silently succeeding."
    )


# ---------------------------------------------------------------------------
# Regression tests — destructive-diff safety guard and README.md obliteration
# Reproduce commit 37fb68f9: README.md (214 lines) replaced by 1-line placeholder.
# ---------------------------------------------------------------------------


def test_large_file_one_line_replacement_is_rejected(monkeypatch) -> None:
    """Regression test for commit 37fb68f9.

    A 214-line README.md must NOT be replaced by a one-line placeholder
    '# Autonomous improvement placeholder for goal: …'.

    The destructive-diff guard must detect that:
      - The proposed content is 1 line vs 214 original lines (0.5% size ratio).
      - Zero original lines are preserved (0% retention).
    and block the commit, transitioning the job to needs_attention with
    the destructive_diff_rejected step recorded.
    """
    from backend.app.services.github_app_service import GitHubAppService

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_large_file_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    # Build a representative 214-line README (unique lines so retention check is strict).
    README_LINES = 214
    original_readme = "\n".join(
        [f"# OpsPilot"]
        + [f"## Section {i}" for i in range(1, 10)]
        + [f"This is paragraph {i} of the README with real content." for i in range(README_LINES - 10)]
    ) + "\n"
    assert len(original_readme.splitlines()) == README_LINES, "Fixture must be exactly 214 lines"

    DESTRUCTIVE_CONTENT = "# Autonomous improvement placeholder for goal: Inspect this repository\n"
    assert len(DESTRUCTIVE_CONTENT.splitlines()) == 1

    modify_calls: list[dict] = []

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": []}, duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "abc1234567890"}], "count": 1}, duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 0, "items": []}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path == "README.md":
            return ToolResult(
                tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                data={"content": original_readme, "sha": "readme_sha_214", "is_directory": False},
                duration_ms=1,
            )
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        modify_calls.append({"path": path, "content": content, "sha": sha})
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "SHOULD_NOT_HAPPEN"}, duration_ms=1,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)

    # Inject the agent directly with the toolkit already pre-resolved so we can
    # test the destructive-diff guard independently of the auth stack.
    from backend.app.agents.opspilot_agent import _AgentContext, OpsPilotOrchestrator
    from backend.app.workflows.job_manager import JobStatus as JS
    from backend.app.models import AgentExecutionRequest

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_large_test",
        GITHUB_APP_ID="app_large_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    toolkit = GitHubToolkit(settings, token="ghs_large_file_test")
    orchestrator = OpsPilotOrchestrator(settings, github=toolkit, demo_mode=False)

    AUTONOMOUS_GOAL = (
        "Inspect this repository and identify one small, safe improvement. "
        "Create a branch, implement it, and open a pull request. "
        "Do not merge the pull request."
    )

    async def run():
        return await orchestrator.start_job(
            goal=AUTONOMOUS_GOAL,
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    # A. modify_file must NEVER be called — the destructive-diff guard or the
    #    no_verified_targets gate must fire before any commit is made.
    assert modify_calls == [], (
        f"modify_file was called: {[(c['path'], c['content'][:60]) for c in modify_calls]}. "
        f"A 214-line README.md must not be replaced by a 1-line placeholder. "
        f"This is the exact production failure (commit 37fb68f9)."
    )

    # B. Job must stop safely.
    assert job.status in (JS.needs_attention, JS.partially_completed, JS.failed), (
        f"Expected safe-stop status, got: {job.status}. Error: {job.error}"
    )

    # C. A safety-guard step must be present in the job steps.
    step_names = {s["name"] for s in job.steps}
    safety_steps = {"destructive_diff_rejected", "no_verified_targets", "file_verification_failed"}
    assert step_names & safety_steps, (
        f"Expected one of {safety_steps} in job steps. Got: {sorted(step_names)}"
    )


def test_destructive_diff_guard_blocks_large_deletion(monkeypatch) -> None:
    """Unit-level test for the destructive-diff guard threshold.

    Given a 100-line file and a proposed replacement of 2 lines with zero
    original lines preserved, the guard must fire and block modify_file.
    This exercises the guard directly via _apply_fix without going through
    the full investigate → patches pipeline.
    """
    from backend.app.agents.opspilot_agent import _AgentContext, OpsPilotOrchestrator
    from backend.app.workflows.job_manager import JobStatus as JS

    settings = Settings()
    ORIGINAL = "\n".join(f"line {i}: some real content here" for i in range(100)) + "\n"
    DESTRUCTIVE = "# placeholder\n# second placeholder\n"

    assert len([l for l in ORIGINAL.splitlines() if l.strip()]) == 100
    assert len([l for l in DESTRUCTIVE.splitlines() if l.strip()]) == 2

    modify_called = []

    async def mock_get_file(self, owner, repo, path, **_kw):
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
            data={"content": ORIGINAL, "sha": "orig_sha_100", "is_directory": False},
            duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        modify_called.append(path)
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "SHOULD_NOT_HAPPEN"}, duration_ms=1,
        )

    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)

    toolkit = GitHubToolkit(settings, token="ghs_unit_test")
    orchestrator = OpsPilotOrchestrator(settings, github=toolkit, demo_mode=False)

    # Directly inject a patch containing the destructive content into _resolve_patches.
    import unittest.mock as mock_mod

    with mock_mod.patch.object(
        OpsPilotOrchestrator,
        "_resolve_patches",
        return_value=[("large_file.py", DESTRUCTIVE, "fix: replace large file with placeholder")],
    ):
        with mock_mod.patch.object(
            OpsPilotOrchestrator,
            "_resolve_head_sha",
            new=lambda self, gh, o, r: _make_sha_coro(),
        ):
            async def run():
                from backend.app.models import AgentExecutionRequest
                from backend.app.agents.opspilot_agent import _AgentContext
                from backend.app.workflows.job_manager import JobStatus

                req = AgentExecutionRequest(
                    goal="Targeted fix.",
                    github_owner="owner",
                    github_repo="repo",
                    demo_mode=False,
                    auto_approve=True,
                )
                job = orchestrator.job_manager().create(req)
                ctx = _AgentContext(
                    job=job,
                    owner="owner",
                    repo="repo",
                    demo_mode=False,
                    auto_approve=True,
                    goal="Targeted fix.",
                )
                ctx.verified_file_paths.add("large_file.py")
                orchestrator.job_manager().transition(job, JobStatus.running, "test")
                result = await orchestrator._apply_fix(ctx, toolkit, {"title": "fix", "number": 0})
                return result, job

            import asyncio as _asyncio

            async def _make_sha_coro():
                return "a" * 40

            fix_ok, job = _asyncio.run(run())

    # The guard must have returned False (blocked the write).
    assert fix_ok is False, (
        "destructive-diff guard must return False (block the write) "
        "when proposed content is 2 lines replacing 100-line original."
    )

    # modify_file must not have been called.
    assert modify_called == [], (
        f"modify_file must not be called when the diff is destructive. Called for: {modify_called}"
    )

    # The step log must contain destructive_diff_rejected.
    step_names = {s["name"] for s in job.steps}
    assert "destructive_diff_rejected" in step_names, (
        f"Expected 'destructive_diff_rejected' step. Got: {sorted(step_names)}"
    )


def test_targeted_modification_with_high_retention_succeeds(monkeypatch) -> None:
    """A legitimate targeted modification — small targeted change on a file where
    the majority of lines are preserved — must pass through the guard and succeed.

    Scenario: a 20-line Python file has a version constant updated (one line changed,
    all other 19 lines identical).  size_ratio = 20/20 = 1.0, retention ≈ 95%.
    The guard must NOT fire.
    """
    from backend.app.services.github_app_service import GitHubAppService
    from backend.app.tools.testing import LocalTestRunner

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_targeted_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    ORIGINAL_PY = (
        "\"\"\"Version module.\"\"\"\n"
        "__version__ = '1.0.0'\n"
        "\n"
        "def get_version():\n"
        "    return __version__\n"
        "\n"
        "# More content follows\n"
        "FEATURE_FLAGS = {\n"
        "    'auth_v2': True,\n"
        "    'new_ui': False,\n"
        "    'beta': False,\n"
        "}\n"
        "\n"
        "def is_enabled(flag):\n"
        "    return FEATURE_FLAGS.get(flag, False)\n"
        "\n"
        "TIMEOUT_SECONDS = 30\n"
        "MAX_RETRIES = 3\n"
        "DEFAULT_BRANCH = 'main'\n"
        "LOG_LEVEL = 'INFO'\n"
    )
    UPDATED_PY = ORIGINAL_PY.replace("'1.0.0'", "'1.0.1'")

    modify_calls: list[dict] = []

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": [
                {"number": 12, "title": "Upgrade version constant", "body": "bump version to 1.0.1",
                 "labels": [], "comments": 0, "priority_score": 40},
            ]},
            duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "abc1234567890abc"}], "count": 1}, duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 0, "items": []}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path in ("src/version.py", "requirements.txt"):
            content = ORIGINAL_PY if path == "src/version.py" else "httpx==0.25.0\n"
            return ToolResult(
                tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                data={"content": content, "sha": f"sha_{path.replace('/', '_')}", "is_directory": False},
                duration_ms=1,
            )
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch, sha, **_kw):
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": branch, "sha": sha}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        modify_calls.append({"path": path, "content": content})
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "version_bump_commit"}, duration_ms=1,
        )

    async def mock_create_pr(self, owner, repo, title, head, base, body=None, draft=False, **_kw):
        return ToolResult(
            tool_name="create_pull_request", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"number": 13, "html_url": f"https://github.com/{owner}/{repo}/pull/13"},
            duration_ms=1,
        )

    async def mock_run_tests(self, command="pytest", owner="", repo="", workspace_override=None):
        return ToolResult(
            tool_name="run_tests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"command": command, "summary": {"exit_code": 0, "passed": 5, "failed": 0,
                                                   "total": 5, "success": True},
                  "stdout_tail": "5 passed", "stderr_tail": ""},
            duration_ms=50,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)
    monkeypatch.setattr(GitHubToolkit, "create_pull_request", mock_create_pr)
    monkeypatch.setattr(LocalTestRunner, "run", mock_run_tests)

    # Inject the patch via _resolve_patches to supply the high-retention content.
    import unittest.mock as mock_mod
    from backend.app.services.github_app_service import GitHubAppService as _GAS

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_targeted_test",
        GITHUB_APP_ID="app_targeted_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=False)

    with mock_mod.patch.object(
        OpsPilotOrchestrator,
        "_resolve_patches",
        return_value=[("src/version.py", UPDATED_PY, "fix: bump version to 1.0.1")],
    ):
        async def run():
            return await orchestrator.start_job(
                goal="Upgrade version constant to 1.0.1.",
                github_owner="harichopper",
                github_repo="opspilot",
                demo_mode=False,
                installation_id="inst_targeted_test",
                auto_approve=True,
                background=False,
            )

        job = _run(run())

    # The guard must NOT have blocked this legitimate modification.
    assert any(c["path"] == "src/version.py" for c in modify_calls), (
        f"modify_file must be called for 'src/version.py' (legitimate targeted change). "
        f"modify_calls = {modify_calls}. "
        f"The destructive-diff guard must not block high-retention modifications."
    )

    # The job must complete (or at minimum not fail due to the guard).
    step_names = {s["name"] for s in job.steps}
    assert "destructive_diff_rejected" not in step_names, (
        f"destructive_diff_rejected step must NOT fire for a legitimate targeted change. "
        f"Steps: {sorted(step_names)}"
    )
    assert job.status == JobStatus.completed, (
        f"Expected completed, got {job.status}. Error: {job.error}\n"
        f"Steps: {sorted(step_names)}"
    )


def test_demo_mode_unchanged_by_patch_generation_fix(monkeypatch) -> None:
    """Demo mode must continue to use the seeded FIXED_AUTH_TOKEN_PY / FIXED_REQUIREMENTS_TXT
    content unchanged.  The patch-generation fix must not affect demo mode.
    """
    import backend.app.workflows.demo as demo_module

    # Clear issues so agent synthesises a task (to reach _apply_fix with a non-empty patch).
    monkeypatch.setattr(demo_module, "DEMO_ISSUES", [])

    orchestrator = OpsPilotOrchestrator(Settings(), demo_mode=True)

    async def run() -> None:
        job = await orchestrator.start_job(
            goal="Fix authentication validation bug. Investigate token logic and implement fix.",
            project_id="test-demo-unchanged",
            github_owner="harichopper",
            github_repo="opspilot",
            auto_approve=True,
            background=False,
            demo_mode=True,
        )
        assert job.status == JobStatus.completed, (
            f"Demo mode must still complete successfully. "
            f"Status: {job.status}. Error: {job.error}\nReport:\n{job.report}"
        )
        tool_names = [t["tool_name"] for t in job.tools_used]
        assert "modify_file" in tool_names, (
            "Demo mode must call modify_file for the seeded demo fix. "
            f"tools_used: {tool_names}"
        )
        # No destructive-diff guard step must appear in demo runs.
        step_names = {s["name"] for s in job.steps}
        assert "destructive_diff_rejected" not in step_names, (
            f"destructive_diff_rejected must never appear in demo mode. Steps: {sorted(step_names)}"
        )
        assert "no_verified_targets" not in step_names, (
            f"no_verified_targets must never appear in demo mode. Steps: {sorted(step_names)}"
        )

    _run(run())


# ---------------------------------------------------------------------------
# Regression tests — branch-name collision handling
# Covers: first run creates normal branch; second identical goal creates a
# unique branch; existing branch is never modified; names are valid Git refs.
# ---------------------------------------------------------------------------


def _make_branch_collision_mocks(
    *,
    monkeypatch,
    existing_branches: set,          # branch names that already "exist" in the remote
    modify_allowed: bool = True,      # whether verify/PR calls should succeed
):
    """Wire up a full set of mocks for branch-collision tests.

    Returns a dict of call-tracking lists:
      create_branch_calls  – list of branch names passed to create_branch
      modify_file_calls    – list of paths passed to modify_file
      create_pr_calls      – list of head-branch names passed to create_pull_request
    """
    from backend.app.services.github_app_service import GitHubAppService
    from backend.app.tools.testing import LocalTestRunner

    calls = {
        "create_branch": [],
        "modify_file": [],
        "create_pr": [],
    }

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_branch_collision_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    async def mock_get_repository(self, owner, repo):
        return ToolResult(
            tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
            data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                  "open_issues_count": 0, "language": "Python"},
            duration_ms=1,
        )

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
            data={"issues": [
                {"number": 77, "title": "Upgrade httpx dependency",
                 "body": "httpx is outdated", "labels": [], "comments": 0, "priority_score": 60},
            ]},
            duration_ms=1,
        )

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"pull_requests": []}, duration_ms=1,
        )

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(
            tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
            data={"commits": [{"sha": "abc1234567890abcdef1234567890abcdef123456"}], "count": 1},
            duration_ms=1,
        )

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(
            tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
            data={"count": 0, "items": []}, duration_ms=1,
        )

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path == "requirements.txt":
            return ToolResult(
                tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                data={"content": "httpx==0.25.0\npytest==7.4.0\n",
                      "sha": "reqsha123456", "is_directory": False},
                duration_ms=1,
            )
        return ToolResult(
            tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
            error=f"404 Not Found: {path}", data={}, duration_ms=1,
        )

    async def mock_create_branch(self, owner, repo, branch_name, from_sha, **_kw):
        calls["create_branch"].append(branch_name)
        if branch_name in existing_branches:
            # Simulate GitHub 422 — branch already exists.
            return ToolResult(
                tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.error,
                error=f"Branch '{branch_name}' likely already exists or SHA is invalid.",
                data={}, duration_ms=1,
            )
        # Mark it as now existing so a second call with the same name fails too.
        existing_branches.add(branch_name)
        return ToolResult(
            tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
            data={"ref": f"refs/heads/{branch_name}", "sha": from_sha,
                  "branch_name": branch_name}, duration_ms=1,
        )

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        calls["modify_file"].append(path)
        return ToolResult(
            tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"commit_sha": "fixcommitsha123"}, duration_ms=1,
        )

    async def mock_create_pr(self, owner, repo, title, head_branch, base_branch, body="", draft=False, **_kw):
        calls["create_pr"].append(head_branch)
        return ToolResult(
            tool_name="create_pull_request", risk=RiskLevel.medium, status=ToolStatus.success,
            data={"number": 99, "html_url": f"https://github.com/{owner}/{repo}/pull/99"},
            duration_ms=1,
        )

    async def mock_run_tests(self, command="pytest", owner="", repo="", workspace_override=None):
        return ToolResult(
            tool_name="run_tests", risk=RiskLevel.low, status=ToolStatus.success,
            data={"command": command, "summary": {"exit_code": 0, "passed": 5, "failed": 0,
                                                   "total": 5, "success": True},
                  "stdout_tail": "5 passed", "stderr_tail": ""},
            duration_ms=50,
        )

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)
    monkeypatch.setattr(GitHubToolkit, "create_pull_request", mock_create_pr)
    monkeypatch.setattr(LocalTestRunner, "run", mock_run_tests)

    return calls


def _make_live_orchestrator(monkeypatch):
    """Return an orchestrator backed by mocked GitHub App auth."""
    from backend.app.services.github_app_service import GitHubAppService

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_branch_test"

    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_branch_test",
        GITHUB_APP_ID="app_branch_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    return OpsPilotOrchestrator(settings, demo_mode=False)


COLLISION_GOAL = "Upgrade httpx dependency to the latest stable version."


def test_first_run_creates_base_branch_name(monkeypatch) -> None:
    """First run: no pre-existing branch → branch is created with the
    plain readable goal-derived name, no suffix appended.
    """
    existing: set = set()
    calls = _make_branch_collision_mocks(monkeypatch=monkeypatch, existing_branches=existing)
    orchestrator = _make_live_orchestrator(monkeypatch)

    async def run():
        return await orchestrator.start_job(
            goal=COLLISION_GOAL,
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_branch_test",
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    assert job.status == JobStatus.completed, (
        f"Expected completed, got {job.status}. Error: {job.error}"
    )

    # Exactly one create_branch call — the base name, no retry needed.
    assert len(calls["create_branch"]) == 1, (
        f"Expected exactly 1 create_branch call (no collision), got: {calls['create_branch']}"
    )
    branch = calls["create_branch"][0]

    # Base name must be the plain slug form.
    assert branch.startswith("opspilot/fix-issue-77-"), (
        f"Expected opspilot/fix-issue-77-... prefix, got {branch!r}"
    )
    # No numeric suffix appended on first run.
    assert branch == "opspilot/fix-issue-77-upgrade-httpx-dependency", (
        f"First-run branch should be the base name, got {branch!r}"
    )

    # PR must target the same branch.
    assert calls["create_pr"] == [branch], (
        f"PR head branch {calls['create_pr']} must match created branch {branch!r}"
    )

    # Branch name must be a valid Git ref.
    import re
    assert re.fullmatch(r"[A-Za-z0-9_./-]{1,255}", branch), (
        f"Branch name {branch!r} is not a valid Git ref"
    )


def test_second_identical_goal_gets_unique_branch(monkeypatch) -> None:
    """Second run with the same goal: the base branch already exists →
    a unique suffixed branch is created instead. The existing branch is
    never modified.
    """
    base_branch = "opspilot/fix-issue-77-upgrade-httpx-dependency"
    # Pre-populate so the first attempt fails immediately.
    existing: set = {base_branch}
    calls = _make_branch_collision_mocks(monkeypatch=monkeypatch, existing_branches=existing)
    orchestrator = _make_live_orchestrator(monkeypatch)

    async def run():
        return await orchestrator.start_job(
            goal=COLLISION_GOAL,
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_branch_test",
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    assert job.status == JobStatus.completed, (
        f"Expected completed after collision retry, got {job.status}. Error: {job.error}"
    )

    # Exactly two create_branch calls: first attempt (fails) + retry (succeeds).
    assert len(calls["create_branch"]) == 2, (
        f"Expected 2 create_branch calls (base + retry), got: {calls['create_branch']}"
    )
    first_attempt = calls["create_branch"][0]
    retry_attempt = calls["create_branch"][1]

    # First attempt is the unmodified base name.
    assert first_attempt == base_branch, (
        f"First attempt must be the base name {base_branch!r}, got {first_attempt!r}"
    )

    # Retry must share the same readable prefix.
    assert retry_attempt.startswith("opspilot/fix-issue-77-"), (
        f"Retry branch must keep readable prefix, got {retry_attempt!r}"
    )

    # Retry must differ from the base branch.
    assert retry_attempt != base_branch, (
        f"Retry branch must differ from the existing branch: {retry_attempt!r}"
    )

    # Retry branch must be ≤ 63 characters (valid Git ref constraint).
    assert len(retry_attempt) <= 63, (
        f"Retry branch too long: {retry_attempt!r} ({len(retry_attempt)} chars)"
    )

    # Retry branch must match the Git ref pattern.
    import re
    assert re.fullmatch(r"[A-Za-z0-9_./-]{1,255}", retry_attempt), (
        f"Retry branch {retry_attempt!r} is not a valid Git ref name"
    )

    # The PR must target the RETRY branch, not the original base branch.
    assert calls["create_pr"] == [retry_attempt], (
        f"PR head branch {calls['create_pr']} must target the retry branch "
        f"{retry_attempt!r}, not the original {base_branch!r}"
    )


def test_existing_branch_is_never_modified(monkeypatch) -> None:
    """Existing branch content must never be modified.

    The collision-retry logic only creates a new branch from the same base
    SHA. It must never force-push, update the ref, or modify files on the
    pre-existing branch.
    """
    base_branch = "opspilot/fix-issue-77-upgrade-httpx-dependency"
    existing: set = {base_branch}

    # Track every call that touches a branch name.
    branch_touched: dict[str, list] = {
        "create_branch": [],
        "modify_file_branches": [],  # collect (path, branch) tuples
    }

    from backend.app.services.github_app_service import GitHubAppService
    from backend.app.tools.testing import LocalTestRunner

    async def mock_get_inst_token(self, installation_id=None):
        return "ghs_existing_branch_test"
    monkeypatch.setattr(GitHubAppService, "get_installation_access_token", mock_get_inst_token)

    async def mock_get_repository(self, owner, repo):
        return ToolResult(tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"full_name": f"{owner}/{repo}", "default_branch": "main",
                                "open_issues_count": 0, "language": "Python"}, duration_ms=1)

    async def mock_list_issues(self, owner, repo, **_kw):
        return ToolResult(tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"issues": [{"number": 77, "title": "Upgrade httpx dependency",
                                            "body": "httpx is outdated", "labels": [],
                                            "comments": 0, "priority_score": 60}]}, duration_ms=1)

    async def mock_list_prs(self, owner, repo, **_kw):
        return ToolResult(tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"pull_requests": []}, duration_ms=1)

    async def mock_recent_commits(self, owner, repo, **_kw):
        return ToolResult(tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"commits": [{"sha": "abc1234567890abcdef1234567890abcdef123456"}],
                                "count": 1}, duration_ms=1)

    async def mock_search_code(self, owner, repo, query, **_kw):
        return ToolResult(tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"count": 0, "items": []}, duration_ms=1)

    async def mock_get_file(self, owner, repo, path, **_kw):
        if path == "requirements.txt":
            return ToolResult(tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success,
                              data={"content": "httpx==0.25.0\n", "sha": "reqsha123456",
                                    "is_directory": False}, duration_ms=1)
        return ToolResult(tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error,
                          error=f"404 Not Found: {path}", data={}, duration_ms=1)

    async def mock_create_branch(self, owner, repo, branch_name, from_sha, **_kw):
        branch_touched["create_branch"].append(branch_name)
        if branch_name in existing:
            return ToolResult(tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.error,
                              error=f"Branch '{branch_name}' likely already exists or SHA is invalid.",
                              data={}, duration_ms=1)
        existing.add(branch_name)
        return ToolResult(tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"ref": f"refs/heads/{branch_name}", "sha": from_sha,
                                "branch_name": branch_name}, duration_ms=1)

    async def mock_modify_file(self, owner, repo, path, content, branch, message, sha=None, **_kw):
        branch_touched["modify_file_branches"].append((path, branch))
        return ToolResult(tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success,
                          data={"commit_sha": "newcommit123"}, duration_ms=1)

    async def mock_create_pr(self, owner, repo, title, head_branch, base_branch, body="", draft=False, **_kw):
        return ToolResult(tool_name="create_pull_request", risk=RiskLevel.medium, status=ToolStatus.success,
                          data={"number": 100, "html_url": f"https://github.com/{owner}/{repo}/pull/100"},
                          duration_ms=1)

    async def mock_run_tests(self, command="pytest", owner="", repo="", workspace_override=None):
        return ToolResult(tool_name="run_tests", risk=RiskLevel.low, status=ToolStatus.success,
                          data={"command": command, "summary": {"exit_code": 0, "passed": 3,
                                                                "failed": 0, "total": 3,
                                                                "success": True},
                                "stdout_tail": "3 passed", "stderr_tail": ""}, duration_ms=50)

    monkeypatch.setattr(GitHubToolkit, "get_repository", mock_get_repository)
    monkeypatch.setattr(GitHubToolkit, "list_issues", mock_list_issues)
    monkeypatch.setattr(GitHubToolkit, "list_pull_requests", mock_list_prs)
    monkeypatch.setattr(GitHubToolkit, "get_recent_commits", mock_recent_commits)
    monkeypatch.setattr(GitHubToolkit, "search_code", mock_search_code)
    monkeypatch.setattr(GitHubToolkit, "get_file", mock_get_file)
    monkeypatch.setattr(GitHubToolkit, "create_branch", mock_create_branch)
    monkeypatch.setattr(GitHubToolkit, "modify_file", mock_modify_file)
    monkeypatch.setattr(GitHubToolkit, "create_pull_request", mock_create_pr)
    monkeypatch.setattr(LocalTestRunner, "run", mock_run_tests)

    settings = Settings(
        GITHUB_TOKEN="",
        GITHUB_APP_INSTALLATION_ID="inst_existing_test",
        GITHUB_APP_ID="app_existing_test",
        GITHUB_APP_PRIVATE_KEY="",
    )
    orchestrator = OpsPilotOrchestrator(settings, demo_mode=False)

    async def run():
        return await orchestrator.start_job(
            goal=COLLISION_GOAL,
            github_owner="harichopper",
            github_repo="opspilot",
            demo_mode=False,
            installation_id="inst_existing_test",
            auto_approve=True,
            background=False,
        )

    job = _run(run())

    assert job.status == JobStatus.completed, (
        f"Job must complete after collision retry. Status: {job.status}. Error: {job.error}"
    )

    # The pre-existing branch must never receive any modify_file commits.
    for path, branch in branch_touched["modify_file_branches"]:
        assert branch != base_branch, (
            f"modify_file was called on the pre-existing branch {base_branch!r} "
            f"(path={path!r}). Existing branches must never be modified by a new run."
        )

    # The actual branch used must be a new, distinct branch.
    actual_branch = job.checkpoint.get("pr_number") and branch_touched["modify_file_branches"]
    if branch_touched["modify_file_branches"]:
        used_branch = branch_touched["modify_file_branches"][0][1]
        assert used_branch != base_branch, (
            f"All file modifications must target the new branch, not {base_branch!r}"
        )


def test_branch_names_are_valid_git_refs(monkeypatch) -> None:
    """Both the base name and the unique-suffixed name must satisfy Git ref
    constraints: characters in [A-Za-z0-9_./-], total length ≤ 63.

    This is a pure unit test of the two static name-generation methods;
    it does not need a running orchestrator or GitHub API.
    """
    import re

    _GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9_./-]{1,255}$")

    issue_numbered   = {"number": 42, "title": "Fix auth token timing bug in production"}
    issue_no_number  = {"number": 0,  "title": "Inspect this repository and identify one small safe improvement without changing the public API please"}
    issue_short      = {"number": 0,  "title": "Fix"}
    issue_special    = {"number": 5,  "title": "Fix: 'auth' token—unicode & special <chars>"}

    for issue in (issue_numbered, issue_no_number, issue_short, issue_special):
        base = OpsPilotOrchestrator._fix_branch_name(issue)

        # Base name constraints.
        assert len(base) <= 63, f"Base name too long ({len(base)}): {base!r}"
        assert _GIT_REF_PATTERN.fullmatch(base), f"Base name not a valid ref: {base!r}"
        assert base.startswith("opspilot/"), f"Base name must start with opspilot/: {base!r}"

        # Unique name with a typical 6-hex suffix.
        for suffix in ("a3f9c1", "000000", "ffffff", "ab1cd2"):
            unique = OpsPilotOrchestrator._unique_branch_name(base, suffix)
            assert len(unique) <= 63, (
                f"Unique name too long ({len(unique)}): {unique!r} "
                f"(base={base!r}, suffix={suffix!r})"
            )
            assert _GIT_REF_PATTERN.fullmatch(unique), (
                f"Unique name not a valid ref: {unique!r}"
            )
            assert unique.endswith(suffix), (
                f"Unique name must end with suffix {suffix!r}: {unique!r}"
            )
            assert unique.startswith("opspilot/"), (
                f"Unique name must start with opspilot/: {unique!r}"
            )
            # Readable prefix: the first segment after "opspilot/" is preserved.
            assert unique != base, (
                f"Unique name must differ from base: {unique!r}"
            )
