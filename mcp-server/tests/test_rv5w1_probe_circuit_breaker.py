"""TDD tests for enhancedchannelmanager-rv5w1 — probe lifecycle + channel
pipeline circuit-breaker tools.

Covers get_probe_history, reset_probe_state (confirm-gated — force-resets a
possibly-still-running probe), dismiss_probe_failures,
list_dismissed_probe_failures (all tools/streams.py), plus
get_channel_pipeline_circuit_breaker and reset_channel_pipeline_circuit_breaker
(confirm-gated, tools/channel_pipeline.py).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _streams_mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.streams import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _pipeline_mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.channel_pipeline import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# get_probe_history
# ---------------------------------------------------------------------------
class TestGetProbeHistory:
    @pytest.mark.asyncio
    async def test_lists_runs_most_recent_first(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = [
            {"timestamp": "2026-07-09T10:00:00Z", "total": 50, "success_count": 45, "failed_count": 5, "status": "completed"},
            {"timestamp": "2026-07-08T10:00:00Z", "total": 50, "success_count": 50, "failed_count": 0, "status": "completed"},
        ]

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_probe_history", {})

        text = _text(result)
        assert "2 run" in text
        assert "45/50" in text
        assert "5 failed" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["stream_stats_probe_history"]

    @pytest.mark.asyncio
    async def test_no_history(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = []

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_probe_history", {})

        assert "No probe run history" in _text(result)


# ---------------------------------------------------------------------------
# reset_probe_state
# ---------------------------------------------------------------------------
class TestResetProbeState:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_resets_nothing(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"in_progress": True, "status": "probing"}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("reset_probe_state", {})

        text = _text(result)
        assert "IN PROGRESS" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["stream_stats_probe_progress"]

    @pytest.mark.asyncio
    async def test_confirm_true_resets(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "reset", "message": "Probe state forcibly reset (was_in_progress=True)"}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("reset_probe_state", {"confirm": True})

        assert "reset" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["stream_stats_probe_reset"]


# ---------------------------------------------------------------------------
# dismiss_probe_failures / list_dismissed_probe_failures
# ---------------------------------------------------------------------------
class TestDismissProbeFailures:
    @pytest.mark.asyncio
    async def test_dismisses_given_streams(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"dismissed": 3, "stream_ids": [1, 2, 3]}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("dismiss_probe_failures", {"stream_ids": [1, 2, 3]})

        assert "3 stream" in _text(result)
        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"stream_ids": [1, 2, 3]}


class TestListDismissedProbeFailures:
    @pytest.mark.asyncio
    async def test_lists_dismissed_ids(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"dismissed_stream_ids": [7, 8], "count": 2}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_dismissed_probe_failures", {})

        text = _text(result)
        assert "2" in text
        assert "7" in text and "8" in text

    @pytest.mark.asyncio
    async def test_none_dismissed(self):
        mcp = _streams_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"dismissed_stream_ids": [], "count": 0}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_dismissed_probe_failures", {})

        assert "No dismissed" in _text(result)


# ---------------------------------------------------------------------------
# get_channel_pipeline_circuit_breaker
# ---------------------------------------------------------------------------
class TestGetChannelPipelineCircuitBreaker:
    @pytest.mark.asyncio
    async def test_clear(self):
        mcp = _pipeline_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"disabled": False, "reason": None}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_channel_pipeline_circuit_breaker", {})

        assert "clear" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_tripped(self):
        mcp = _pipeline_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"disabled": True, "reason": "abandoned_run"}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_channel_pipeline_circuit_breaker", {})

        text = _text(result)
        assert "TRIPPED" in text
        assert "abandoned_run" in text


# ---------------------------------------------------------------------------
# reset_channel_pipeline_circuit_breaker
# ---------------------------------------------------------------------------
class TestResetChannelPipelineCircuitBreaker:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false(self):
        mcp = _pipeline_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"disabled": True, "reason": "abandoned_run"}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool("reset_channel_pipeline_circuit_breaker", {})

        text = _text(result)
        assert "TRIPPED" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channel_pipeline_circuit_breaker"]

    @pytest.mark.asyncio
    async def test_already_clear_short_circuits(self):
        mcp = _pipeline_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"disabled": False, "reason": None}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool("reset_channel_pipeline_circuit_breaker", {})

        assert "already clear" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channel_pipeline_circuit_breaker"]

    @pytest.mark.asyncio
    async def test_confirm_true_clears(self):
        mcp = _pipeline_mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"success": True, "was_disabled": True, "disabled": False}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool("reset_channel_pipeline_circuit_breaker", {"confirm": True})

        assert "cleared" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channel_pipeline_reset_circuit_breaker"]
