from backend.app.config.settings import Settings
from backend.app.memory import MemoryEntry, MemoryService, VALID_MEMORY_TYPES


def test_memory_write_and_retrieve_by_type() -> None:
    service = MemoryService(Settings())
    pid = "proj-1"
    e1 = service.write(
        project_id=pid,
        memory_type="project_convention",
        content="Repo uses pytest for backend tests.",
        source="successful_task",
        confidence=0.94,
    )
    e2 = service.write(
        project_id=pid,
        memory_type="successful_fix",
        content="Applied leeway seconds to iat validation.",
        source="task",
        confidence=0.88,
    )
    assert isinstance(e1, MemoryEntry)
    assert isinstance(e2, MemoryEntry)

    convs = service.retrieve(pid, memory_types=["project_convention"])
    assert len(convs) == 1
    assert convs[0].memory_id == e1.memory_id

    both = service.retrieve(pid)
    assert len(both) == 2


def test_memory_retrieve_with_confidence_and_query() -> None:
    service = MemoryService(Settings())
    pid = "proj-2"
    service.write(pid, "project_convention", "Uses pnpm for frontend.", "task", 0.9)
    service.write(pid, "project_convention", "Pre-commit uses black.", "task", 0.5)
    service.write(pid, "testing_convention", "Run pytest -q.", "task", 0.92)

    highs = service.retrieve(pid, min_confidence=0.85)
    assert len(highs) == 2
    pnpm = service.retrieve(pid, query="pnpm")
    assert len(pnpm) == 1
    assert pnpm[0].content == "Uses pnpm for frontend."


def test_memory_valid_types() -> None:
    for t in (
        "project_convention",
        "successful_fix",
        "failed_approach",
        "previous_task",
        "approval_decision",
        "repository_structure",
        "testing_convention",
        "deployment_convention",
    ):
        assert t in VALID_MEMORY_TYPES


def test_memory_context_snapshot_and_build_reasoning_context() -> None:
    service = MemoryService(Settings())
    pid = "proj-3"
    service.write_context_snapshot(
        pid,
        repo_language="Python",
        testing_framework_hint="pytest",
        default_branch="main",
    )
    ctx = service.build_reasoning_context(pid, "setup python")
    assert "Python" in ctx
    assert "pytest" in ctx
    assert "main" in ctx


def test_memory_reap_older_than() -> None:
    service = MemoryService(Settings())
    pid = "proj-4"
    old = service.write(pid, "previous_task", "Old task", "task", 0.7)
    import time

    time.sleep(0.02)
    new = service.write(pid, "previous_task", "New task", "task", 0.7)
    removed = service.reap_older_than(pid, older_than_seconds=0.01)
    ids_removed = {r.memory_id for r in removed}
    assert old.memory_id in ids_removed
    assert new.memory_id not in ids_removed
