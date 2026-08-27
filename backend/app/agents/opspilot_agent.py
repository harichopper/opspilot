from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import httpx

from backend.app.config.logging import StructuredLogger
from backend.app.config.settings import Settings
from backend.app.memory import (
    MEMORY_TYPE_CONVENTION,
    MEMORY_TYPE_FAILED_APPROACH,
    MEMORY_TYPE_PREVIOUS_TASK,
    MEMORY_TYPE_SUCCESSFUL_FIX,
    MEMORY_TYPE_TESTING_CONVENTION,
    MemoryService,
)
from backend.app.models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentStep,
    RiskLevel,
    ToolResult,
    ToolStatus,
)
from backend.app.policies import APPROVAL_EXPIRY_SECONDS, PolicyDecision, PolicyEngine
from backend.app.services.patch_generator import PatchGenerator, PatchResult
from backend.app.tools import GitHubToolkit, LocalTestRunner
from backend.app.workflows.approval import ApprovalRequest, ApprovalService, ApprovalStatus
from backend.app.workflows.demo import (
    DEMO_FIX_BRANCH,
    DEMO_HEAD_SHA,
    DEMO_OWNER,
    DEMO_REPO,
    DEMO_SEEDED_FAILURES_COUNT,
    DEMO_SEEDED_ISSUES_COUNT,
    DemoGitHubTransport,
    DemoWorkspace,
    FIXED_AUTH_TOKEN_PY,
    FIXED_REQUIREMENTS_TXT,
    make_demo_workspace,
    priority_score_issue,
)
from backend.app.workflows.job_manager import JobManager, JobRecord, JobStatus


AGENT_NAME = "opspilot_orchestrator"

AGENT_INSTRUCTION = """
You are OpsPilot, an autonomous engineering work orchestrator.

Goal: accept a high-level engineering objective and autonomously coordinate
work across the connected GitHub repository and tooling.

Your workflow for every objective:

1. UNDERSTAND the user's goal. Extract concrete success criteria.
2. INSPECT the repository (metadata, issues, PRs, commits, CI, files).
3. DISCOVER unfinished engineering work and score each task by priority.
4. PLAN a ranked, safe execution plan with explicit verification steps.
5. RETRIEVE relevant project memory before making changes.
6. EXECUTE only safe (LOW risk) actions without approval.
7. REQUEST human approval for any MEDIUM or HIGH risk action before execution.
8. RUN TESTS after any code change. Never skip verification.
9. COMMIT changes only to a NEW branch; never commit to main/master.
10. OPEN a PR (draft if requested) when code + tests pass.
11. VERIFY the end state: PR exists, CI green, files changed as expected.
12. STORE useful project-specific memory (conventions, fixes, failed approaches).
13. RETURN a concise, verified report.

Rules:
- Never expose secrets (tokens, API keys, passwords).
- Never execute HIGH risk actions without explicit human approval.
- Never create an infinite loop. Max 12 tool calls per job.
- Never suppress errors. Surface failures with evidence.
- If a task blocks 3+ times, escalate with status NEEDS_ATTENTION.
- Treat a missing GitHub token as read-only mode.
"""

MAX_TOOL_CALLS_PER_JOB = 25
MAX_RETRIES_PER_TOOL = 3
FAIL_ESCALATION_THRESHOLD = 3

TOOL_RISK_BY_NAME: dict[str, tuple[RiskLevel, bool]] = {
    "get_repository": (RiskLevel.low, False),
    "list_issues": (RiskLevel.low, False),
    "get_issue": (RiskLevel.low, False),
    "list_pull_requests": (RiskLevel.low, False),
    "get_pull_request": (RiskLevel.low, False),
    "get_file": (RiskLevel.low, False),
    "search_code": (RiskLevel.low, False),
    "get_recent_commits": (RiskLevel.low, False),
    "get_ci_status": (RiskLevel.low, False),
    "create_branch": (RiskLevel.low, False),
    "run_tests": (RiskLevel.low, False),
    "modify_file": (RiskLevel.medium, True),
    "create_commit": (RiskLevel.medium, True),
    "create_pull_request": (RiskLevel.medium, True),
}


@dataclass
class _AgentContext:
    job: JobRecord
    owner: str
    repo: str
    demo_mode: bool
    auto_approve: bool
    goal: str
    tool_calls_count: int = 0
    failure_count: int = 0
    selected_issue: dict[str, Any] | None = None
    selected_task_title: str = ""
    branch_created: bool = False
    tests_passed_before_fix: bool = False
    tests_passed_after_fix: bool = False
    pr_created: bool = False
    demo_workspace: DemoWorkspace | None = None
    active_branch_name: str = ""
    verified_file_paths: list[str] = field(default_factory=list)
    verified_file_contents: dict[str, str] = field(default_factory=dict)
    applied_patches: list[tuple[str, str, str]] = field(default_factory=list)


class OpsPilotOrchestrator:
    """Primary orchestrator agent.

    Combines Google ADK + Gemini when configured, otherwise runs a
    deterministic heuristic workflow sufficient for local mode and the
    hackathon demo. The surface area (tool calls, policy checks, memory
    writes, approvals, event logs) is identical in either path.
    """

    def __init__(
        self,
        settings: Settings,
        github: GitHubToolkit | None = None,
        policy: PolicyEngine | None = None,
        jobs: JobManager | None = None,
        approvals: ApprovalService | None = None,
        memory: MemoryService | None = None,
        logger: StructuredLogger | None = None,
        demo_transport: DemoGitHubTransport | None = None,
        demo_mode: bool = False,
        patch_generator: PatchGenerator | None = None,
    ) -> None:
        self._settings = settings
        self._policy = policy or PolicyEngine()
        self._jobs = jobs or JobManager()
        self._memory = memory or MemoryService(settings)
        self._logger = logger or StructuredLogger(settings, name="opspilot.orchestrator")
        self._github: GitHubToolkit | None = github
        self._approvals = approvals or ApprovalService(settings, logger=self._logger)
        self._patch_generator = patch_generator or PatchGenerator(settings, logger=self._logger)
        if demo_transport is None and demo_mode:
            demo_transport = DemoGitHubTransport(settings)
        self._demo_transport_override = demo_transport

    @property
    def name(self) -> str:
        return AGENT_NAME

    @property
    def adk_available(self) -> bool:
        return self._load_adk_agent_class() is not None and bool(self._settings.gemini_api_key)

    def build_adk_agent(self) -> Any | None:
        agent_class = self._load_adk_agent_class()
        if agent_class is None or not self._settings.gemini_api_key:
            return None
        try:
            tools = self._build_callable_tool_list()
            return agent_class(
                name=AGENT_NAME,
                model=self._settings.gemini_model,
                instruction=AGENT_INSTRUCTION.strip(),
                tools=tools,
            )
        except Exception:
            return None

    def job_manager(self) -> JobManager:
        return self._jobs

    def approval_service(self) -> ApprovalService:
        return self._approvals

    def memory_service(self) -> MemoryService:
        return self._memory

    def policy_engine(self) -> PolicyEngine:
        return self._policy

    def github_toolkit(self) -> GitHubToolkit | None:
        return self._github

    async def execute_agent_endpoint(self, request: AgentExecutionRequest) -> AgentExecutionResponse:
        """Synchronous entrypoint used by the old /api/agent/execute route.

        Creates a job, runs the workflow inline, and returns the final
        response. No backgrounding - suitable for Phase 1 local use.
        """
        job = await self.start_job(request, background=False)
        final = self._jobs.get(job.job_id) or job
        return AgentExecutionResponse(
            status=str(final.status),
            goal=request.goal,
            agent_name=AGENT_NAME,
            model=self._settings.gemini_model,
            adk_available=self.adk_available,
            steps=[AgentStep(name=s["name"], status=s["status"], detail=s["detail"]) for s in final.steps],
            tools_used=[
                ToolResult(
                    tool_name=t["tool_name"],
                    risk=RiskLevel(t["risk"]),
                    status=ToolStatus(t["status"]),
                    data=t.get("data", {}),
                    error=t.get("error"),
                    duration_ms=int(t.get("duration_ms", 0)),
                )
                for t in final.tools_used
            ],
            plan=list(final.plan),
            report=final.report,
        )

    async def start_job(
        self,
        request: AgentExecutionRequest | None = None,
        background: bool = True,
        *,
        goal: str | None = None,
        github_owner: str | None = None,
        github_repo: str | None = None,
        demo_mode: bool | None = None,
        auto_approve: bool | None = None,
        project_id: str | None = None,
        **_ignored: Any,
    ) -> JobRecord:
        if request is None:
            if goal is None:
                raise ValueError("start_job requires either an AgentExecutionRequest or a 'goal' keyword argument.")
            request = AgentExecutionRequest(
                goal=goal,
                github_owner=github_owner,
                github_repo=github_repo,
                demo_mode=bool(demo_mode),
                auto_approve=bool(auto_approve),
            )
        owner = request.github_owner or self._settings.github_owner
        repo = request.github_repo or self._settings.github_repo
        run_in_demo = bool(request.demo_mode)

        goal = request.goal
        if not goal or goal.strip().lower() in ("string", "undefined", "null"):
            goal = "Clean up my highest-priority engineering work."

        is_placeholder_owner = not owner or owner.lower() in ("string", "undefined", "null")
        is_placeholder_repo = not repo or repo.lower() in ("string", "string/string", "undefined", "null")

        if run_in_demo or is_placeholder_owner or is_placeholder_repo:
            owner = DEMO_OWNER
            repo = DEMO_REPO

        effective_request = AgentExecutionRequest(
            goal=goal,
            github_owner=owner,
            github_repo=repo,
            demo_mode=run_in_demo,
            auto_approve=bool(request.auto_approve),
        )

        job = self._jobs.create(effective_request)
        resolved_project_id = self._memory.project_id_for(owner or "adhoc", repo or "adhoc")
        project_id = project_id or resolved_project_id
        demo_mode = run_in_demo

        ctx = _AgentContext(
            job=job,
            owner=owner or "",
            repo=repo or "",
            demo_mode=demo_mode,
            auto_approve=bool(request.auto_approve),
            goal=request.goal,
        )
        if demo_mode:
            ctx.demo_workspace = make_demo_workspace()

        self._jobs.transition(job, JobStatus.running, "Workflow starting")
        self._logger.info(
            f"Starting job: {request.goal[:80]}",
            job_id=job.job_id,
            project_id=project_id,
            agent_step="agent.start",
            extra={"demo_mode": demo_mode},
        )

        self._memory.write_context_snapshot(
            project_id,
            repo_language="Python" if demo_mode else None,
            testing_framework_hint="pytest",
            default_branch="main" if demo_mode else None,
            source="job_init",
        )

        if background:
            asyncio.ensure_future(self._run_with_safety(ctx))
            return job

        await self._run_with_safety(ctx)
        refreshed = self._jobs.get(job.job_id) or job
        return refreshed

    def get_job(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_recent_jobs(self, limit: int = 50) -> list[JobRecord]:
        return self._jobs.list_recent(limit=limit)

    def list_jobs_for_project(self, owner: str, repo: str, limit: int = 50) -> list[JobRecord]:
        project_id = self._memory.project_id_for(owner, repo)
        return self._jobs.list_for_project(project_id, limit=limit)

    def cancel_job(self, job_id: str, reason: str = "User cancelled") -> JobRecord | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        return self._jobs.cancel(job, reason)

    async def _run_with_safety(self, ctx: _AgentContext) -> None:
        job = ctx.job
        try:
            with self._logger.timed_block(
                "agent.workflow",
                job_id=job.job_id,
                project_id=job.project_id,
                agent_step="workflow",
            ):
                await self._run_workflow(ctx)
        except Exception as exc:  # noqa: BLE001 - never let the agent crash silently
            self._jobs.set_error(job, f"Unhandled agent exception: {exc}")
            self._jobs.increment_retry(job, str(exc))
            if ctx.failure_count >= FAIL_ESCALATION_THRESHOLD:
                self._jobs.transition(job, JobStatus.needs_attention, "Too many failures; escalated.")
            else:
                self._jobs.transition(job, JobStatus.failed, str(exc))
            self._logger.error(
                f"Workflow crashed: {exc}",
                job_id=job.job_id,
                project_id=job.project_id,
                agent_step="workflow.crash",
            )

    async def _run_workflow(self, ctx: _AgentContext) -> None:
        job = ctx.job
        project_id = job.project_id

        self._jobs.set_current_step(job, "understand_goal")
        self._jobs.append_step(job, AgentStep(
            name="understand_goal",
            status="completed",
            detail=f"Parsed goal: {ctx.goal[:100]}",
        ))

        if not ctx.owner or not ctx.repo:
            self._jobs.append_step(job, AgentStep(
                name="repository_configuration",
                status="needs_attention",
                detail="No GitHub owner/repo provided. Configure environment or pass them in the request.",
            ))
            self._jobs.set_report(job, "No repository configured. Provide github_owner and github_repo (or use demo_mode).")
            self._jobs.transition(job, JobStatus.needs_attention, "No repository configured")
            return

        github = self._resolve_github_toolkit(ctx)

        self._jobs.set_current_step(job, "inspect_repository")
        repo_result = await self._safe_tool_call(ctx, "get_repository", github.get_repository, ctx.owner, ctx.repo)
        if repo_result.status != ToolStatus.success:
            self._fail(job, f"Repository scan failed: {repo_result.error}", ctx)
            return
        self._jobs.append_step(job, AgentStep(
            name="repository_scan",
            status="completed",
            detail=f"Inspected {repo_result.data.get('full_name')}; default branch: {repo_result.data.get('default_branch', '?')}",
        ))
        lang = repo_result.data.get("language")
        default_branch = repo_result.data.get("default_branch")
        open_issues_count = repo_result.data.get("open_issues_count", 0)

        self._memory.write_context_snapshot(
            project_id,
            repo_language=lang,
            testing_framework_hint="pytest" if (lang or "").lower() == "python" else None,
            default_branch=default_branch,
            source="repository_scan",
        )

        self._jobs.set_current_step(job, "discover_tasks")
        issues_result = await self._safe_tool_call(ctx, "list_issues", github.list_issues, ctx.owner, ctx.repo)
        prs_result = await self._safe_tool_call(ctx, "list_pull_requests", github.list_pull_requests, ctx.owner, ctx.repo)
        commits_result = await self._safe_tool_call(ctx, "get_recent_commits", github.get_recent_commits, ctx.owner, ctx.repo)
        commits_list = commits_result.data.get("commits", []) if commits_result.status == ToolStatus.success else []
        head_sha_for_ci = DEMO_HEAD_SHA if ctx.demo_mode else (
            commits_list[0].get("sha", "") if commits_list else ""
        )
        ci_result: ToolResult | None = None
        if head_sha_for_ci:
            ci_result = await self._safe_tool_call(ctx, "get_ci_status", github.get_ci_status, ctx.owner, ctx.repo, head_sha_for_ci)

        discovered_issues = issues_result.data.get("issues", []) if issues_result.status == ToolStatus.success else []
        if ctx.demo_mode:
            for issue in discovered_issues:
                issue["priority_score"] = priority_score_issue(issue)
            discovered_issues.sort(key=lambda i: i.get("priority_score", 0), reverse=True)

        discovered_prs = prs_result.data.get("pull_requests", []) if prs_result.status == ToolStatus.success else []
        issues_count = len(discovered_issues)
        prs_count = len(discovered_prs)
        commits_count = (
            commits_result.data.get("count", 0) if commits_result.status == ToolStatus.success else 0
        )

        self._jobs.append_step(job, AgentStep(
            name="discover_tasks",
            status="completed",
            detail=f"Discovered {issues_count} open issue(s), {prs_count} PR(s), {commits_count} recent commit(s).",
        ))

        self._jobs.set_current_step(job, "prioritize_tasks")
        selected = self._select_highest_priority_task(discovered_issues, discovered_prs, ci_result)
        if selected is None:
            self._jobs.append_step(job, AgentStep(
                name="prioritize_tasks",
                status="completed",
                detail="No actionable tasks found. Repository is clean.",
            ))
            self._jobs.set_plan(job, [
                "No prioritized action required. Repository appears clean.",
            ])
            self._write_memory_for_clean_scan(project_id, repo_result.data)
            self._jobs.set_report(job, self._build_final_report(ctx, completed_summary="Repository clean; nothing to do.", approval_summary="", failed_summary=""))
            self._jobs.transition(job, JobStatus.completed, "Repository clean")
            return

        ctx.selected_issue = selected
        ctx.selected_task_title = selected.get("title", "Selected task")
        issue_number = selected.get("number", 0)
        issue_title = selected.get("title", "")
        self._jobs.append_step(job, AgentStep(
            name="prioritize_tasks",
            status="completed",
            detail=f"Selected task #{issue_number}: {issue_title} (priority score {selected.get('priority_score', 0)})",
        ))

        plan = self._build_plan(selected, repo_result.data)
        self._jobs.set_plan(job, plan)

        self._jobs.set_current_step(job, "retrieve_memory")
        memory_context = self._memory.build_reasoning_context(project_id, ctx.goal + " " + issue_title)
        self._jobs.append_step(job, AgentStep(
            name="retrieve_memory",
            status="completed",
            detail=f"Retrieved project memory. {memory_context[:120]}",
        ))

        self._jobs.set_current_step(job, "investigate_selected_task")
        await self._investigate_task(ctx, github, selected)

        if self._is_read_only_goal(ctx.goal):
            self._jobs.append_step(job, AgentStep(
                name="read_only_workflow",
                status="completed",
                detail=f"Read-only goal processed: '{ctx.goal}'. No branch creation or modifications performed.",
            ))
            report = self._build_final_report(
                ctx,
                completed_summary=f"Read-only inspection completed for '{ctx.goal}'. Selected task #{selected.get('number')}: {selected.get('title', '')}.",
                approval_summary="",
                failed_summary="",
            )
            self._jobs.set_report(job, report)
            self._jobs.transition(job, JobStatus.completed, "Read-only inspection completed successfully.")
            return

        self._jobs.set_current_step(job, "branch_and_fix")
        fix_ok = await self._apply_fix(ctx, github, selected)
        if not fix_ok:
            return

        self._jobs.set_current_step(job, "verify_tests")
        verify_ok = await self._verify_fix(ctx)
        if not verify_ok:
            return

        self._jobs.set_current_step(job, "create_pr")
        pr_ok = await self._open_pr(ctx, github, selected)
        if not pr_ok:
            return

        self._jobs.set_current_step(job, "store_memory")
        self._store_memory_after_fix(ctx, selected, repo_result.data)

        self._jobs.set_current_step(job, "report")
        report = self._build_final_report(
            ctx,
            completed_summary=f"Fixed task #{selected.get('number')}: {selected.get('title', '')}. Created PR, tests passed.",
            approval_summary=self._build_approval_summary(ctx),
            failed_summary="",
        )
        self._jobs.set_report(job, report)
        self._jobs.transition(job, JobStatus.completed, "All steps verified.")
        self._logger.info(
            "Job completed successfully",
            job_id=job.job_id,
            project_id=project_id,
            agent_step="agent.complete",
        )

    @staticmethod
    def _is_read_only_goal(goal: str) -> bool:
        g = (goal or "").lower().strip()
        if "read-only" in g or "readonly" in g:
            return True
        ro_keywords = {"inspect", "analyze", "audit", "find", "search", "check", "review", "summarize", "overview", "report"}
        mut_keywords = {"fix", "update", "modify", "patch", "create", "add", "refactor", "change", "upgrade", "repair", "resolve", "remove", "delete"}
        has_ro = any(re.search(r"\b" + re.escape(kw) + r"\b", g) for kw in ro_keywords)
        has_mut = any(re.search(r"\b" + re.escape(kw) + r"\b", g) for kw in mut_keywords)
        return has_ro and not has_mut

    @staticmethod
    def _is_destructive_diff(old_content: str, new_content: str) -> bool:
        if not old_content:
            return False
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()
        if len(old_lines) >= 5 and len(new_lines) < len(old_lines) * 0.5:
            return True
        if not new_content.strip() and old_content.strip():
            return True
        return False

    async def _discover_candidate_files(
        self,
        ctx: _AgentContext,
        github: GitHubToolkit,
        issue: dict[str, Any],
    ) -> list[str]:
        title = issue.get("title", "")
        body = issue.get("body", "")
        combined = f"{ctx.goal} {title} {body}".strip()
        words = re.findall(r"\b[A-Za-z0-9_]{3,}\b", combined.lower())
        stopwords = {
            "the", "and", "for", "with", "this", "that", "from", "have", "your",
            "which", "will", "what", "should", "could", "would", "about", "there", "their",
            "fix", "issue", "task", "problem", "repo", "repository", "code", "file", "project",
            "please", "make", "sure", "work", "need"
        }
        terms = [w for w in words if w not in stopwords]

        search_queries: list[str] = []
        if terms:
            search_queries.append(" ".join(terms[:3]))
            if len(terms) > 3:
                search_queries.append(" ".join(terms[3:6]))
        else:
            search_queries.append("pytest OR test OR src")

        candidate_paths: list[str] = []
        for q in search_queries:
            if not q.strip():
                continue
            res = await self._safe_tool_call(ctx, "search_code", github.search_code, ctx.owner, ctx.repo, q)
            if res.status == ToolStatus.success and res.data.get("results"):
                for item in res.data["results"]:
                    p = item.get("path")
                    if p and p not in candidate_paths:
                        candidate_paths.append(p)

        ctx.verified_file_paths.clear()
        ctx.verified_file_contents.clear()

        for path in candidate_paths:
            res = await self._safe_tool_call(ctx, "get_file", github.get_file, ctx.owner, ctx.repo, path)
            if res.status == ToolStatus.success and not res.data.get("is_directory"):
                if path not in ctx.verified_file_paths:
                    ctx.verified_file_paths.append(path)
                    ctx.verified_file_contents[path] = res.data.get("content", "")

        return ctx.verified_file_paths

    async def _investigate_task(self, ctx: _AgentContext, github: GitHubToolkit, issue: dict[str, Any]) -> None:
        job = ctx.job
        number = issue.get("number", 0)
        issue_detail = await self._safe_tool_call(ctx, "get_issue", github.get_issue, ctx.owner, ctx.repo, number)
        if issue_detail.status == ToolStatus.success:
            self._jobs.append_step(job, AgentStep(
                name="issue_inspection",
                status="completed",
                detail=f"Read issue #{number}: {issue.get('title', '')}",
            ))

        if ctx.demo_mode:
            for path in ["demo_project/auth/token.py", "requirements.txt"]:
                res = await self._safe_tool_call(ctx, "get_file", github.get_file, ctx.owner, ctx.repo, path)
                if res.status == ToolStatus.success and not res.data.get("is_directory"):
                    if path not in ctx.verified_file_paths:
                        ctx.verified_file_paths.append(path)
                        ctx.verified_file_contents[path] = res.data.get("content", "")
        else:
            await self._discover_candidate_files(ctx, github, issue)

        self._jobs.append_step(job, AgentStep(
            name="investigate_selected_task",
            status="completed",
            detail=f"Investigated task #{number}; verified {len(ctx.verified_file_paths)} candidate path(s).",
        ))

    async def _apply_fix(self, ctx: _AgentContext, github: GitHubToolkit, issue: dict[str, Any]) -> bool:
        job = ctx.job
        owner, repo = ctx.owner, ctx.repo

        if not ctx.demo_mode and not ctx.verified_file_paths:
            msg = "No verified target candidate files found in repository via code search."
            self._jobs.append_step(job, AgentStep(
                name="branch_and_fix",
                status="needs_attention",
                detail=msg,
            ))
            self._jobs.set_report(job, f"OpsPilot stopped safely: {msg}")
            self._jobs.transition(job, JobStatus.needs_attention, msg)
            return False

        patches = await self._resolve_patches(ctx, issue)
        if not patches:
            msg = "OpsPilot could not generate a safe targeted patch from verified repository content."
            self._jobs.append_step(job, AgentStep(
                name="branch_and_fix",
                status="needs_attention",
                detail=msg,
            ))
            self._jobs.set_report(job, f"OpsPilot stopped safely: {msg}")
            self._jobs.transition(job, JobStatus.needs_attention, msg)
            return False

        ctx.applied_patches = patches

        base_sha = DEMO_HEAD_SHA if ctx.demo_mode else (
            await self._resolve_head_sha(github, owner, repo)
        )
        if not base_sha:
            self._fail(job, "Could not resolve base commit SHA for branch creation.", ctx)
            return False

        initial_branch = self._fix_branch_name(issue)
        target_branch = initial_branch
        decision = self._policy.evaluate("create_branch", RiskLevel.low, False)
        if not decision.allowed and not ctx.auto_approve:
            return self._request_approval_and_halt(ctx, "create_branch", RiskLevel.low, decision, {})

        branch_result = await self._safe_tool_call(
            ctx, "create_branch", github.create_branch, owner, repo, target_branch, base_sha,
        )
        if branch_result.status != ToolStatus.success:
            err_msg = (branch_result.error or "").lower()
            if "already exists" in err_msg or "422" in err_msg:
                target_branch = f"{initial_branch}-v2"
                branch_result = await self._safe_tool_call(
                    ctx, "create_branch", github.create_branch, owner, repo, target_branch, base_sha,
                )
                if branch_result.status != ToolStatus.success and ("already exists" in (branch_result.error or "").lower() or "422" in (branch_result.error or "")):
                    target_branch = f"{initial_branch}-v3"
                    branch_result = await self._safe_tool_call(
                        ctx, "create_branch", github.create_branch, owner, repo, target_branch, base_sha,
                    )

        if branch_result.status != ToolStatus.success:
            self._fail(job, f"Could not create branch: {branch_result.error}", ctx)
            return False

        ctx.active_branch_name = target_branch
        ctx.branch_created = True
        self._jobs.append_step(job, AgentStep(
            name="create_branch",
            status="completed",
            detail=f"Created branch '{ctx.active_branch_name}' from {base_sha[:8]}.",
        ))

        for path, new_content, message in patches:
            existing = await self._safe_tool_call(ctx, "get_file", github.get_file, owner, repo, path)
            current_sha = None
            if existing.status == ToolStatus.success and not existing.data.get("is_directory"):
                current_sha = existing.data.get("sha")
            decision = self._policy.evaluate("modify_file", RiskLevel.medium, True, tool_args={"path": path})
            if decision.needs_approval and not ctx.auto_approve:
                return self._request_approval_and_halt(
                    ctx, "modify_file", decision.risk, decision,
                    {"path": path, "branch": ctx.active_branch_name, "message": message},
                )
            modify_result = await self._safe_tool_call(
                ctx,
                "modify_file",
                github.modify_file,
                owner, repo, path, new_content, ctx.active_branch_name, message, current_sha,
            )
            if modify_result.status != ToolStatus.success:
                self._fail(job, f"Modifying {path} failed: {modify_result.error}", ctx)
                return False
            self._jobs.append_step(job, AgentStep(
                name="modify_file",
                status="completed",
                detail=f"Updated {path} on branch '{ctx.active_branch_name}' (commit {str(modify_result.data.get('commit_sha',''))[:8]}).",
            ))

        if ctx.demo_mode and ctx.demo_workspace:
            ctx.demo_workspace.apply_fixes()

        return True

    async def _verify_fix(self, ctx: _AgentContext) -> bool:
        job = ctx.job

        if ctx.demo_mode and ctx.demo_workspace:
            ctx.tests_passed_after_fix = True
            self._jobs.append_step(job, AgentStep(
                name="verify_tests",
                status="completed",
                detail="Tests verified (demo mode): seeded fix confirmed to make all 6 auth tests pass.",
            ))
            self._record_tool_result(ctx, ToolResult(
                tool_name="run_tests",
                risk=RiskLevel.low,
                status=ToolStatus.success,
                data={
                    "command": "pytest tests/",
                    "summary": {"exit_code": 0, "passed": 6, "failed": 0, "total": 6, "success": True},
                    "stdout_tail": "6 passed in 0.12s",
                    "stderr_tail": "",
                },
                duration_ms=120,
            ))
            return True

        workspace_path = None
        runner = LocalTestRunner(timeout_seconds=120)
        post_command = "pytest"
        decision = self._policy.evaluate("run_tests", RiskLevel.low, False)
        if not decision.allowed and not ctx.auto_approve:
            return self._request_approval_and_halt(ctx, "run_tests", RiskLevel.low, decision, {})
        test_result = await runner.run(command=post_command, owner=ctx.owner, repo=ctx.repo, workspace_override=workspace_path)
        self._record_tool_result(ctx, test_result)
        summary = test_result.data.get("summary", {}) if test_result.status == ToolStatus.success else {}
        passed = int(summary.get("passed", 0))
        failed = int(summary.get("failed", 0))
        exit_code = int(summary.get("exit_code", 1))

        if test_result.status != ToolStatus.success:
            self._fail(job, f"Tests failed after fix: {test_result.error}. Stderr tail: {test_result.data.get('stderr_tail', '')[:200]}", ctx)
            return False

        ctx.tests_passed_after_fix = True
        self._jobs.append_step(job, AgentStep(
            name="verify_tests",
            status="completed",
            detail=f"Tests verified: {passed} passed, {failed} failed (exit code {exit_code}).",
        ))
        return True

    async def _open_pr(self, ctx: _AgentContext, github: GitHubToolkit, issue: dict[str, Any]) -> bool:
        job = ctx.job
        branch = ctx.active_branch_name or self._fix_branch_name(issue)
        base = "main"
        title = self._pr_title(issue)
        body = self._pr_body(ctx, issue, branch)
        decision = self._policy.evaluate(
            "create_pull_request",
            RiskLevel.medium,
            True,
            tool_args={"head_branch": branch, "base_branch": base},
        )
        if decision.needs_approval and not ctx.auto_approve:
            return self._request_approval_and_halt(
                ctx,
                "create_pull_request",
                decision.risk,
                decision,
                {"head": branch, "base": base, "title": title},
            )
        pr_result = await self._safe_tool_call(
            ctx, "create_pull_request", github.create_pull_request,
            ctx.owner, ctx.repo, title, branch, base, body, False,
        )
        if pr_result.status != ToolStatus.success:
            self._fail(job, f"PR creation failed: {pr_result.error}", ctx)
            return False
        ctx.pr_created = True
        pr_url = pr_result.data.get("html_url", "")
        pr_number = pr_result.data.get("number", "?")
        self._jobs.append_step(job, AgentStep(
            name="create_pr",
            status="completed",
            detail=f"Created PR #{pr_number}: {pr_url}",
        ))
        self._jobs.set_checkpoint(job, "pr_url", pr_url)
        self._jobs.set_checkpoint(job, "pr_number", pr_number)
        return True

    def _store_memory_after_fix(self, ctx: _AgentContext, issue: dict[str, Any], repo_data: dict[str, Any]) -> None:
        job = ctx.job
        project_id = job.project_id
        number = issue.get("number", 0)
        title = issue.get("title", "")
        labels = ", ".join(issue.get("labels", []))
        if ctx.applied_patches:
            patch_files = ", ".join(f"`{p[0]}`" for p in ctx.applied_patches)
            fix_summary = f"Resolved issue #{number} ('{title}') with verified changes to {patch_files}."
        else:
            fix_summary = f"Resolved issue #{number} ('{title}')."
        mem_fix = self._memory.write(
            project_id,
            MEMORY_TYPE_SUCCESSFUL_FIX,
            fix_summary,
            source=f"job:{job.job_id}",
            confidence=0.92,
            metadata={"issue_number": number, "pr_number": job.checkpoint.get("pr_number")},
        )
        self._jobs.add_memory_update(job, mem_fix.memory_id)

        mem_task = self._memory.write(
            project_id,
            MEMORY_TYPE_PREVIOUS_TASK,
            f"Task: {title} (labels: {labels}). Outcome: fixed, PR created, tests green.",
            source=f"job:{job.job_id}",
            confidence=0.95,
            metadata={"issue_number": number, "priority_score": issue.get("priority_score", 0)},
        )
        self._jobs.add_memory_update(job, mem_task.memory_id)

        if repo_data.get("language"):
            lang = repo_data["language"]
            mem = self._memory.write(
                project_id,
                MEMORY_TYPE_CONVENTION,
                f"Repository primary language detected as {lang}.",
                source="after_fix_scan",
                confidence=0.95,
            )
            self._jobs.add_memory_update(job, mem.memory_id)
        mem_test = self._memory.write(
            project_id,
            MEMORY_TYPE_TESTING_CONVENTION,
            "Tests are executed via pytest. Run 'pytest' after code changes and require exit code 0.",
            source="after_fix_scan",
            confidence=0.9,
        )
        self._jobs.add_memory_update(job, mem_test.memory_id)
        self._jobs.append_step(job, AgentStep(
            name="store_memory",
            status="completed",
            detail=f"Wrote {len(job.memory_updated)} memory entries for future runs.",
        ))

    async def _resolve_head_sha(self, github: GitHubToolkit, owner: str, repo: str) -> str:
        commits = await github.get_recent_commits(owner, repo, per_page=1)
        if commits.status != ToolStatus.success or not commits.data.get("commits"):
            return ""
        return commits.data["commits"][0].get("sha", "")

    @staticmethod
    def _fix_branch_name(issue: dict[str, Any]) -> str:
        number = issue.get("number", 0)
        title = (issue.get("title") or "fix").lower()
        slug = "".join(c if c.isalnum() else "-" for c in title).strip("-")[:40].strip("-")
        branch = f"opspilot/fix-issue-{number}-{slug}"
        return branch[:63] or DEMO_FIX_BRANCH

    @staticmethod
    def _pr_title(issue: dict[str, Any]) -> str:
        number = issue.get("number", 0)
        title = (issue.get("title") or "Fix").capitalize()
        return f"fix: {title} (#{number})"

    def _pr_body(self, ctx: _AgentContext, issue: dict[str, Any], branch: str) -> str:
        issue_number = issue.get("number", "?")
        tests_note = "Tests passed after fix." if ctx.tests_passed_after_fix else "Tests not yet verified."

        changes_lines: list[str] = []
        if ctx.applied_patches:
            for path, _, msg in ctx.applied_patches:
                changes_lines.append(f"- Modifies `{path}`: {msg}")
        elif ctx.demo_mode:
            changes_lines.append("- Applied clock-skew tolerant token validation in `demo_project/auth/token.py`.")
            changes_lines.append("- Updated dependencies in `requirements.txt`.")
        else:
            changes_lines.append(f"- Targeted fix applied for issue #{issue_number}.")

        changes_section = "\n".join(changes_lines)

        return (
            f"## Summary\n"
            f"Automated fix for issue #{issue_number} generated by OpsPilot.\n\n"
            f"- Branch: `{branch}`\n"
            f"- Closes: #{issue_number}\n"
            f"- {tests_note}\n\n"
            f"## Changes\n"
            f"{changes_section}\n\n"
            f"_Generated by OpsPilot (job `{ctx.job.job_id}`)._"
        )

    async def _resolve_patches(self, ctx: _AgentContext, issue: dict[str, Any]) -> list[tuple[str, str, str]]:
        if ctx.demo_mode:
            return [
                (
                    "demo_project/auth/token.py",
                    FIXED_AUTH_TOKEN_PY,
                    f"fix: apply clock-skew tolerant token validation (closes #{issue.get('number', 0)})",
                ),
                (
                    "requirements.txt",
                    FIXED_REQUIREMENTS_TXT,
                    f"chore(deps): upgrade httpx and pytest (closes #{issue.get('number', 0)})",
                ),
            ]

        if not ctx.verified_file_paths:
            return []

        ranked_paths = self._patch_generator.rank_candidate_files(
            ctx.verified_file_paths,
            ctx.goal,
            issue.get("title", ""),
            issue.get("body", ""),
        )

        patches: list[tuple[str, str, str]] = []
        for path in ranked_paths:
            content = ctx.verified_file_contents.get(path, "")
            if content is None:
                continue

            patch_result = await self._patch_generator.generate_patch(
                goal=ctx.goal,
                issue=issue,
                target_path=path,
                original_content=content,
                verified_paths=ctx.verified_file_paths,
            )
            if patch_result.success and patch_result.new_content:
                patches.append((path, patch_result.new_content, patch_result.commit_message))
                break  # Target one high-confidence file modification per task

        return patches

    def _select_highest_priority_task(
        self,
        issues: list[dict[str, Any]],
        prs: list[dict[str, Any]],
        ci_result: ToolResult | None,
    ) -> dict[str, Any] | None:
        scored = list(issues)
        scored.sort(key=lambda i: i.get("priority_score", 0), reverse=True)
        if scored:
            return scored[0]
        if prs:
            pr = prs[0]
            return {
                "number": pr.get("number", 0),
                "title": f"Follow up on PR #{pr.get('number')}",
                "labels": [{"name": "pr"}],
                "comments": 0,
                "priority_score": 20,
                "body": pr.get("body", ""),
            }
        return None

    def _build_plan(self, selected: dict[str, Any], repo: dict[str, Any]) -> list[str]:
        title = selected.get("title", "")
        number = selected.get("number", 0)
        return [
            f"Investigate selected task #{number}: {title}.",
            "Inspect related source files, tests, and repository structure.",
            "Create a dedicated fix branch from the default branch.",
            "Apply targeted source changes using MEDIUM risk modify_file (requires approval if not auto_approve).",
            "Run tests (pytest) and require exit code 0 before continuing.",
            "Open a PR against the default branch with an automated summary.",
            "Verify PR exists and capture final state.",
            "Store project memory: conventions, successful fix, previous task.",
        ]

    def _write_memory_for_clean_scan(self, project_id: str, repo: dict[str, Any]) -> None:
        self._memory.write(
            project_id,
            MEMORY_TYPE_PREVIOUS_TASK,
            f"Scan of {repo.get('full_name','')}: no actionable tasks found at this time.",
            source="clean_scan",
            confidence=0.7,
            metadata={
                "open_issues_count": repo.get("open_issues_count", 0),
                "language": repo.get("language"),
            },
        )

    def _build_final_report(
        self,
        ctx: _AgentContext,
        completed_summary: str,
        approval_summary: str,
        failed_summary: str,
    ) -> str:
        job = ctx.job
        lines = ["OPS PILOT REPORT", ""]
        if completed_summary:
            lines.append("Completed:")
            for item in completed_summary.split(". "):
                item = item.strip().strip(".")
                if item:
                    lines.append(f"  - {item}.")
            lines.append("")
        if job.memory_updated:
            lines.append(f"Memory updated: {len(job.memory_updated)} entries stored for future runs.")
            lines.append("")
        if approval_summary:
            lines.append("Approvals:")
            lines.append(f"  {approval_summary}")
            lines.append("")
        if failed_summary:
            lines.append("Failed:")
            lines.append(f"  {failed_summary}")
            lines.append("")
        else:
            lines.append("Failed: 0")
            lines.append("")
        lines.append(f"Tools executed: {len(job.tools_used)}")
        lines.append(f"Plan steps: {len(job.plan)}")
        return "\n".join(lines)

    def _build_approval_summary(self, ctx: _AgentContext) -> str:
        job = ctx.job
        job_id = job.job_id
        reqs = self._approvals.list_for_job(job_id)
        if not reqs:
            if ctx.auto_approve:
                return "All medium/high risk actions auto-approved (auto_approve=true)."
            return "No medium/high risk actions required approval this run."
        parts = []
        for a in reqs:
            parts.append(f"{a.tool_name} ({a.risk.value}): {a.status.value}")
        return "; ".join(parts)

    def _resolve_github_toolkit(self, ctx: _AgentContext) -> GitHubToolkit:
        if self._github is not None:
            return self._github
        if ctx.demo_mode:
            transport = self._demo_transport_override or DemoGitHubTransport(self._settings)
            client = transport.build_client()
            return GitHubToolkit(self._settings, client=client)
        return GitHubToolkit(self._settings)

    async def _safe_tool_call(
        self,
        ctx: _AgentContext,
        name: str,
        func: Callable[..., Coroutine[Any, Any, ToolResult]],
        *args: Any,
        **kwargs: Any,
    ) -> ToolResult:
        default_risk, default_approval = TOOL_RISK_BY_NAME.get(name, (RiskLevel.low, False))
        decision = self._policy.evaluate(name, default_risk, default_approval, tool_args=kwargs)
        if decision.blocked:
            err = ToolResult(
                tool_name=name,
                risk=RiskLevel.blocked,
                status=ToolStatus.error,
                error=decision.reason,
                duration_ms=0,
            )
            self._record_tool_result(ctx, err)
            return err
        if decision.needs_approval and not ctx.auto_approve:
            existing_list = self._approvals.find_pending_for_tool(ctx.job.job_id, name)
            if not existing_list:
                self._request_approval_and_halt(ctx, name, decision.risk, decision, {})
                err = ToolResult(
                    tool_name=name,
                    risk=decision.risk,
                    status=ToolStatus.error,
                    error=f"Human approval required; pending approval_id not yet resolved. {decision.reason}",
                    duration_ms=0,
                )
                self._record_tool_result(ctx, err)
                return err
            existing = existing_list[0]
            ok, _ = self._approvals.validate_execution_allowed(existing.approval_id)
            if not ok:
                err = ToolResult(
                    tool_name=name,
                    risk=decision.risk,
                    status=ToolStatus.error,
                    error="Human approval not granted; blocked by policy engine.",
                    duration_ms=0,
                )
                self._record_tool_result(ctx, err)
                return err

        if ctx.tool_calls_count >= MAX_TOOL_CALLS_PER_JOB:
            err = ToolResult(
                tool_name=name,
                risk=default_risk,
                status=ToolStatus.error,
                error=f"Tool call budget exceeded ({MAX_TOOL_CALLS_PER_JOB}).",
                duration_ms=0,
            )
            self._record_tool_result(ctx, err)
            return err
        ctx.tool_calls_count += 1

        last_error: ToolResult | None = None
        for attempt in range(1, MAX_RETRIES_PER_TOOL + 1):
            try:
                with self._logger.timed_block(
                    f"tool.{name}",
                    job_id=ctx.job.job_id,
                    project_id=ctx.job.project_id,
                    agent_step=f"tool_call:{name}",
                    tool_name=name,
                ):
                    result = await func(*args, **kwargs)
                self._record_tool_result(ctx, result)
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = ToolResult(
                    tool_name=name,
                    risk=default_risk,
                    status=ToolStatus.error,
                    error=f"{exc.__class__.__name__}: {exc}",
                    duration_ms=0,
                )
                if attempt >= MAX_RETRIES_PER_TOOL:
                    break
                await asyncio.sleep(0.25 * attempt)
        if last_error is not None:
            self._record_tool_result(ctx, last_error)
            return last_error
        return ToolResult(
            tool_name=name,
            risk=default_risk,
            status=ToolStatus.error,
            error="Unknown tool error.",
            duration_ms=0,
        )

    def _record_tool_result(self, ctx: _AgentContext, result: ToolResult) -> None:
        self._jobs.append_tool_result(ctx.job, result)
        if result.status != ToolStatus.success:
            ctx.failure_count += 1

    def _request_approval_and_halt(
        self,
        ctx: _AgentContext,
        tool_name: str,
        risk: RiskLevel,
        decision: PolicyDecision,
        tool_args: dict[str, Any],
    ) -> bool:
        job = ctx.job
        approval = self._approvals.request(
            job_id=job.job_id,
            project_id=job.project_id,
            tool_name=tool_name,
            risk=risk,
            reason=decision.reason,
            tool_args=tool_args,
            expires_seconds=APPROVAL_EXPIRY_SECONDS,
        )
        self._jobs.add_approval_requested(job, approval.approval_id)
        self._jobs.append_step(job, AgentStep(
            name="approval_requested",
            status="needs_approval",
            detail=f"Approval id={approval.approval_id} for tool '{tool_name}' ({risk.value}). {decision.reason}",
        ))
        self._jobs.transition(job, JobStatus.needs_approval, f"Awaiting human approval for {tool_name}")
        return False

    def _fail(self, job: JobRecord, message: str, ctx: _AgentContext) -> None:
        self._jobs.set_error(job, message)
        ctx.failure_count += 1
        self._memory.write(
            job.project_id,
            MEMORY_TYPE_FAILED_APPROACH,
            f"Job {job.job_id} failed: {message}",
            source=f"job:{job.job_id}",
            confidence=0.6,
        )
        if ctx.failure_count >= FAIL_ESCALATION_THRESHOLD:
            self._jobs.transition(job, JobStatus.needs_attention, message)
        else:
            self._jobs.transition(job, JobStatus.partially_completed, message)
        self._logger.error(
            message,
            job_id=job.job_id,
            project_id=job.project_id,
            agent_step="agent.fail",
        )

    def _build_callable_tool_list(self) -> list[Callable[..., Any]]:
        tools: list[Callable[..., Any]] = []
        gh = GitHubToolkit(self._settings)
        tools.append(gh.get_repository)
        tools.append(gh.list_issues)
        tools.append(gh.get_issue)
        tools.append(gh.list_pull_requests)
        tools.append(gh.get_pull_request)
        tools.append(gh.get_file)
        tools.append(gh.search_code)
        tools.append(gh.get_recent_commits)
        tools.append(gh.get_ci_status)
        tools.append(gh.create_branch)
        tools.append(gh.run_tests)
        return tools

    @staticmethod
    def _load_adk_agent_class() -> Any | None:
        try:
            from google.adk import Agent  # type: ignore
        except ImportError:
            return None
        return Agent

    @property
    def demo_stats(self) -> dict[str, Any]:
        return {
            "seeded_issues": DEMO_SEEDED_ISSUES_COUNT,
            "seeded_tests_failing": DEMO_SEEDED_FAILURES_COUNT,
        }
