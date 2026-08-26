import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.app.models import RiskLevel, ToolResult, ToolStatus


class LocalTestRunner:
    """Sandboxed local test runner with timeout and working-directory restriction."""

    def __init__(self, timeout_seconds: int = 120) -> None:
        self._timeout = timeout_seconds
        self._allowed_commands = {
            "pytest",
            "python",
            "python3",
            "npm",
            "npx",
            "yarn",
            "go",
            "cargo",
            "dotnet",
        }

    async def run(
        self,
        command: str,
        owner: str,
        repo: str,
        workspace_override: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        risk = RiskLevel.low
        tool_name = "run_tests"

        tokens = command.strip().split()
        if not tokens:
            return ToolResult(
                tool_name=tool_name,
                risk=risk,
                status=ToolStatus.error,
                error="Empty command.",
                duration_ms=self._elapsed(started),
            )
        base_cmd = tokens[0].lower().replace(".exe", "")
        if base_cmd not in self._allowed_commands:
            return ToolResult(
                tool_name=tool_name,
                risk=risk,
                status=ToolStatus.error,
                error=f"Command '{tokens[0]}' is not in the allowed test command allowlist.",
                duration_ms=self._elapsed(started),
            )

        workspace = workspace_override
        if not workspace:
            workspace = tempfile.mkdtemp(prefix=f"opspilot-test-{owner}-{repo}-")
        workspace_path = Path(workspace).resolve()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace_path),
                env=self._safe_env(),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return ToolResult(
                    tool_name=tool_name,
                    risk=risk,
                    status=ToolStatus.error,
                    error=f"Test command timed out after {self._timeout} seconds.",
                    duration_ms=self._elapsed(started),
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            passed, failed, total = self._parse_summary(command, stdout, stderr, exit_code)
            summary = {
                "exit_code": exit_code,
                "passed": passed,
                "failed": failed,
                "total": total,
                "success": exit_code == 0,
            }
            tail_stdout = "\n".join(stdout.splitlines()[-80:])
            tail_stderr = "\n".join(stderr.splitlines()[-80:])

            status = ToolStatus.success if exit_code == 0 else ToolStatus.error
            error = None if exit_code == 0 else f"Tests failed with exit code {exit_code}."
            data: dict[str, Any] = {
                "command": command,
                "workspace": str(workspace_path),
                "summary": summary,
                "stdout_tail": tail_stdout,
                "stderr_tail": tail_stderr,
            }
            return ToolResult(
                tool_name=tool_name,
                risk=risk,
                status=status,
                data=data,
                error=error,
                duration_ms=self._elapsed(started),
            )
        finally:
            if not workspace_override:
                try:
                    shutil.rmtree(workspace, ignore_errors=True)
                except Exception:
                    pass

    @staticmethod
    def _safe_env() -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env.keys()):
            if "TOKEN" in key or "SECRET" in key or "KEY" in key or "PASSWORD" in key:
                del env[key]
        return env

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _parse_summary(
        command: str,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> tuple[int, int, int]:
        combined = stdout + "\n" + stderr
        passed = 0
        failed = 0
        total = 0

        if "pytest" in command.lower():
            import re as _re
            match = _re.search(r"(\d+) passed", combined)
            if match:
                passed = int(match.group(1))
            match = _re.search(r"(\d+) failed", combined)
            if match:
                failed = int(match.group(1))
            match = _re.search(r"(\d+) (?:error|errors)", combined)
            if match:
                failed += int(match.group(1))
            total = passed + failed
        elif "npm" in command.lower() or "yarn" in command.lower() or "npx" in command.lower():
            import re as _re
            match = _re.search(r"Tests:\s+(\d+) failed", combined)
            if match:
                failed = int(match.group(1))
            match = _re.search(r"Tests:\s+(\d+) passed", combined)
            if match:
                passed = int(match.group(1))
            match = _re.search(r"Tests:\s+(\d+) total", combined)
            if match:
                total = int(match.group(1))
            if total == 0:
                total = passed + failed

        if total == 0:
            total = 0 if exit_code == 0 else 1
            if exit_code == 0:
                passed = total
            else:
                failed = total

        return passed, failed, total
