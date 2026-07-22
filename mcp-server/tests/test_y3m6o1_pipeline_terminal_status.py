"""Regression tests for y3m6o.1 review (build 0.17.6-0152) — run_channel_pipeline
poll-loop terminal-status handling.

Blocker 2: ``_TERMINAL_STATUSES`` omitted ``completed_with_errors`` and
``capped``, so a run finalizing in either state was polled all
``_POLL_MAX_ATTEMPTS`` times and then falsely reported "still running after N
polls". These tests prove:

  * EACH real terminal status a ChannelPipelineExecution can persist
    (completed, failed, rolled_back, capped, completed_with_errors) exits the
    poll loop on the FIRST poll — no timeout, exactly one status poll.
  * A ``completed_with_errors`` terminal result surfaces the failed-action
    warning/error summary (error_message) to the caller rather than a generic
    "complete".
  * A ``capped`` terminal result surfaces its cap guidance.
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP


def _client(side_effect):
    mock = AsyncMock()
    mock.call_endpoint.side_effect = side_effect
    return mock


def _register() -> FastMCP:
    mcp = FastMCP("test")
    from tools.channel_pipeline import register
    register(mcp)
    return mcp


async def _run(mock_client) -> str:
    """Register the tool against a fresh FastMCP wired to ``mock_client`` and
    invoke run_channel_pipeline, returning the rendered text."""
    with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
        mcp = _register()
        result = await mcp.call_tool("run_channel_pipeline", {"dry_run": False})
    return result[0][0].text


# Every value ChannelPipelineExecution.status can persist that is TERMINAL
# (see alembic 0039 + finalization branches in channel_pipeline_engine.py).
_ALL_TERMINAL = ["completed", "failed", "rolled_back", "capped", "completed_with_errors"]


@pytest.mark.parametrize("status", _ALL_TERMINAL)
@pytest.mark.asyncio
async def test_each_terminal_status_exits_poll_loop_immediately(status):
    """Each terminal status breaks the poll loop on the first poll: the tool
    calls call_endpoint exactly twice (kickoff + one status poll) and never
    returns the timeout string, even with a tiny attempt cap that would trip
    if the status were treated as non-terminal."""
    kickoff = {"execution_id": 42, "status": "running"}
    final = {
        "id": 42, "status": status, "mode": "execute",
        "streams_evaluated": 10, "streams_matched": 3, "channels_created": 3,
        "duration_seconds": 1.0, "error_message": "some detail",
    }
    mock_client = _client(side_effect=[kickoff, final])
    with (
        patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client),
        patch("tools.channel_pipeline._poll_sleep", new=AsyncMock(return_value=None)),
        patch("tools.channel_pipeline._POLL_MAX_ATTEMPTS", 3),
    ):
        mcp = _register()
        result = await mcp.call_tool("run_channel_pipeline", {"dry_run": False})
    text = result[0][0].text

    # Exactly one status poll after the kickoff — proves an immediate break.
    assert mock_client.call_endpoint.await_count == 2
    assert "still running after" not in text


@pytest.mark.asyncio
async def test_completed_with_errors_surfaces_error_summary():
    """A completed_with_errors run reports it completed WITH ERRORS and includes
    the failed-action summary — not a generic 'complete' with no warning."""
    kickoff = {"execution_id": 9, "status": "running"}
    summary = "1 action failed: 'Movie Rule' sort_group. Rerunning is safe."
    final = {
        "id": 9, "status": "completed_with_errors", "mode": "execute",
        "streams_evaluated": 10, "streams_matched": 5, "channels_created": 5,
        "duration_seconds": 2.0, "error_message": summary,
    }
    mock_client = _client(side_effect=[kickoff, final])
    with patch("tools.channel_pipeline._poll_sleep", new=AsyncMock(return_value=None)):
        result = await _run(mock_client)

    assert "still running after" not in result
    assert "WITH ERRORS" in result
    assert summary in result
    # Not silently reported as a clean completion.
    assert "complete (execution_id=9)" not in result


@pytest.mark.asyncio
async def test_capped_surfaces_cap_guidance():
    """A capped run reports it was CAPPED and surfaces the cap error_message."""
    kickoff = {"execution_id": 11, "status": "running"}
    guidance = "Created-channel cap reached: created 50 of ~120 matched."
    final = {
        "id": 11, "status": "capped", "mode": "execute",
        "streams_evaluated": 200, "streams_matched": 120, "channels_created": 50,
        "duration_seconds": 3.0, "error_message": guidance,
    }
    mock_client = _client(side_effect=[kickoff, final])
    with patch("tools.channel_pipeline._poll_sleep", new=AsyncMock(return_value=None)):
        result = await _run(mock_client)

    assert "still running after" not in result
    assert "CAPPED" in result
    assert guidance in result


@pytest.mark.asyncio
async def test_completed_with_errors_discloses_non_reversible_membership():
    """When the completed_with_errors run also changed channel-profile
    membership, the terminal summary discloses rollback will not restore it."""
    kickoff = {"execution_id": 13, "status": "running"}
    final = {
        "id": 13, "status": "completed_with_errors", "mode": "execute",
        "streams_evaluated": 10, "streams_matched": 5, "channels_created": 0,
        "duration_seconds": 1.0, "error_message": "1 action failed.",
        "has_non_reversible_profile_changes": True,
    }
    mock_client = _client(side_effect=[kickoff, final])
    with patch("tools.channel_pipeline._poll_sleep", new=AsyncMock(return_value=None)):
        result = await _run(mock_client)

    assert "channel-profile membership" in result
    assert "Rollback" in result
