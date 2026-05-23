"""TDD tests for P2 stream fixes.

bd-1wq7z.16: cleanup_struck_out_streams must surface channel-deletion failures
bd-1wq7z.17: probe_single_stream must pass a 300s timeout to call_endpoint
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    """Register streams tools on a fresh FastMCP and return the mcp instance."""
    from mcp.server.fastmcp import FastMCP
    from tools.streams import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# bd-1wq7z.16 — cleanup_struck_out_streams surfaces channel-deletion failures
# ---------------------------------------------------------------------------

class TestCleanupStruckOutStreamsErrorSurfacing:
    """Verify that channel-deletion errors are reported in the result message."""

    @pytest.mark.asyncio
    async def test_channel_delete_failure_appears_in_result(self):
        """When one channel DELETE raises, the result message reports the failure.

        Before the fix: bare `except: pass` swallows the error and reports
        "No channels were left empty" (clean success).
        After the fix: the result describes how many deletions failed and why.
        """
        mcp = _make_mcp_and_register()

        # One stream in channel id=42, so channel 42 becomes empty after cleanup.
        struck_payload = {
            "streams": [
                {
                    "id": 1,
                    "stream_id": 1,
                    "name": "Stream A",
                    "channels": [{"id": 42}],
                },
            ],
            "threshold": 3,
            "enabled": True,
        }
        # Remove endpoint returns success.
        remove_payload = {"removed_from_channels": 1}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name  # Endpoint is a dataclass with .name attribute
            if name == "stream_stats_struck_out":
                return struck_payload
            if name == "stream_stats_struck_out_remove":
                return remove_payload
            if name == "channels_get":
                return {"id": 42, "name": "Empty Channel", "streams": []}
            if name == "channels_delete":
                raise Exception("403 Forbidden: channel locked")
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "cleanup_struck_out_streams",
                {"delete_empty_channels": True},
            )

        text = result[0][0].text
        # The error must surface — not be silently swallowed
        assert "fail" in text.lower() or "error" in text.lower() or "403" in text.lower(), (
            f"Expected failure to be reported but got: {text!r}"
        )
        # The warning about the failed deletion must be present
        assert "WARNING" in text or "fail" in text.lower(), (
            f"Expected WARNING or failure message in result: {text!r}"
        )
        # The failure reason must appear (not just "channel locked" but some trace)
        assert "403" in text or "Forbidden" in text or "locked" in text or "channel deletion" in text.lower(), (
            f"Expected failure detail in result: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_partial_failure_reports_count(self):
        """Result reports the number of failed deletions when some succeed and some fail."""
        mcp = _make_mcp_and_register()

        struck_payload = {
            "streams": [
                {"id": 1, "stream_id": 1, "name": "Stream A", "channels": [{"id": 10}]},
                {"id": 2, "stream_id": 2, "name": "Stream B", "channels": [{"id": 20}]},
            ],
            "threshold": 3,
            "enabled": True,
        }
        remove_payload = {"removed_from_channels": 2}

        call_counts = {"delete": 0}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "stream_stats_struck_out":
                return struck_payload
            if name == "stream_stats_struck_out_remove":
                return remove_payload
            if name == "channels_get":
                ch_id = kwargs.get("path_args", {}).get("channel_id")
                return {"id": ch_id, "name": f"Channel {ch_id}", "streams": []}
            if name == "channels_delete":
                call_counts["delete"] += 1
                if call_counts["delete"] == 2:
                    raise Exception("500 Server Error")
                return {}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "cleanup_struck_out_streams",
                {"delete_empty_channels": True},
            )

        text = result[0][0].text
        # Should report one success (deleted) and one failure
        assert "fail" in text.lower() or "error" in text.lower(), (
            f"Expected failure count in result: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_all_succeed_no_failure_line(self):
        """When all channel deletions succeed, no failure count is reported."""
        mcp = _make_mcp_and_register()

        struck_payload = {
            "streams": [
                {"id": 1, "stream_id": 1, "name": "Stream A", "channels": [{"id": 99}]},
            ],
            "threshold": 3,
            "enabled": True,
        }
        remove_payload = {"removed_from_channels": 1}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "stream_stats_struck_out":
                return struck_payload
            if name == "stream_stats_struck_out_remove":
                return remove_payload
            if name == "channels_get":
                return {"id": 99, "name": "Empty", "streams": []}
            if name == "channels_delete":
                return {}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "cleanup_struck_out_streams",
                {"delete_empty_channels": True},
            )

        text = result[0][0].text
        assert "Deleted" in text
        # Should not report failures when there are none
        assert "fail" not in text.lower()


# ---------------------------------------------------------------------------
# bd-1wq7z.17 — probe_single_stream must use a 300s timeout
# ---------------------------------------------------------------------------

class TestProbeSingleStreamTimeout:
    """Verify probe_single_stream passes timeout=300.0 to call_endpoint."""

    @pytest.mark.asyncio
    async def test_passes_300s_timeout(self):
        """probe_single_stream must pass timeout=300.0, matching the bulk probe variants."""
        mcp = _make_mcp_and_register()

        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "ok", "probe_status": "ok"}

        with patch("tools.streams.get_ecm_client", return_value=client):
            await mcp.call_tool("probe_single_stream", {"stream_id": 42})

        assert client.call_endpoint.called, "call_endpoint was not called"
        _args, kwargs = client.call_endpoint.call_args
        timeout = kwargs.get("timeout")
        assert timeout == 300.0, (
            f"Expected timeout=300.0 but probe_single_stream passed timeout={timeout!r}. "
            "This matches the bulk/all probe variants (probe_streams, probe_bulk_streams)."
        )

    @pytest.mark.asyncio
    async def test_result_includes_status(self):
        """probe_single_stream still returns the stream status in its result."""
        mcp = _make_mcp_and_register()

        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "healthy", "probe_status": "healthy"}

        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("probe_single_stream", {"stream_id": 7})

        text = result[0][0].text
        assert "healthy" in text
        assert "7" in text


# ---------------------------------------------------------------------------
# enhancedchannelmanager-znc76.5 — probe_bulk_streams async start+poll flow.
#
# probe_bulk_streams now START the background bulk probe (which 504'd when it
# ran synchronously), POLL get_probe_progress to completion, then report the
# real success/failed counts from get_probe_results. It must degrade gracefully
# when the poll budget is exhausted and when a probe is already running.
# ---------------------------------------------------------------------------

def _endpoint_router(responses: dict):
    """Build a call_endpoint side_effect that dispatches on the endpoint name.

    ``responses`` maps endpoint name -> a value or a list of values consumed in
    order across successive calls (so progress can transition in_progress ->
    done across polls).
    """
    from itertools import count

    cursors = {name: count() for name in responses}

    async def _call(ep, *args, **kwargs):
        name = ep.name
        value = responses[name]
        if isinstance(value, list):
            idx = next(cursors[name])
            return value[min(idx, len(value) - 1)]
        return value

    return _call


class TestProbeBulkStreamsAsyncFlow:
    """Verify probe_bulk_streams start+poll flow reports real counts."""

    @pytest.mark.asyncio
    async def test_starts_polls_and_reports_real_counts(self):
        """Starts the probe, polls progress to completion, reports envelope counts."""
        mcp = _make_mcp_and_register()

        responses = {
            "stream_stats_probe_bulk": {"status": "started", "total": 3},
            # First poll: still running. Second poll: done.
            "stream_stats_probe_progress": [
                {"in_progress": True, "current": 1, "total": 3},
                {"in_progress": False, "status": "completed", "total": 3,
                 "success_count": 2, "failed_count": 1},
            ],
            "stream_stats_probe_results": {
                "success_count": 2,
                "failed_count": 1,
                "success_streams": [{"id": 10, "name": "Stream 10"},
                                    {"id": 11, "name": "Stream 11"}],
                "failed_streams": [{"id": 12, "name": "Bad Stream",
                                    "error": "ffprobe failed: 404 Not Found"}],
            },
        }

        client = AsyncMock()
        client.call_endpoint.side_effect = _endpoint_router(responses)

        # No real sleeping between polls.
        with patch("tools.streams.get_ecm_client", return_value=client), \
             patch("tools.streams.asyncio.sleep", new=AsyncMock()):
            result = await mcp.call_tool("probe_bulk_streams", {"stream_ids": [10, 11, 12]})

        text = result[0][0].text
        assert "Bulk probe completed for 3 streams" in text
        assert "Success: 2" in text
        assert "Failed: 1" in text
        assert "Success: 0" not in text  # 0/0 accounting bug must not reappear
        # Failed stream listed with name + error from the results envelope.
        assert "Bad Stream" in text
        assert "404 Not Found" in text

    @pytest.mark.asyncio
    async def test_returns_already_running_without_polling(self):
        """If a probe is already running, report that instead of starting a second."""
        mcp = _make_mcp_and_register()

        responses = {
            "stream_stats_probe_bulk": {"status": "already_running"},
        }
        client = AsyncMock()
        client.call_endpoint.side_effect = _endpoint_router(responses)

        with patch("tools.streams.get_ecm_client", return_value=client), \
             patch("tools.streams.asyncio.sleep", new=AsyncMock()):
            result = await mcp.call_tool("probe_bulk_streams", {"stream_ids": [10, 11]})

        text = result[0][0].text
        assert "already in progress" in text.lower()
        # Should not have polled progress at all.
        called_endpoints = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert "stream_stats_probe_progress" not in called_endpoints

    @pytest.mark.asyncio
    async def test_degrades_gracefully_when_poll_cap_hit(self):
        """If the probe never finishes within the poll budget, say so — don't hang/error."""
        mcp = _make_mcp_and_register()

        responses = {
            "stream_stats_probe_bulk": {"status": "started", "total": 5},
            # Always still in progress -> poll cap is exhausted.
            "stream_stats_probe_progress": {"in_progress": True, "current": 2, "total": 5},
        }
        client = AsyncMock()
        client.call_endpoint.side_effect = _endpoint_router(responses)

        with patch("tools.streams.get_ecm_client", return_value=client), \
             patch("tools.streams.asyncio.sleep", new=AsyncMock()):
            result = await mcp.call_tool("probe_bulk_streams", {"stream_ids": [1, 2, 3, 4, 5]})

        text = result[0][0].text
        assert "still running" in text.lower()
        assert "get_probe_progress" in text
        # No exception leaked.
        assert "Error probing" not in text
