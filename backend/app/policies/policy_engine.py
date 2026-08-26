from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.models import RiskLevel, ToolResult, ToolStatus


BLOCKED_TOOL_NAMES = {
    "delete_production_database",
    "delete_cloud_infrastructure",
    "expose_secrets",
    "print_secret_values",
    "disable_security_controls",
    "drop_production_table",
}

APPROVAL_EXPIRY_SECONDS = 60 * 60 * 4


@dataclass
class PolicyDecision:
    allowed: bool
    risk: RiskLevel
    reason: str
    needs_approval: bool
    blocked: bool = False


class PolicyEngine:
    """Centralised policy enforcement for every tool invocation."""

    def __init__(self) -> None:
        self._tool_risk_override: dict[str, RiskLevel] = {}
        self._tool_approval_override: dict[str, bool] = {}

    def set_tool_risk(self, tool_name: str, risk: RiskLevel) -> None:
        self._tool_risk_override[tool_name] = risk

    def set_needs_approval(self, tool_name: str, value: bool) -> None:
        self._tool_approval_override[tool_name] = value

    def evaluate(
        self,
        tool_name: str,
        default_risk: RiskLevel,
        default_needs_approval: bool,
        tool_args: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        if tool_name in BLOCKED_TOOL_NAMES:
            return PolicyDecision(
                allowed=False,
                risk=RiskLevel.blocked,
                reason=f"Tool '{tool_name}' is blocked by security policy.",
                needs_approval=False,
                blocked=True,
            )

        risk = self._tool_risk_override.get(tool_name, default_risk)
        needs_approval = self._tool_approval_override.get(tool_name, default_needs_approval)

        extra_reason = ""
        tool_args = tool_args or {}

        if risk == RiskLevel.medium and tool_name == "modify_file":
            path = str(tool_args.get("path", ""))
            if any(seg in path.lower() for seg in {".env", "prod", "production", "secret", "config/prod"}):
                risk = RiskLevel.high
                needs_approval = True
                extra_reason = " (path touches production-sensitive file; elevated to HIGH risk)"

        if risk == RiskLevel.medium and tool_name == "create_pull_request":
            base = str(tool_args.get("base_branch") or tool_args.get("base") or "")
            if base.lower() in {"main", "master", "production", "prod", "release"}:
                needs_approval = True
                extra_reason = f" (PR targets protected base branch '{base}')"

        if risk == RiskLevel.blocked:
            return PolicyDecision(
                allowed=False,
                risk=risk,
                reason=f"Tool '{tool_name}' is blocked." + extra_reason,
                needs_approval=False,
                blocked=True,
            )

        if risk == RiskLevel.high and not needs_approval:
            needs_approval = True
            extra_reason += " (HIGH risk always requires human approval)"

        reason = f"Risk {risk.value} for tool '{tool_name}'." + extra_reason
        allowed = not needs_approval
        return PolicyDecision(
            allowed=allowed,
            risk=risk,
            reason=reason,
            needs_approval=needs_approval,
            blocked=False,
        )

    def classify_tool_from_result(self, result: ToolResult) -> PolicyDecision:
        return self.evaluate(
            tool_name=result.tool_name,
            default_risk=result.risk,
            default_needs_approval=result.risk in {RiskLevel.medium, RiskLevel.high},
        )

    def wrap_result_with_policy(
        self,
        result: ToolResult,
        approval_granted: bool = False,
    ) -> ToolResult:
        decision = self.classify_tool_from_result(result)
        if decision.blocked:
            return ToolResult(
                tool_name=result.tool_name,
                risk=RiskLevel.blocked,
                status=ToolStatus.error,
                data=result.data,
                error=decision.reason,
                duration_ms=result.duration_ms,
            )
        if decision.needs_approval and not approval_granted:
            return ToolResult(
                tool_name=result.tool_name,
                risk=decision.risk,
                status=ToolStatus.error,
                data=result.data,
                error=f"Human approval required. {decision.reason}",
                duration_ms=result.duration_ms,
            )
        return result
