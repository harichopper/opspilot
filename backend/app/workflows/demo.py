from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from backend.app.config.settings import Settings
from backend.app.models import ToolResult, ToolStatus, RiskLevel


DEMO_OWNER = "opspilot"
DEMO_REPO = "demo-repo"
DEMO_DEFAULT_BRANCH = "main"
DEMO_HEAD_SHA = "a" * 40
DEMO_FIX_BRANCH = "opspilot/fix-auth-test"


DEMO_REPOSITORY_DATA: dict[str, Any] = {
    "id": 99999999,
    "full_name": f"{DEMO_OWNER}/{DEMO_REPO}",
    "description": "Seeded demo repository for OpsPilot hackathon demo - contains reproducible engineering tasks.",
    "private": False,
    "default_branch": DEMO_DEFAULT_BRANCH,
    "open_issues_count": 5,
    "forks_count": 0,
    "stargazers_count": 1,
    "language": "Python",
    "topics": ["demo", "opspilot", "hackathon"],
    "pushed_at": "2026-08-26T00:00:00Z",
    "created_at": "2026-08-20T00:00:00Z",
    "updated_at": "2026-08-26T00:00:00Z",
    "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}",
    "clone_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}.git",
    "license": {"spdx_id": "MIT"},
}


DEMO_ISSUES: list[dict[str, Any]] = [
    {
        "number": 101,
        "title": "Authentication test suite is flaky - 3 tests fail intermittently",
        "state": "open",
        "body": "The JWT validation tests in tests/auth/test_token.py randomly fail on CI. Investigation needed; likely a timing issue with clock skew handling.",
        "labels": [
            {"name": "bug"},
            {"name": "critical"},
            {"name": "priority-high"},
        ],
        "assignees": [{"login": "dev-lead"}],
        "user": {"login": "qa-engineer"},
        "created_at": "2026-08-25T08:00:00Z",
        "updated_at": "2026-08-26T00:10:00Z",
        "comments": 12,
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/issues/101",
    },
    {
        "number": 97,
        "title": "Update httpx dependency from 0.26.x to 0.27.x",
        "state": "open",
        "body": "Our current httpx version is behind the latest stable. We need to upgrade for security fixes. Check compatibility with tests.",
        "labels": [{"name": "dependencies"}, {"name": "enhancement"}, {"name": "good first issue"}],
        "assignees": [],
        "user": {"login": "dependabot"},
        "created_at": "2026-08-22T10:00:00Z",
        "updated_at": "2026-08-24T16:30:00Z",
        "comments": 3,
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/issues/97",
    },
    {
        "number": 88,
        "title": "Improve error message when API rate limit is exceeded",
        "state": "open",
        "body": "Currently we return a generic 500. Users should see a 429 with a retry-after hint.",
        "labels": [{"name": "enhancement"}, {"name": "api"}],
        "assignees": [],
        "user": {"login": "product-manager"},
        "created_at": "2026-08-15T12:00:00Z",
        "updated_at": "2026-08-18T09:15:00Z",
        "comments": 1,
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/issues/88",
    },
    {
        "number": 76,
        "title": "Add project conventions document for new contributors",
        "state": "open",
        "body": "We should document testing conventions (pytest) and our branching model so new hires are productive faster.",
        "labels": [{"name": "documentation"}],
        "assignees": [],
        "user": {"login": "tech-lead"},
        "created_at": "2026-08-05T14:00:00Z",
        "updated_at": "2026-08-06T11:00:00Z",
        "comments": 0,
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/issues/76",
    },
    {
        "number": 54,
        "title": "Staging deployment fails occasionally",
        "state": "open",
        "body": "Cloud Run staging revision sometimes fails to become healthy. Low occurrence, but blocks releases.",
        "labels": [{"name": "bug"}, {"name": "deployment"}, {"name": "medium"}],
        "assignees": [{"login": "sre"}],
        "user": {"login": "release-manager"},
        "created_at": "2026-07-28T09:00:00Z",
        "updated_at": "2026-08-20T15:00:00Z",
        "comments": 5,
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/issues/54",
    },
]


DEMO_PRS: list[dict[str, Any]] = [
    {
        "number": 120,
        "title": "WIP: Migrate logging to structured JSON",
        "state": "open",
        "body": "Initial cut at structured logging. Need to fix two tests before this can land.",
        "user": {"login": "backend-eng"},
        "head_ref": "feature/json-logs",
        "base_ref": DEMO_DEFAULT_BRANCH,
        "mergeable": False,
        "mergeable_state": "dirty",
        "created_at": "2026-08-24T10:00:00Z",
        "updated_at": "2026-08-25T18:00:00Z",
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/pull/120",
        "additions": 312,
        "deletions": 88,
        "changed_files": 7,
        "labels": [{"name": "WIP"}],
    },
]


DEMO_COMMITS: list[dict[str, Any]] = [
    {
        "sha": DEMO_HEAD_SHA,
        "message": "Initial demo state with failing auth tests\n\nThis is a known seeded state for the hackathon demo.",
        "author_name": "OpsPilot Demo Setup",
        "author_email": "demo@opspilot.local",
        "date": "2026-08-26T00:00:00Z",
        "login": "opspilot-bot",
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/commit/{DEMO_HEAD_SHA}",
        "parents_count": 1,
    },
    {
        "sha": "b" * 40,
        "message": "Add CI workflow for backend tests",
        "author_name": "ci-bot",
        "author_email": "ci@opspilot.local",
        "date": "2026-08-25T22:00:00Z",
        "login": "opspilot-bot",
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/commit/{'b' * 40}",
        "parents_count": 1,
    },
    {
        "sha": "c" * 40,
        "message": "Update README with onboarding steps",
        "author_name": "tech-writer",
        "author_email": "docs@opspilot.local",
        "date": "2026-08-25T15:00:00Z",
        "login": "opspilot-bot",
        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/commit/{'c' * 40}",
        "parents_count": 1,
    },
]


FAILING_AUTH_TOKEN_PY = '''"""JWT token validation helpers.

Seeded file for the OpsPilot hackathon demo.
This module contains a deliberately introduced bug (leeway=0) so the
auth tests are flaky. OpsPilot is expected to fix it by increasing leeway.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenClaims:
    sub: str
    exp: int
    iat: int
    iss: str


def _now_ts() -> int:
    return int(time.time())


def validate_token_expiry(
    claims: TokenClaims,
    now_ts: Optional[int] = None,
    leeway_seconds: int = 0,
) -> bool:
    """Return True if the token is not expired, accounting for leeway."""
    now = now_ts if now_ts is not None else _now_ts()
    return claims.exp + leeway_seconds >= now


def validate_token_issued_at(
    claims: TokenClaims,
    now_ts: Optional[int] = None,
    leeway_seconds: int = 0,
) -> bool:
    """Return True if the token was issued in the past, accounting for leeway.

    BUG: leeway is currently not applied (seeded for demo). OpsPilot must
    replace the comparison so leeway is used symmetrically.
    """
    now = now_ts if now_ts is not None else _now_ts()
    return claims.iat <= now
'''


TEST_AUTH_TOKEN_PY = '''"""Seeded auth tests for OpsPilot demo.

The flaky tests are:
- test_validate_token_expiry_allows_1s_skew
- test_validate_token_issued_at_allows_small_clock_skew
- test_validate_expiry_and_iat_within_1s

They all fail because auth/token.py introduces leeway=0 and doesn't apply
leeway to iat validation. The fix is in the top priority issue (#101).
"""
from __future__ import annotations

import pytest

from demo_project.auth.token import TokenClaims, validate_token_expiry, validate_token_issued_at


def test_validate_token_expiry_rejects_expired():
    claims = TokenClaims(sub="user-1", exp=1000, iat=900, iss="demo")
    assert validate_token_expiry(claims, now_ts=1001, leeway_seconds=5) is False


def test_validate_token_expiry_allows_1s_skew():
    claims = TokenClaims(sub="user-1", exp=1000, iat=900, iss="demo")
    assert validate_token_expiry(claims, now_ts=1000, leeway_seconds=5) is True


def test_validate_token_expiry_uses_leeway():
    claims = TokenClaims(sub="user-1", exp=1000, iat=900, iss="demo")
    assert validate_token_expiry(claims, now_ts=1002, leeway_seconds=5) is True


def test_validate_token_issued_at_allows_small_clock_skew():
    claims = TokenClaims(sub="user-2", exp=2000, iat=1005, iss="demo")
    assert validate_token_issued_at(claims, now_ts=1000, leeway_seconds=10) is True


def test_validate_issued_at_rejects_far_future():
    claims = TokenClaims(sub="user-2", exp=2000, iat=1500, iss="demo")
    assert validate_token_issued_at(claims, now_ts=1000, leeway_seconds=10) is False


def test_validate_expiry_and_iat_within_1s():
    claims = TokenClaims(sub="user-3", exp=1000, iat=1000, iss="demo")
    assert validate_token_issued_at(claims, now_ts=1000, leeway_seconds=5) is True
    assert validate_token_expiry(claims, now_ts=1000, leeway_seconds=5) is True
'''


REQUIREMENTS_TXT = """httpx==0.26.0
pytest==8.2.0
pydantic==2.7.0
fastapi==0.114.0
"""


FIXED_AUTH_TOKEN_PY = '''"""JWT token validation helpers.

Fixed by OpsPilot: leeway now properly applied to both expiry and issued-at
checks to eliminate flaky behaviour due to clock skew.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenClaims:
    sub: str
    exp: int
    iat: int
    iss: str


def _now_ts() -> int:
    return int(time.time())


def validate_token_expiry(
    claims: TokenClaims,
    now_ts: Optional[int] = None,
    leeway_seconds: int = 0,
) -> bool:
    """Return True if the token is not expired, accounting for leeway."""
    now = now_ts if now_ts is not None else _now_ts()
    return claims.exp + leeway_seconds >= now


def validate_token_issued_at(
    claims: TokenClaims,
    now_ts: Optional[int] = None,
    leeway_seconds: int = 0,
) -> bool:
    """Return True if the token was issued in the past, accounting for leeway."""
    now = now_ts if now_ts is not None else _now_ts()
    return claims.iat - leeway_seconds <= now
'''


FIXED_REQUIREMENTS_TXT = """httpx==0.27.2
pytest==8.3.2
pydantic==2.8.2
fastapi==0.115.0
"""


@dataclass
class DemoWorkspace:
    root: Path

    def write_initial(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        auth_dir = self.root / "demo_project" / "auth"
        tests_dir = self.root / "tests"
        auth_dir.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)

        (auth_dir / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "demo_project" / "__init__.py").write_text("", encoding="utf-8")
        (auth_dir / "token.py").write_text(FAILING_AUTH_TOKEN_PY, encoding="utf-8")
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        (tests_dir / "test_auth_token.py").write_text(TEST_AUTH_TOKEN_PY, encoding="utf-8")
        (self.root / "requirements.txt").write_text(REQUIREMENTS_TXT, encoding="utf-8")

    def apply_fixes(self) -> None:
        auth_dir = self.root / "demo_project" / "auth"
        (auth_dir / "token.py").write_text(FIXED_AUTH_TOKEN_PY, encoding="utf-8")
        (self.root / "requirements.txt").write_text(FIXED_REQUIREMENTS_TXT, encoding="utf-8")


def make_demo_workspace() -> DemoWorkspace:
    path = Path(tempfile.mkdtemp(prefix="opspilot-demo-workspace-"))
    ws = DemoWorkspace(path)
    ws.write_initial()
    return ws


class DemoGitHubTransport:
    """A httpx.MockTransport serving seeded demo repository data.

    The orchestrator points the GitHub toolkit at this transport when the
    caller requests demo_mode. Everything is still executed through the
    real tool surface (input validation, risk classification, policy checks,
    logging) - only the HTTP responses are seeded for a deterministic 4-minute
    demo.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._branches: dict[str, str] = {DEMO_DEFAULT_BRANCH: DEMO_HEAD_SHA}
        self._file_shas: dict[tuple[str, str, str], str] = {}
        self._pr_counter = 150
        self._created_prs: list[dict[str, Any]] = []

    def _make_handler(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            method = request.method.upper()
            qs = dict(request.url.params)

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}":
                return httpx.Response(200, json=DEMO_REPOSITORY_DATA)

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/issues":
                state = qs.get("state", "open")
                items = [i for i in DEMO_ISSUES if state == "all" or i["state"] == state]
                return httpx.Response(200, json=items)

            if path.startswith(f"/repos/{DEMO_OWNER}/{DEMO_REPO}/issues/"):
                number = int(path.rsplit("/", 1)[-1])
                for issue in DEMO_ISSUES:
                    if issue["number"] == number:
                        payload = dict(issue)
                        return httpx.Response(200, json=payload)
                return httpx.Response(404, json={"message": "Not found"})

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/pulls" and method == "GET":
                state = qs.get("state", "open")
                items = [p for p in DEMO_PRS + self._created_prs if state == "all" or p["state"] == state]
                return httpx.Response(200, json=items)

            if path.startswith(f"/repos/{DEMO_OWNER}/{DEMO_REPO}/pulls/"):
                number = int(path.rsplit("/", 1)[-1])
                for pr in DEMO_PRS + self._created_prs:
                    if pr["number"] == number:
                        return httpx.Response(200, json=pr)
                return httpx.Response(404, json={"message": "Not found"})

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/commits":
                branch = qs.get("sha")
                items = list(DEMO_COMMITS)
                if branch and branch in self._branches:
                    items = [DEMO_COMMITS[0]]
                return httpx.Response(200, json=items)

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/commits/{DEMO_HEAD_SHA}/check-runs":
                return httpx.Response(200, json={
                    "total_count": 1,
                    "check_runs": [{
                        "id": 1,
                        "name": "Backend pytest (seeded, pre-fix)",
                        "status": "completed",
                        "conclusion": "failure",
                        "started_at": "2026-08-26T00:05:00Z",
                        "completed_at": "2026-08-26T00:06:00Z",
                        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/runs/1",
                    }],
                })

            contents_prefix = f"/repos/{DEMO_OWNER}/{DEMO_REPO}/contents/"
            if path.startswith(contents_prefix) and method == "GET":
                rel = path[len(contents_prefix):]
                ref = qs.get("ref", DEMO_DEFAULT_BRANCH)
                if rel == "":
                    return httpx.Response(200, json=[
                        {"name": "demo_project", "path": "demo_project", "type": "dir", "sha": "d0" * 20, "size": 0},
                        {"name": "tests", "path": "tests", "type": "dir", "sha": "d1" * 20, "size": 0},
                        {"name": "requirements.txt", "path": "requirements.txt", "type": "file", "sha": "f0" * 20, "size": len(REQUIREMENTS_TXT)},
                    ])
                body, sha = self._file_contents(rel, ref)
                if body is None:
                    return httpx.Response(404, json={"message": "Not found"})
                import base64
                encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
                return httpx.Response(200, json={
                    "path": rel,
                    "name": rel.rsplit("/", 1)[-1],
                    "sha": sha,
                    "size": len(body.encode("utf-8")),
                    "encoding": "base64",
                    "content": encoded,
                    "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/blob/{ref}/{rel}",
                    "download_url": None,
                })

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/git/refs" and method == "POST":
                import json as _json
                body = _json.loads(request.content.decode("utf-8"))
                ref = body.get("ref", "")
                sha = body.get("sha", "")
                if ref.startswith("refs/heads/"):
                    branch = ref[len("refs/heads/"):]
                    self._branches[branch] = sha
                    return httpx.Response(201, json={"ref": ref, "object": {"sha": sha}})
                return httpx.Response(422, json={"message": "Unprocessable"})

            if path.startswith(contents_prefix) and method == "PUT":
                import json as _json
                import base64 as _b64
                rel = path[len(contents_prefix):]
                body = _json.loads(request.content.decode("utf-8"))
                branch = body.get("branch", DEMO_DEFAULT_BRANCH)
                encoded_content = body.get("content", "")
                message = body.get("message", "update")
                decoded = _b64.b64decode(encoded_content).decode("utf-8", errors="replace")
                new_sha = "f" + secrets.token_hex(19)
                self._file_shas[(branch, rel, message)] = new_sha
                commit_sha = "c" + secrets.token_hex(19)
                return httpx.Response(200, json={
                    "content": {"path": rel, "sha": new_sha},
                    "commit": {
                        "sha": commit_sha,
                        "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/commit/{commit_sha}",
                        "commit": {"message": message},
                    },
                })

            if path == f"/repos/{DEMO_OWNER}/{DEMO_REPO}/pulls" and method == "POST":
                import json as _json
                body = _json.loads(request.content.decode("utf-8"))
                self._pr_counter += 1
                pr = {
                    "number": self._pr_counter,
                    "title": body.get("title"),
                    "state": "open",
                    "body": body.get("body", ""),
                    "user": {"login": "opspilot-bot"},
                    "head_ref": body.get("head"),
                    "base_ref": body.get("base"),
                    "mergeable": True,
                    "mergeable_state": "clean",
                    "created_at": _demots(),
                    "updated_at": _demots(),
                    "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/pull/{self._pr_counter}",
                    "diff_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/pull/{self._pr_counter}.diff",
                    "additions": 42,
                    "deletions": 18,
                    "changed_files": 2,
                    "labels": [],
                    "draft": body.get("draft", False),
                    "head": {"ref": body.get("head"), "sha": DEMO_HEAD_SHA},
                    "base": {"ref": body.get("base")},
                    "commits": 1,
                    "comments": 0,
                    "review_comments": 0,
                    "requested_reviewers": [],
                }
                self._created_prs.append(pr)
                return httpx.Response(201, json=pr)

            if path.startswith("/search/code"):
                q = qs.get("q", "")
                hits = []
                if "auth" in q.lower() or "token" in q.lower():
                    hits.append({"name": "token.py", "path": "demo_project/auth/token.py", "sha": "f2" * 20,
                                 "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/blob/main/demo_project/auth/token.py",
                                 "repository": {"full_name": f"{DEMO_OWNER}/{DEMO_REPO}"}})
                if "test" in q.lower():
                    hits.append({"name": "test_auth_token.py", "path": "tests/test_auth_token.py", "sha": "f3" * 20,
                                 "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/blob/main/tests/test_auth_token.py",
                                 "repository": {"full_name": f"{DEMO_OWNER}/{DEMO_REPO}"}})
                if "requirements" in q.lower():
                    hits.append({"name": "requirements.txt", "path": "requirements.txt", "sha": "f0" * 20,
                                 "html_url": f"https://github.com/{DEMO_OWNER}/{DEMO_REPO}/blob/main/requirements.txt",
                                 "repository": {"full_name": f"{DEMO_OWNER}/{DEMO_REPO}"}})
                return httpx.Response(200, json={"total_count": len(hits), "items": hits})

            return httpx.Response(404, json={"message": "Not found in demo transport"})

        return handler

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._make_handler()), timeout=20)

    def _file_contents(self, path: str, ref: str) -> tuple[str | None, str]:
        import base64
        if ref == DEMO_FIX_BRANCH and path == "demo_project/auth/token.py":
            body = FIXED_AUTH_TOKEN_PY
            sha = "af" * 20
            return body, sha
        if ref == DEMO_FIX_BRANCH and path == "requirements.txt":
            body = FIXED_REQUIREMENTS_TXT
            sha = "ae" * 20
            return body, sha
        if path == "demo_project/auth/token.py":
            return FAILING_AUTH_TOKEN_PY, "aa" * 20
        if path == "tests/test_auth_token.py":
            return TEST_AUTH_TOKEN_PY, "ab" * 20
        if path == "requirements.txt":
            return REQUIREMENTS_TXT, "f0" * 20
        return None, "00" * 20


def _demots() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def priority_score_issue(issue: dict[str, Any]) -> int:
    score = 0
    labels = []
    for lbl in issue.get("labels", []):
        if isinstance(lbl, dict):
            labels.append(lbl.get("name", "").lower())
        elif isinstance(lbl, str):
            labels.append(lbl.lower())
    
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


DEMO_SEEDED_FAILURES_COUNT = 3
DEMO_SEEDED_ISSUES_COUNT = len(DEMO_ISSUES)
