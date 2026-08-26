from backend.app.models import RiskLevel, ToolResult, ToolStatus
from backend.app.policies import PolicyEngine, PolicyDecision


def test_policy_engine_blocked_tools_always_blocked() -> None:
    engine = PolicyEngine()
    for tool in ("delete_production_database", "expose_secrets", "disable_security_controls"):
        decision = engine.evaluate(tool, RiskLevel.low, False)
        assert isinstance(decision, PolicyDecision)
        assert decision.allowed is False
        assert decision.blocked is True
        assert decision.needs_approval is False


def test_policy_engine_high_risk_requires_approval() -> None:
    engine = PolicyEngine()
    decision = engine.evaluate(
        "deploy_production", RiskLevel.high, False
    )
    assert decision.blocked is False
    assert decision.needs_approval is True
    assert decision.risk == RiskLevel.high


def test_policy_engine_elevates_modify_file_on_prod_paths() -> None:
    engine = PolicyEngine()
    decision = engine.evaluate(
        "modify_file",
        RiskLevel.medium,
        False,
        tool_args={"path": "config/.env.production"},
    )
    assert decision.risk == RiskLevel.high
    assert decision.needs_approval is True
    assert "production" in decision.reason.lower()


def test_policy_engine_pr_targeting_main_needs_approval() -> None:
    engine = PolicyEngine()
    decision = engine.evaluate(
        "create_pull_request",
        RiskLevel.medium,
        False,
        tool_args={"base_branch": "main"},
    )
    assert decision.needs_approval is True
    assert "main" in decision.reason.lower()


def test_policy_engine_wrap_result_labels_blocked() -> None:
    engine = PolicyEngine()
    blocked_result = ToolResult(
        tool_name="delete_production_database",
        risk=RiskLevel.high,
        status=ToolStatus.success,
        data={},
        duration_ms=0,
    )
    wrapped = engine.wrap_result_with_policy(blocked_result, approval_granted=False)
    assert wrapped.status.value == "error"
    assert "blocked" in (wrapped.error or "").lower()
