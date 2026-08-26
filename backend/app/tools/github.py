import base64
import re
import time
from typing import Any

import httpx

from backend.app.config.settings import Settings
from backend.app.models import RiskLevel, ToolResult, ToolStatus


_GITHUB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_./-]{1,255}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class GitHubToolkit:
    """Comprehensive GitHub tool surface with typed risk classification."""

    api_url = "https://api.github.com"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "get_repository", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "list_issues", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "get_issue", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "list_pull_requests", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "get_pull_request", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "get_file", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "search_code", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "get_recent_commits", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "get_ci_status", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "create_branch", "risk": RiskLevel.low, "needs_approval": False},
            {"name": "modify_file", "risk": RiskLevel.medium, "needs_approval": True},
            {"name": "create_commit", "risk": RiskLevel.medium, "needs_approval": True},
            {"name": "create_pull_request", "risk": RiskLevel.medium, "needs_approval": True},
            {"name": "run_tests", "risk": RiskLevel.low, "needs_approval": False},
        ]

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opspilot-agent",
        }
        if self._settings.github_token:
            headers["Authorization"] = f"Bearer {self._settings.github_token}"
        return headers

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        close_after = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20)
        return client, close_after

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _error(self, tool_name: str, risk: RiskLevel, message: str, started: float) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            risk=risk,
            status=ToolStatus.error,
            error=message,
            duration_ms=self._elapsed_ms(started),
        )

    def _success(self, tool_name: str, risk: RiskLevel, data: dict[str, Any], started: float) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            risk=risk,
            status=ToolStatus.success,
            data=data,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _validate_repo_ref(owner: str, repo: str) -> str | None:
        if not _GITHUB_NAME_PATTERN.fullmatch(owner):
            return "Invalid GitHub owner. Use letters, numbers, dots, dashes, or underscores."
        if not _GITHUB_NAME_PATTERN.fullmatch(repo):
            return "Invalid GitHub repository name. Use letters, numbers, dots, dashes, or underscores."
        return None

    async def get_repository(self, owner: str, repo: str) -> ToolResult:
        tool_name = "get_repository"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            data = {
                "id": payload.get("id"),
                "full_name": payload.get("full_name"),
                "description": payload.get("description"),
                "private": payload.get("private"),
                "default_branch": payload.get("default_branch"),
                "open_issues_count": payload.get("open_issues_count"),
                "forks_count": payload.get("forks_count"),
                "stargazers_count": payload.get("stargazers_count"),
                "language": payload.get("language"),
                "topics": payload.get("topics", []),
                "pushed_at": payload.get("pushed_at"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "html_url": payload.get("html_url"),
                "clone_url": payload.get("clone_url"),
                "license": payload.get("license", {}).get("spdx_id") if payload.get("license") else None,
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code} for {owner}/{repo}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
        labels: str | None = None,
    ) -> ToolResult:
        tool_name = "list_issues"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if state not in {"open", "closed", "all"}:
            return self._error(tool_name, risk, "State must be 'open', 'closed', or 'all'.", started)
        if not 1 <= per_page <= 100:
            return self._error(tool_name, risk, "per_page must be between 1 and 100.", started)

        params: dict[str, Any] = {"state": state, "per_page": per_page}
        if labels:
            params["labels"] = labels

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/issues",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            items = response.json()
            issues = []
            for item in items:
                if "pull_request" in item:
                    continue
                issues.append({
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "body": item.get("body"),
                    "labels": [l.get("name") for l in item.get("labels", [])],
                    "assignees": [a.get("login") for a in item.get("assignees", [])],
                    "user": item.get("user", {}).get("login"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "comments": item.get("comments"),
                    "html_url": item.get("html_url"),
                    "priority_score": self._priority_score(item),
                })
            issues.sort(key=lambda i: i["priority_score"], reverse=True)
            return self._success(tool_name, risk, {"count": len(issues), "issues": issues}, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> ToolResult:
        tool_name = "get_issue"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if issue_number <= 0:
            return self._error(tool_name, risk, "issue_number must be a positive integer.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/issues/{issue_number}",
                headers=self._headers(),
            )
            response.raise_for_status()
            item = response.json()
            is_pr = "pull_request" in item
            data = {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "body": item.get("body"),
                "labels": [l.get("name") for l in item.get("labels", [])],
                "assignees": [a.get("login") for a in item.get("assignees", [])],
                "user": item.get("user", {}).get("login"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "comments": item.get("comments"),
                "html_url": item.get("html_url"),
                "is_pull_request": is_pr,
                "milestone": item.get("milestone", {}).get("title") if item.get("milestone") else None,
                "priority_score": self._priority_score(item),
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code} for issue #{issue_number}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 20,
    ) -> ToolResult:
        tool_name = "list_pull_requests"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if state not in {"open", "closed", "all"}:
            return self._error(tool_name, risk, "State must be 'open', 'closed', or 'all'.", started)
        if not 1 <= per_page <= 100:
            return self._error(tool_name, risk, "per_page must be between 1 and 100.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/pulls",
                headers=self._headers(),
                params={"state": state, "per_page": per_page},
            )
            response.raise_for_status()
            items = response.json()
            prs = []
            for item in items:
                prs.append({
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "body": item.get("body"),
                    "user": item.get("user", {}).get("login"),
                    "head_ref": item.get("head", {}).get("ref"),
                    "base_ref": item.get("base", {}).get("ref"),
                    "mergeable": item.get("mergeable"),
                    "mergeable_state": item.get("mergeable_state"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "html_url": item.get("html_url"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "changed_files": item.get("changed_files", 0),
                    "labels": [l.get("name") for l in item.get("labels", [])],
                })
            return self._success(tool_name, risk, {"count": len(prs), "pull_requests": prs}, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> ToolResult:
        tool_name = "get_pull_request"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if pr_number <= 0:
            return self._error(tool_name, risk, "pr_number must be a positive integer.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self._headers(),
            )
            response.raise_for_status()
            item = response.json()
            data = {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "body": item.get("body"),
                "user": item.get("user", {}).get("login"),
                "head_ref": item.get("head", {}).get("ref"),
                "head_sha": item.get("head", {}).get("sha"),
                "base_ref": item.get("base", {}).get("ref"),
                "base_sha": item.get("base", {}).get("sha"),
                "mergeable": item.get("mergeable"),
                "mergeable_state": item.get("mergeable_state"),
                "merged": item.get("merged"),
                "merged_by": item.get("merged_by", {}).get("login") if item.get("merged_by") else None,
                "merged_at": item.get("merged_at"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "closed_at": item.get("closed_at"),
                "html_url": item.get("html_url"),
                "additions": item.get("additions", 0),
                "deletions": item.get("deletions", 0),
                "changed_files": item.get("changed_files", 0),
                "commits_count": item.get("commits", 0),
                "comments_count": item.get("comments", 0),
                "review_comments_count": item.get("review_comments", 0),
                "labels": [l.get("name") for l in item.get("labels", [])],
                "requested_reviewers": [r.get("login") for r in item.get("requested_reviewers", [])],
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code} for PR #{pr_number}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> ToolResult:
        tool_name = "get_file"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not path or not path.strip() or ".." in path.split("/"):
            return self._error(tool_name, risk, "Invalid file path.", started)

        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            item = response.json()
            if isinstance(item, list):
                entries = [
                    {
                        "name": e.get("name"),
                        "path": e.get("path"),
                        "type": e.get("type"),
                        "sha": e.get("sha"),
                        "size": e.get("size", 0),
                    }
                    for e in item
                ]
                return self._success(tool_name, risk, {"is_directory": True, "entries": entries}, started)

            content = ""
            if item.get("encoding") == "base64":
                try:
                    raw = base64.b64decode(item.get("content", ""))
                    content = raw.decode("utf-8", errors="replace")
                except Exception:
                    content = item.get("content", "")

            data = {
                "path": item.get("path"),
                "name": item.get("name"),
                "sha": item.get("sha"),
                "size": item.get("size", 0),
                "encoding": item.get("encoding"),
                "content": content,
                "html_url": item.get("html_url"),
                "download_url": item.get("download_url"),
                "ref": ref,
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return self._error(tool_name, risk, f"File not found: {path}", started)
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def search_code(
        self,
        owner: str,
        repo: str,
        query: str,
        per_page: int = 10,
    ) -> ToolResult:
        tool_name = "search_code"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not query or not query.strip():
            return self._error(tool_name, risk, "Search query is required.", started)
        if not 1 <= per_page <= 100:
            return self._error(tool_name, risk, "per_page must be between 1 and 100.", started)

        search_query = f"repo:{owner}/{repo} {query.strip()}"
        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/search/code",
                headers=self._headers(),
                params={"q": search_query, "per_page": per_page},
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items", [])
            results = []
            for item in items:
                results.append({
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "sha": item.get("sha"),
                    "html_url": item.get("html_url"),
                    "repository": item.get("repository", {}).get("full_name"),
                })
            data = {
                "query": query,
                "total_count": payload.get("total_count", 0),
                "count": len(results),
                "results": results,
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub search returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def get_recent_commits(
        self,
        owner: str,
        repo: str,
        per_page: int = 20,
        branch: str | None = None,
    ) -> ToolResult:
        tool_name = "get_recent_commits"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not 1 <= per_page <= 100:
            return self._error(tool_name, risk, "per_page must be between 1 and 100.", started)

        params: dict[str, Any] = {"per_page": per_page}
        if branch:
            params["sha"] = branch

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/commits",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            items = response.json()
            commits = []
            for item in items:
                commit = item.get("commit", {})
                author = commit.get("author", {})
                commits.append({
                    "sha": item.get("sha"),
                    "message": commit.get("message"),
                    "author_name": author.get("name"),
                    "author_email": author.get("email"),
                    "date": author.get("date"),
                    "login": item.get("author", {}).get("login") if item.get("author") else None,
                    "html_url": item.get("html_url"),
                    "parents_count": len(item.get("parents", [])),
                })
            return self._success(tool_name, risk, {"count": len(commits), "commits": commits}, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def get_ci_status(self, owner: str, repo: str, ref: str) -> ToolResult:
        tool_name = "get_ci_status"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not ref or not ref.strip():
            return self._error(tool_name, risk, "ref (branch, SHA) is required.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.get(
                f"{self.api_url}/repos/{owner}/{repo}/commits/{ref}/check-runs",
                headers=self._headers(),
                params={"per_page": 50},
            )
            response.raise_for_status()
            payload = response.json()
            runs = payload.get("check_runs", [])
            check_runs = []
            for r in runs:
                check_runs.append({
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "status": r.get("status"),
                    "conclusion": r.get("conclusion"),
                    "started_at": r.get("started_at"),
                    "completed_at": r.get("completed_at"),
                    "html_url": r.get("html_url"),
                })
            success_count = sum(1 for c in check_runs if c["conclusion"] == "success")
            failure_count = sum(1 for c in check_runs if c["conclusion"] in {"failure", "timed_out", "action_required", "cancelled"})
            pending_count = sum(1 for c in check_runs if c["status"] != "completed")
            data = {
                "ref": ref,
                "total_count": payload.get("total_count", 0),
                "check_runs": check_runs,
                "summary": {
                    "success": success_count,
                    "failed": failure_count,
                    "pending": pending_count,
                    "overall": self._overall_ci_status(check_runs),
                },
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        from_sha: str,
    ) -> ToolResult:
        tool_name = "create_branch"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not _BRANCH_NAME_PATTERN.fullmatch(branch_name):
            return self._error(tool_name, risk, "Invalid branch name. Use letters, numbers, dots, dashes, underscores, or slashes.", started)
        if not _SHA_PATTERN.fullmatch(from_sha):
            return self._error(tool_name, risk, "from_sha must be a valid 40-character commit SHA.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.post(
                f"{self.api_url}/repos/{owner}/{repo}/git/refs",
                headers=self._headers(),
                json={"ref": f"refs/heads/{branch_name}", "sha": from_sha},
            )
            response.raise_for_status()
            payload = response.json()
            data = {
                "ref": payload.get("ref"),
                "sha": payload.get("object", {}).get("sha"),
                "branch_name": branch_name,
                "html_url": f"https://github.com/{owner}/{repo}/tree/{branch_name}",
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                return self._error(tool_name, risk, f"Branch '{branch_name}' likely already exists or SHA is invalid.", started)
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def modify_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        branch: str,
        message: str,
        current_sha: str | None = None,
    ) -> ToolResult:
        tool_name = "modify_file"
        risk = RiskLevel.medium
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not path or not path.strip() or ".." in path.split("/"):
            return self._error(tool_name, risk, "Invalid file path.", started)
        if not _BRANCH_NAME_PATTERN.fullmatch(branch):
            return self._error(tool_name, risk, "Invalid branch name.", started)
        if not message or not message.strip():
            return self._error(tool_name, risk, "Commit message is required.", started)
        if len(content.encode("utf-8")) > 1024 * 1024:
            return self._error(tool_name, risk, "Content exceeds 1MB limit for single file modification.", started)

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body: dict[str, Any] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if current_sha:
            if not _SHA_PATTERN.fullmatch(current_sha):
                return self._error(tool_name, risk, "current_sha must be a valid 40-character SHA if provided.", started)
            body["sha"] = current_sha

        client, close_after = await self._get_client()
        try:
            response = await client.put(
                f"{self.api_url}/repos/{owner}/{repo}/contents/{path.lstrip('/')}",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            commit_info = payload.get("commit", {})
            data = {
                "path": path,
                "branch": branch,
                "commit_sha": commit_info.get("sha"),
                "commit_html_url": commit_info.get("html_url"),
                "commit_message": commit_info.get("commit", {}).get("message"),
                "is_new": current_sha is None,
                "file_sha": payload.get("content", {}).get("sha") if payload.get("content") else None,
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body_err = exc.response.json()
                detail = f" - {body_err.get('message', '')}"
            except Exception:
                pass
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}{detail}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def create_commit(
        self,
        owner: str,
        repo: str,
        message: str,
        tree_sha: str,
        parent_sha: str,
    ) -> ToolResult:
        tool_name = "create_commit"
        risk = RiskLevel.medium
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not message or not message.strip():
            return self._error(tool_name, risk, "Commit message is required.", started)
        if not _SHA_PATTERN.fullmatch(tree_sha):
            return self._error(tool_name, risk, "tree_sha must be a valid 40-character SHA.", started)
        if not _SHA_PATTERN.fullmatch(parent_sha):
            return self._error(tool_name, risk, "parent_sha must be a valid 40-character SHA.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.post(
                f"{self.api_url}/repos/{owner}/{repo}/git/commits",
                headers=self._headers(),
                json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
            )
            response.raise_for_status()
            payload = response.json()
            data = {
                "sha": payload.get("sha"),
                "message": payload.get("message"),
                "html_url": f"https://github.com/{owner}/{repo}/commit/{payload.get('sha')}",
                "author": payload.get("author"),
                "committer": payload.get("committer"),
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head_branch: str,
        base_branch: str,
        body: str = "",
        draft: bool = False,
    ) -> ToolResult:
        tool_name = "create_pull_request"
        risk = RiskLevel.medium
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not title or not title.strip():
            return self._error(tool_name, risk, "PR title is required.", started)
        if not _BRANCH_NAME_PATTERN.fullmatch(head_branch):
            return self._error(tool_name, risk, "Invalid head branch name.", started)
        if not _BRANCH_NAME_PATTERN.fullmatch(base_branch):
            return self._error(tool_name, risk, "Invalid base branch name.", started)
        if head_branch == base_branch:
            return self._error(tool_name, risk, "head_branch and base_branch must differ.", started)

        client, close_after = await self._get_client()
        try:
            response = await client.post(
                f"{self.api_url}/repos/{owner}/{repo}/pulls",
                headers=self._headers(),
                json={
                    "title": title,
                    "head": head_branch,
                    "base": base_branch,
                    "body": body,
                    "draft": draft,
                },
            )
            response.raise_for_status()
            payload = response.json()
            data = {
                "number": payload.get("number"),
                "title": payload.get("title"),
                "state": payload.get("state"),
                "html_url": payload.get("html_url"),
                "diff_url": payload.get("diff_url"),
                "head_branch": head_branch,
                "base_branch": base_branch,
                "draft": payload.get("draft", draft),
                "created_at": payload.get("created_at"),
                "user": payload.get("user", {}).get("login"),
            }
            return self._success(tool_name, risk, data, started)
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body_err = exc.response.json()
                detail = f" - {body_err.get('message', '')}"
                for err in body_err.get("errors", []):
                    detail += f" [{err.get('message', '')}]"
            except Exception:
                pass
            return self._error(tool_name, risk, f"GitHub returned {exc.response.status_code}{detail}.", started)
        except httpx.HTTPError as exc:
            return self._error(tool_name, risk, f"GitHub request failed: {exc.__class__.__name__}.", started)
        finally:
            if close_after:
                await client.aclose()

    async def run_tests(
        self,
        owner: str,
        repo: str,
        command: str = "pytest",
        timeout_seconds: int = 120,
    ) -> ToolResult:
        tool_name = "run_tests"
        risk = RiskLevel.low
        started = time.perf_counter()
        validation_error = self._validate_repo_ref(owner, repo)
        if validation_error:
            return self._error(tool_name, risk, validation_error, started)
        if not command or not command.strip():
            return self._error(tool_name, risk, "Test command is required.", started)
        if not 10 <= timeout_seconds <= 600:
            return self._error(tool_name, risk, "timeout_seconds must be between 10 and 600.", started)
        if any(token in command.lower() for token in {"rm -rf", "format c:", "del /f", "drop table", "shutdown", "curl", "wget", "&&"}):
            return self._error(tool_name, risk, "Command contains potentially unsafe tokens.", started)

        from backend.app.tools.testing import LocalTestRunner
        runner = LocalTestRunner(timeout_seconds=timeout_seconds)
        result = await runner.run(command=command, owner=owner, repo=repo)
        result.tool_name = tool_name
        result.risk = risk
        result.duration_ms = self._elapsed_ms(started)
        return result

    @staticmethod
    def _priority_score(issue: dict[str, Any]) -> int:
        score = 0
        labels = [l.lower() for l in (lbl.get("name", "") for lbl in issue.get("labels", []))]
        if "bug" in labels:
            score += 30
        if "critical" in labels or "urgent" in labels:
            score += 50
        if "high" in labels or "priority-high" in labels or "p0" in labels or "p1" in labels:
            score += 40
        if "medium" in labels or "p2" in labels:
            score += 20
        if "enhancement" in labels:
            score += 10
        if "documentation" in labels:
            score += 5
        if "good first issue" in labels:
            score += 15
        score += min(issue.get("comments", 0), 10) * 2
        return score

    @staticmethod
    def _overall_ci_status(check_runs: list[dict[str, Any]]) -> str:
        completed = [c for c in check_runs if c["status"] == "completed"]
        if not completed:
            if not check_runs:
                return "no_runs"
            return "pending"
        if all(c["conclusion"] == "success" for c in completed):
            return "success"
        if any(c["conclusion"] in {"failure", "timed_out", "action_required"} for c in completed):
            return "failed"
        return "mixed"
