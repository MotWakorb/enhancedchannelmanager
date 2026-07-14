"""vkktd.5 — MCP list_tasks must surface effective_enabled, not the parent gate.

Background: the backend (vkktd.3) exposes ``effective_enabled`` on each task in
GET /api/tasks = (parent scheduled_tasks gate) AND (>=1 enabled child schedule).
A task can read ``enabled=True`` yet never fire (no active schedule). The MCP
``list_tasks`` tool previously formatted only ``enabled`` (the parent gate), so
an AI agent saw a gated-off task as "enabled" — the exact "reads Enabled but
won't run" trap the vkktd epic exists to eliminate, on the agent-facing surface.

These tests lock the three display states:
  1. effective_enabled True                     -> "enabled"
  2. enabled True + effective_enabled False      -> "enabled but WON'T RUN (no active schedule)"
  3. enabled False                               -> "disabled"
plus the defensive fallback when the backend omits effective_enabled.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_client(return_value=None, side_effect=None):
    mock = AsyncMock()
    if side_effect is not None:
        mock.call_endpoint.side_effect = side_effect
    else:
        mock.call_endpoint.return_value = return_value
    return mock


async def _run_list_tasks(tasks):
    from tools.tasks import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)

    mock_client = _make_client(return_value={"tasks": tasks})
    with patch("tools.tasks.get_ecm_client", return_value=mock_client):
        result = await mcp.call_tool("list_tasks", {})
    return result[0][0].text


class TestListTasksEffectiveEnabled:
    @pytest.mark.asyncio
    async def test_running_fine_shows_enabled(self):
        """effective_enabled True -> plain 'enabled' (parent on + active schedule)."""
        text = await _run_list_tasks([
            {"task_id": "m3u_refresh", "enabled": True, "effective_enabled": True,
             "status": "idle", "last_run": "2026-07-14T00:00:00Z"},
        ])
        line = [l for l in text.splitlines() if "m3u_refresh" in l][0]
        assert "enabled" in line
        assert "WON'T RUN" not in line
        assert "disabled" not in line

    @pytest.mark.asyncio
    async def test_enabled_but_wont_run_is_explicit(self):
        """enabled True + effective_enabled False -> explicit won't-run warning.

        This is the core of the bead: the gated-off-by-schedule task must NOT
        read as a bare 'enabled'.
        """
        text = await _run_list_tasks([
            {"task_id": "auto_creation", "enabled": True, "effective_enabled": False,
             "status": "idle", "last_run": "never"},
        ])
        line = [l for l in text.splitlines() if "auto_creation" in l][0]
        assert "WON'T RUN" in line
        assert "no active schedule" in line
        # Must not present as a plain enabled/disabled that hides the trap.
        assert "— enabled," not in line
        assert "— disabled," not in line

    @pytest.mark.asyncio
    async def test_disabled_parent_shows_disabled(self):
        """enabled False -> 'disabled' regardless of effective_enabled."""
        text = await _run_list_tasks([
            {"task_id": "stream_probe", "enabled": False, "effective_enabled": False,
             "status": "idle", "last_run": "never"},
        ])
        line = [l for l in text.splitlines() if "stream_probe" in l][0]
        assert "disabled" in line
        assert "WON'T RUN" not in line

    @pytest.mark.asyncio
    async def test_missing_effective_enabled_falls_back_to_parent_gate(self):
        """Older backend without effective_enabled: fall back to ``enabled``."""
        text = await _run_list_tasks([
            {"task_id": "legacy_on", "enabled": True, "status": "idle"},
            {"task_id": "legacy_off", "enabled": False, "status": "idle"},
        ])
        on_line = [l for l in text.splitlines() if "legacy_on" in l][0]
        off_line = [l for l in text.splitlines() if "legacy_off" in l][0]
        assert "enabled" in on_line and "WON'T RUN" not in on_line
        assert "disabled" in off_line

    @pytest.mark.asyncio
    async def test_all_three_states_in_one_listing(self):
        """A mixed listing renders each task in its correct state."""
        text = await _run_list_tasks([
            {"task_id": "running_fine", "enabled": True, "effective_enabled": True},
            {"task_id": "wont_run", "enabled": True, "effective_enabled": False},
            {"task_id": "turned_off", "enabled": False, "effective_enabled": False},
        ])
        lines = {l.split("id=")[1].split(")")[0]: l for l in text.splitlines() if "id=" in l}
        assert "WON'T RUN" not in lines["running_fine"]
        assert "disabled" not in lines["running_fine"]
        assert "WON'T RUN" in lines["wont_run"]
        assert "disabled" in lines["turned_off"]
        assert "WON'T RUN" not in lines["turned_off"]
