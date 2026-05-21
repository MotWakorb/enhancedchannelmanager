"""Shared pytest fixtures for the mcp-server suite.

The Starlette ``lifespan`` starts the StreamableHTTP session manager
(``mcp.session_manager.run()``), and that manager can be run only ONCE per
process instance — a second ``run()`` raises ``RuntimeError`` (see the comment
in ``server.py``). When more than one test module opens its own
``TestClient(app)``, the second module's lifespan trips that guard.

The fix (bd-buiqr.8): a single SESSION-scoped ``client`` fixture here, shared by
every test module that exercises the app over HTTP (``test_server.py``,
``test_dual_path_routing.py``). The app's lifespan — and the session manager —
runs exactly once for the whole pytest session. Per-test auth state is injected
with ``unittest.mock.patch`` inside each test, so sharing the client is safe.
"""
import pytest
from starlette.testclient import TestClient

from server import app


@pytest.fixture(scope="session")
def client():
    """One TestClient (and one app lifespan) for the entire test session."""
    with TestClient(app) as c:
        yield c
