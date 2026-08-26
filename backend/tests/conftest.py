"""
pytest configuration for the backend test suite.

Resets module-level singletons before each test so that state created in one
test (jobs, approvals, memory entries) does not bleed into the next.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all module-level singletons before every test."""
    import backend.app.api.routes as routes_mod
    import backend.app.memory.memory_service as mem_mod

    # Reset orchestrator + policy singletons
    routes_mod._orchestrator_singleton = None
    routes_mod._policy_singleton = None
    routes_mod._settings_singleton = None

    # Reset the global InMemoryStore so jobs/approvals/memory don't leak
    mem_mod._STORE_SINGLETON = None

    yield

    # Teardown: reset again after the test to leave a clean state
    routes_mod._orchestrator_singleton = None
    routes_mod._policy_singleton = None
    routes_mod._settings_singleton = None
    mem_mod._STORE_SINGLETON = None
