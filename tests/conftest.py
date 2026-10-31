from __future__ import annotations

import os

import pytest

# =============================================================================
# Module Overview
# =============================================================================
# Shared test setup. Every test starts with the optional model harness off: a
# developer's shell that sets `CHAMELEON_AGENT*` must not change any result,
# and the harness `resize()` caches is rebuilt for each test that sets one.


@pytest.fixture(autouse=True)
def _agent_off(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CHAMELEON_AGENT"):
            monkeypatch.delenv(name, raising=False)
    import engine.resize

    engine.resize._agent.cache_clear()
    yield
    clear = getattr(engine.resize._agent, "cache_clear", None)  # a test may have replaced `_agent`
    if clear is not None:
        clear()
