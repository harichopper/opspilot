import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.agents import OpsPilotOrchestrator
from backend.app.config.settings import Settings
from backend.app.models import RiskLevel, ToolResult, ToolStatus
from backend.app.services.patch_generator import PatchGenerator, PatchResult
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


def test_discovery_and_file_verification() -> None:
    mock_github = MagicMock()
    mock_github.search_code = AsyncMock(return_value=ToolResult(
        tool_name="search_code",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={
            "results": [
                {"path": "src/valid.py"},
                {"path": "src/valid.py"},
                {"path": "nonexistent.py"},
            ]
        },
    ))

    async def mock_get_file(owner, repo, path):
        if path == "src/valid.py":
            return ToolResult(
                tool_name="get_file",
                risk=RiskLevel.low,
                status=ToolStatus.success,
                duration_ms=10,
                data={"path": "src/valid.py", "content": "print('hello')", "is_directory": False},
            )
        return ToolResult(
            tool_name="get_file",
            risk=RiskLevel.low,
            status=ToolStatus.error,
            duration_ms=10,
            error="File not found",
        )

    mock_github.get_file = AsyncMock(side_effect=mock_get_file)

    orchestrator = OpsPilotOrchestrator(Settings(), github=mock_github, demo_mode=False)

    async def run():
        job = await orchestrator.start_job(
            goal="Test discovery and verification",
            github_owner="realowner",
            github_repo="realrepo",
            auto_approve=True,
            background=False,
        )
        assert job.status in (JobStatus.needs_attention, JobStatus.partially_completed)

    _run(run())


def test_guessed_nonexistent_paths_never_reach_modify_file_and_no_placeholder_patch() -> None:
    mock_github = MagicMock()
    mock_github.get_repository = AsyncMock(return_value=ToolResult(
        tool_name="get_repository",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"full_name": "real/repo", "default_branch": "main", "language": "Python"},
    ))
    mock_github.list_issues = AsyncMock(return_value=ToolResult(
        tool_name="list_issues",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"issues": [{"number": 1, "title": "Fix auth token timing", "body": "some issue", "priority_score": 10}]},
    ))
    mock_github.list_pull_requests = AsyncMock(return_value=ToolResult(
        tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"pull_requests": []}
    ))
    mock_github.get_recent_commits = AsyncMock(return_value=ToolResult(
        tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"commits": [{"sha": "0123456789abcdef0123456789abcdef01234567"}]}
    ))
    mock_github.get_issue = AsyncMock(return_value=ToolResult(
        tool_name="get_issue", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"number": 1, "title": "Fix auth token"}
    ))
    mock_github.search_code = AsyncMock(return_value=ToolResult(
        tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"results": []}
    ))
    mock_github.get_file = AsyncMock(return_value=ToolResult(
        tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error, duration_ms=10, error="404 Not Found"
    ))
    mock_github.modify_file = AsyncMock()

    orchestrator = OpsPilotOrchestrator(Settings(), github=mock_github, demo_mode=False)

    async def run():
        job = await orchestrator.start_job(
            goal="Fix auth token timing",
            github_owner="realowner",
            github_repo="realrepo",
            auto_approve=True,
            background=False,
        )
        assert job.status == JobStatus.needs_attention
        mock_github.modify_file.assert_not_called()
        assert "leeway-aware fix placeholder" not in (job.report or "")

    _run(run())


def test_read_only_workflow_performs_no_mutation() -> None:
    mock_github = MagicMock()
    mock_github.get_repository = AsyncMock(return_value=ToolResult(
        tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"full_name": "real/repo", "default_branch": "main"}
    ))
    mock_github.list_issues = AsyncMock(return_value=ToolResult(
        tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"issues": [{"number": 1, "title": "Audit repository", "priority_score": 10}]}
    ))
    mock_github.list_pull_requests = AsyncMock(return_value=ToolResult(
        tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"pull_requests": []}
    ))
    mock_github.get_recent_commits = AsyncMock(return_value=ToolResult(
        tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"commits": []}
    ))
    mock_github.get_issue = AsyncMock(return_value=ToolResult(
        tool_name="get_issue", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"number": 1, "title": "Audit repository"}
    ))
    mock_github.search_code = AsyncMock(return_value=ToolResult(
        tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"results": []}
    ))
    mock_github.get_file = AsyncMock(return_value=ToolResult(
        tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.error, duration_ms=10, error="404 Not Found"
    ))
    mock_github.create_branch = AsyncMock()
    mock_github.modify_file = AsyncMock()
    mock_github.create_pull_request = AsyncMock()

    orchestrator = OpsPilotOrchestrator(Settings(), github=mock_github, demo_mode=False)

    async def run():
        job = await orchestrator.start_job(
            goal="Inspect repository for open issues and security risks",
            github_owner="realowner",
            github_repo="realrepo",
            auto_approve=True,
            background=False,
        )
        assert job.status == JobStatus.completed
        mock_github.create_branch.assert_not_called()
        mock_github.modify_file.assert_not_called()
        mock_github.create_pull_request.assert_not_called()

    _run(run())


def test_destructive_diff_remains_blocked() -> None:
    assert OpsPilotOrchestrator._is_destructive_diff(
        "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10",
        ""
    ) is True
    assert OpsPilotOrchestrator._is_destructive_diff(
        "line1\nline2",
        "line1\nline2\nline3"
    ) is False


def test_branch_collision_stores_retry_branch() -> None:
    mock_github = MagicMock()
    mock_github.get_repository = AsyncMock(return_value=ToolResult(
        tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"full_name": "real/repo", "default_branch": "main"}
    ))
    mock_github.list_issues = AsyncMock(return_value=ToolResult(
        tool_name="list_issues", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"issues": [{"number": 1, "title": "Upgrade httpx", "priority_score": 10}]}
    ))
    mock_github.list_pull_requests = AsyncMock(return_value=ToolResult(
        tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"pull_requests": []}
    ))
    mock_github.get_recent_commits = AsyncMock(return_value=ToolResult(
        tool_name="get_recent_commits", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"commits": [{"sha": "0000111122223333444455556666777788889999"}]}
    ))
    mock_github.get_issue = AsyncMock(return_value=ToolResult(
        tool_name="get_issue", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"number": 1, "title": "Upgrade httpx"}
    ))
    mock_github.search_code = AsyncMock(return_value=ToolResult(
        tool_name="search_code", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"results": [{"path": "requirements.txt"}]}
    ))
    mock_github.get_file = AsyncMock(return_value=ToolResult(
        tool_name="get_file", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"path": "requirements.txt", "content": "httpx==0.20.0", "sha": "123", "is_directory": False}
    ))

    branch_calls = []
    async def mock_create_branch(owner, repo, branch, base_sha):
        branch_calls.append(branch)
        if branch.endswith("-v2"):
            return ToolResult(tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"branch": branch})
        return ToolResult(tool_name="create_branch", risk=RiskLevel.low, status=ToolStatus.error, duration_ms=10, error="Reference already exists (422)")

    mock_github.create_branch = AsyncMock(side_effect=mock_create_branch)
    mock_github.modify_file = AsyncMock(return_value=ToolResult(
        tool_name="modify_file", risk=RiskLevel.medium, status=ToolStatus.success, duration_ms=10, data={"commit_sha": "abc"}
    ))
    mock_github.create_pull_request = AsyncMock(return_value=ToolResult(
        tool_name="create_pull_request", risk=RiskLevel.medium, status=ToolStatus.success, duration_ms=10, data={"number": 2, "html_url": "http://pr"}
    ))

    orchestrator = OpsPilotOrchestrator(Settings(), github=mock_github, demo_mode=False)

    with patch("backend.app.tools.LocalTestRunner.run", new_callable=AsyncMock) as mock_test_runner:
        mock_test_runner.return_value = ToolResult(
            tool_name="run_tests", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"summary": {"exit_code": 0, "passed": 1, "failed": 0}}
        )
        async def run():
            job = await orchestrator.start_job(
                goal="Upgrade httpx",
                github_owner="realowner",
                github_repo="realrepo",
                auto_approve=True,
                background=False,
            )
            assert job.status == JobStatus.completed
            assert len(branch_calls) == 2
            assert branch_calls[1].endswith("-v2")

            assert mock_github.modify_file.call_args[0][4] == branch_calls[1]
            assert mock_github.create_pull_request.call_args[0][3] == branch_calls[1]

        _run(run())


def test_patch_generator_unit_validation_and_safety() -> None:
    pg = PatchGenerator(Settings())

    # Unverified path rejected
    valid, msg = pg.validate_patch("unverified.py", "old", "new", ["src/valid.py"])
    assert not valid
    assert "not in verified" in msg

    # Path traversal / absolute path rejected
    valid, msg = pg.validate_patch("../etc/passwd", "old", "new", ["../etc/passwd"])
    assert not valid
    assert "traversal" in msg

    valid, msg = pg.validate_patch("C:/absolute/path.py", "old", "new", ["C:/absolute/path.py"])
    assert not valid
    assert "absolute path" in msg

    # Identical content rejected
    valid, msg = pg.validate_patch("src/valid.py", "same content", "same content", ["src/valid.py"])
    assert not valid
    assert "identical" in msg

    # Empty content rejected
    valid, msg = pg.validate_patch("src/valid.py", "non-empty content", "", ["src/valid.py"])
    assert not valid
    assert "empty" in msg

    # Placeholder content rejected
    valid, msg = pg.validate_patch("src/valid.py", "line1\nline2", "line1\n# leeway-aware fix placeholder", ["src/valid.py"])
    assert not valid
    assert "placeholder" in msg

    # Destructive diff rejected
    old_content = "\n".join([f"line_{i}" for i in range(20)])
    valid, msg = pg.validate_patch("src/valid.py", old_content, "line_0", ["src/valid.py"])
    assert not valid
    assert "Destructive" in msg


def test_patch_generator_llm_integration_mocked() -> None:
    settings = Settings(gemini_api_key="fake-test-key")
    pg = PatchGenerator(settings)

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "```python\ndef verify_token(token: str, leeway: int = 10):\n    return True\n```"
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        async def run():
            res = await pg.generate_patch(
                goal="Fix authentication timeout",
                issue={"number": 5, "title": "Token timeout bug", "body": "leeway issue"},
                target_path="backend/auth/token.py",
                original_content="def verify_token(token: str):\n    return True\n",
                verified_paths=["backend/auth/token.py"],
            )
            assert res.success is True
            assert res.path == "backend/auth/token.py"
            assert "leeway" in res.new_content

        _run(run())


def test_realistic_orchestrator_source_file_mutation_flow() -> None:
    mock_github = MagicMock()
    mock_github.get_repository = AsyncMock(return_value=ToolResult(
        tool_name="get_repository", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"full_name": "real/repo", "default_branch": "main"}
    ))
    mock_github.list_issues = AsyncMock(return_value=ToolResult(
        tool_name="list_issues",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"issues": [{"number": 15, "title": "Fix auth token timeout and leeway", "body": "Need to update timeout handling in token.py", "priority_score": 60}]},
    ))
    mock_github.list_pull_requests = AsyncMock(return_value=ToolResult(
        tool_name="list_pull_requests", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"pull_requests": []}
    ))
    mock_github.get_recent_commits = AsyncMock(return_value=ToolResult(
        tool_name="get_recent_commits",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"commits": [{"sha": "9999888877776666555544443333222211110000"}]},
    ))
    mock_github.get_issue = AsyncMock(return_value=ToolResult(
        tool_name="get_issue", risk=RiskLevel.low, status=ToolStatus.success, duration_ms=10, data={"number": 15, "title": "Fix auth token timeout and leeway"}
    ))
    mock_github.search_code = AsyncMock(return_value=ToolResult(
        tool_name="search_code",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"results": [{"path": "backend/auth/token.py"}]},
    ))

    verified_orig_content = "def verify_token(token: str, timeout = 5):\n    # token check\n    return True\n"
    mock_github.get_file = AsyncMock(return_value=ToolResult(
        tool_name="get_file",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"path": "backend/auth/token.py", "content": verified_orig_content, "sha": "sha_token_123", "is_directory": False},
    ))
    mock_github.create_branch = AsyncMock(return_value=ToolResult(
        tool_name="create_branch",
        risk=RiskLevel.low,
        status=ToolStatus.success,
        duration_ms=10,
        data={"branch_name": "opspilot/fix-issue-15-fix-auth-token-timeout-and-leeway"},
    ))
    mock_github.modify_file = AsyncMock(return_value=ToolResult(
        tool_name="modify_file",
        risk=RiskLevel.medium,
        status=ToolStatus.success,
        duration_ms=10,
        data={"path": "backend/auth/token.py", "commit_sha": "commit_sha_token_456"},
    ))
    mock_github.create_pull_request = AsyncMock(return_value=ToolResult(
        tool_name="create_pull_request",
        risk=RiskLevel.medium,
        status=ToolStatus.success,
        duration_ms=10,
        data={"number": 99, "html_url": "https://github.com/real/repo/pull/99"},
    ))

    orchestrator = OpsPilotOrchestrator(Settings(), github=mock_github, demo_mode=False)

    with patch("backend.app.tools.LocalTestRunner.run", new_callable=AsyncMock) as mock_test_runner:
        mock_test_runner.return_value = ToolResult(
            tool_name="run_tests",
            risk=RiskLevel.low,
            status=ToolStatus.success,
            duration_ms=10,
            data={"summary": {"exit_code": 0, "passed": 12, "failed": 0}},
        )

        async def run():
            job = await orchestrator.start_job(
                goal="Fix the authentication timeout handling",
                github_owner="realowner",
                github_repo="realrepo",
                auto_approve=True,
                background=False,
            )
            assert job.status == JobStatus.completed

            expected_branch = "opspilot/fix-issue-15-fix-auth-token-timeout-and-leeway"

            # Verify modify_file call parameters: owner, repo, path, new_content, branch, message, sha
            mock_github.modify_file.assert_called_once()
            call_args = mock_github.modify_file.call_args[0]
            assert call_args[0] == "realowner"
            assert call_args[1] == "realrepo"
            assert call_args[2] == "backend/auth/token.py"
            assert "timeout=30" in call_args[3] or "leeway" in call_args[3]
            assert call_args[4] == expected_branch
            assert call_args[6] == "sha_token_123"

            # Verify create_pull_request receives same active branch and dynamic truthful body
            mock_github.create_pull_request.assert_called_once()
            pr_call_args = mock_github.create_pull_request.call_args[0]
            assert pr_call_args[3] == expected_branch
            pr_body = pr_call_args[5]
            assert "Modifies `backend/auth/token.py`" in pr_body
            assert "Applied clock-skew tolerant token validation." not in pr_body

        _run(run())
