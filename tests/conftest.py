import os

import pytest


@pytest.fixture(autouse=True)
def _no_agentic_debug_log(monkeypatch):
    """Keep unit-test runs from appending mock records to the real
    AGENTIC_DEBUG_LOG file (app.main's load_dotenv picks it up from .env)."""
    monkeypatch.delenv("AGENTIC_DEBUG_LOG", raising=False)


# The tests in this repo are also runnable as plain scripts (python
# tests/test_x.py), which bypasses fixtures — clear the var at import too.
os.environ.pop("AGENTIC_DEBUG_LOG", None)
