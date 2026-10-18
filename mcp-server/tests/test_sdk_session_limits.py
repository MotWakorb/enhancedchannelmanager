"""Production SDK configuration, not an eviction/lifecycle integration proof."""

from server import mcp


def test_production_stateful_http_manager_has_finite_session_limits():
    """CVE-2026-59951: do not silently restore unlimited session retention."""
    manager = mcp.session_manager
    assert manager.session_idle_timeout is not None
    assert 0 < manager.session_idle_timeout <= 1800
    assert manager.max_sessions is not None
    assert 0 < manager.max_sessions <= 10000
