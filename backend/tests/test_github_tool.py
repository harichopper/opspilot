import asyncio

import httpx
import pytest

from backend.app.config.settings import Settings
from backend.app.models import RiskLevel, ToolStatus
from backend.app.tools.github import GitHubToolkit


@pytest.fixture
def settings() -> Settings:
    return Settings()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_toolkit_tool_specs_count(settings: Settings) -> None:
    toolkit = GitHubToolkit(settings)
    names = [s["name"] for s in toolkit.tool_specs]
    assert "get_repository" in names
    assert "list_issues" in names
    assert "modify_file" in names
    assert "create_pull_request" in names
    assert "run_tests" in names
    assert len(names) >= 12


def test_get_repository_returns_shaped_metadata(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/Hello-World"
        assert request.headers["user-agent"] == "opspilot-agent"
        return httpx.Response(
            200,
            json={
                "id": 1296269,
                "full_name": "octocat/Hello-World",
                "description": "Demo repository",
                "private": False,
                "default_branch": "main",
                "open_issues_count": 2,
                "forks_count": 10,
                "stargazers_count": 20,
                "language": "Python",
                "pushed_at": "2026-08-26T00:00:00Z",
                "html_url": "https://github.com/octocat/Hello-World",
                "ignored_secret_like_field": "do-not-return",
            },
        )

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        toolkit = GitHubToolkit(settings, client=client)

        result = await toolkit.get_repository("octocat", "Hello-World")

        assert result.status == ToolStatus.success
        assert result.risk == RiskLevel.low
        assert result.data["full_name"] == "octocat/Hello-World"
        assert result.data["default_branch"] == "main"
        assert "ignored_secret_like_field" not in result.data
        await client.aclose()

    _run(run())


def test_get_repository_rejects_invalid_repo_reference(settings: Settings) -> None:
    async def run() -> None:
        toolkit = GitHubToolkit(settings)
        result = await toolkit.get_repository("octocat", "bad/repo")
        assert result.status == ToolStatus.error
        assert result.risk == RiskLevel.low
        assert "Invalid GitHub repository name" in (result.error or "")

    _run(run())


def test_get_repository_uses_bearer_token_when_configured(settings: Settings) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "id": 1,
                "full_name": "octocat/Hello-World",
                "description": "Demo repository",
                "private": False,
                "default_branch": "main",
                "open_issues_count": 1,
                "forks_count": 0,
                "stargazers_count": 0,
                "language": "Python",
                "pushed_at": "2026-08-26T00:00:00Z",
                "html_url": "https://github.com/octocat/Hello-World",
            },
        )

    async def run() -> None:
        s = Settings(github_token="supersecret")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        toolkit = GitHubToolkit(s, client=client)
        result = await toolkit.get_repository("octocat", "Hello-World")
        assert result.status == ToolStatus.success
        assert observed["authorization"] == "Bearer supersecret"
        await client.aclose()

    _run(run())


def test_list_issues_priority_scores_bug_and_critical(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/issues" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Auth broken",
                        "state": "open",
                        "body": "urgent",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                        "comments": 5,
                        "user": {"login": "alice"},
                        "labels": [
                            {"name": "bug"},
                            {"name": "critical"},
                            {"name": "P0"},
                        ],
                    },
                    {
                        "number": 2,
                        "title": "Docs typo",
                        "state": "open",
                        "body": None,
                        "created_at": "2026-01-05T00:00:00Z",
                        "updated_at": "2026-01-06T00:00:00Z",
                        "comments": 0,
                        "user": {"login": "bob"},
                        "labels": [{"name": "documentation"}],
                    },
                ],
            )
        return httpx.Response(404, json={})

    async def run() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        toolkit = GitHubToolkit(settings, client=client)
        result = await toolkit.list_issues("octocat", "Hello-World")
        assert result.status == ToolStatus.success
        issues = result.data["issues"]
        assert len(issues) == 2
        assert issues[0]["number"] == 1
        assert issues[0]["priority_score"] >= 100
        assert issues[1]["number"] == 2
        assert issues[1]["priority_score"] < issues[0]["priority_score"]
        await client.aclose()

    _run(run())


def test_create_branch_validates_sha(settings: Settings) -> None:
    async def run() -> None:
        toolkit = GitHubToolkit(settings)
        result = await toolkit.create_branch(
            "o", "r", branch_name="feat/x", from_sha="short"
        )
        assert result.status == ToolStatus.error
        assert "from_sha" in (result.error or "").lower()

    _run(run())


def test_modify_file_1mb_limit(settings: Settings) -> None:
    async def run() -> None:
        toolkit = GitHubToolkit(settings)
        big = "a" * (1024 * 1024 + 1)
        result = await toolkit.modify_file(
            "o", "r", path="too_big.txt", content=big, branch="main",
            message="test commit", current_sha="a" * 40
        )
        assert result.status == ToolStatus.error
        assert "1MB" in (result.error or "")

    _run(run())


def test_create_pull_request_requires_different_base_head(settings: Settings) -> None:
    async def run() -> None:
        toolkit = GitHubToolkit(settings)
        result = await toolkit.create_pull_request(
            "o", "r", title="t", head_branch="main", base_branch="main", body="b"
        )
        assert result.status == ToolStatus.error
        assert "base" in (result.error or "").lower()

    _run(run())
