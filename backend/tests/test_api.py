import time

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "OpsPilot API"


def test_tools_endpoint_returns_tool_catalog() -> None:
    response = client.get("/api/tools")
    assert response.status_code == 200
    payload = response.json()
    tools = payload["tools"]
    names = [t["name"] for t in tools]
    assert "get_repository" in names
    assert "list_issues" in names
    assert "modify_file" in names
    assert "create_pull_request" in names
    assert len(tools) >= 12


def test_approvals_empty_initially() -> None:
    response = client.get("/api/approvals")
    assert response.status_code == 200
    assert response.json()["approvals"] == []


def test_create_job_returns_queued_status_and_id() -> None:
    payload = {
        "goal": "Fix the top-priority bugs please.",
        "project_id": "test-project",
        "github_owner": "opspilot",
        "github_repo": "demo-repo",
        "demo_mode": True,
        "auto_approve": True,
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"].lower() in ("queued", "running")
    job_id = data["job_id"]

    # Poll briefly until terminal or timeout
    terminal = {"completed", "partially_completed", "failed", "cancelled", "needs_attention", "needs_approval"}
    status = data["status"].lower()
    deadline = time.time() + 120
    while status not in terminal and time.time() < deadline:
        time.sleep(1.0)
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        status = resp.json()["status"].lower()

    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"].lower() == "completed", (
        f"Expected completed; got status={final['status']}.\nReport:\n{final.get('report')}\nError:\n{final.get('error')}"
    )
    checkpoints = final.get("checkpoints") or {}
    assert "pr_number" in checkpoints
    assert isinstance(checkpoints["pr_number"], int)
    assert checkpoints["pr_number"] > 0
    # Memory list endpoint
    mem = client.get(f"/api/memory/{final['project_id']}")
    assert mem.status_code == 200
    mem_entries = mem.json().get("entries") or []
    assert len(mem_entries) >= 4


def test_demo_start_endpoint_returns_seeded_counts() -> None:
    response = client.post("/api/demo/start")
    assert response.status_code == 202
    payload = response.json()
    assert payload["seeded_issues"] == 5
    assert payload["seeded_tests_failing"] == 3
    assert "job_id" in payload


def test_job_events_endpoint_returns_list() -> None:
    payload = {
        "goal": "events test",
        "project_id": "events-project",
        "github_owner": "opspilot",
        "github_repo": "demo-repo",
        "demo_mode": True,
        "auto_approve": True,
    }
    resp = client.post("/api/jobs", json=payload)
    job_id = resp.json()["job_id"]
    # wait a moment for at least one event
    time.sleep(1.0)
    events = client.get(f"/api/jobs/{job_id}/events")
    assert events.status_code == 200
    data = events.json()
    assert "events" in data
