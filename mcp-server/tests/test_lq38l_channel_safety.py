"""TDD tests for lq38l channel-tool safety fixes.

bd-lq38l.3: reorder_streams must not silently detach/clear omitted streams —
    only a genuine permutation of the channel's current streams may proceed.
bd-lq38l.5: merge_channels must guard self-merge (data loss) and report the
    backend's per-result outcome instead of the requested count (false success).
bd-lq38l.6: build_channel_lineup must count/match ONLY channels created by THIS
    call (via the bulk-commit tempIdMap), never pre-existing group channels.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    """Register channels tools on a fresh FastMCP and return the mcp instance."""
    from mcp.server.fastmcp import FastMCP
    from tools.channels import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# bd-lq38l.3 — reorder_streams permutation guard
# ---------------------------------------------------------------------------

class TestReorderStreamsPermutationGuard:
    @pytest.mark.asyncio
    async def test_omitting_a_current_stream_is_refused(self):
        """[5001,5002,5204] then reorder([5001,5002]) must refuse — 5204 dropped."""
        mcp = _make_mcp_and_register()

        reorder_called = {"count": 0}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "channels_get":
                return {"id": 12340, "streams": [5001, 5002, 5204]}
            if name == "channels_reorder_streams":
                reorder_called["count"] += 1
                return {"streams": kwargs.get("body", {}).get("stream_ids", [])}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "reorder_streams", {"channel_id": 12340, "stream_ids": [5001, 5002]}
            )

        text = result[0][0].text
        assert "5204" in text, f"Dropped stream id must be named: {text!r}"
        assert "Refused" in text or "detach" in text.lower()
        assert reorder_called["count"] == 0, "Backend reorder must NOT be called"

    @pytest.mark.asyncio
    async def test_empty_list_is_refused(self):
        """reorder([]) must refuse — an empty list would clear all streams."""
        mcp = _make_mcp_and_register()

        reorder_called = {"count": 0}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "channels_get":
                return {"id": 12340, "streams": [5001, 5002, 5204]}
            if name == "channels_reorder_streams":
                reorder_called["count"] += 1
                return {"streams": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "reorder_streams", {"channel_id": 12340, "stream_ids": []}
            )

        text = result[0][0].text
        assert "Refused" in text or "clear" in text.lower()
        assert "3" in text, f"Should report the count that would be cleared: {text!r}"
        assert reorder_called["count"] == 0, "Backend reorder must NOT be called"

    @pytest.mark.asyncio
    async def test_foreign_id_is_refused(self):
        """A stream id not on the channel must be refused and named."""
        mcp = _make_mcp_and_register()

        reorder_called = {"count": 0}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "channels_get":
                return {"id": 12340, "streams": [5001, 5002]}
            if name == "channels_reorder_streams":
                reorder_called["count"] += 1
                return {"streams": kwargs.get("body", {}).get("stream_ids", [])}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "reorder_streams", {"channel_id": 12340, "stream_ids": [5001, 9999]}
            )

        text = result[0][0].text
        assert "9999" in text, f"Foreign stream id must be named: {text!r}"
        assert reorder_called["count"] == 0, "Backend reorder must NOT be called"

    @pytest.mark.asyncio
    async def test_genuine_permutation_proceeds(self):
        """A true permutation reorders via the backend and reports the new order."""
        mcp = _make_mcp_and_register()

        reorder_called = {"count": 0}

        async def call_endpoint_side_effect(endpoint, **kwargs):
            name = endpoint.name
            if name == "channels_get":
                return {"id": 12340, "streams": [5001, 5002, 5204]}
            if name == "channels_reorder_streams":
                reorder_called["count"] += 1
                return {"streams": kwargs.get("body", {}).get("stream_ids", [])}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "reorder_streams",
                {"channel_id": 12340, "stream_ids": [5204, 5001, 5002]},
            )

        text = result[0][0].text
        assert reorder_called["count"] == 1, "Backend reorder MUST be called for a permutation"
        assert "reordered" in text.lower()
        assert "5204" in text


# ---------------------------------------------------------------------------
# bd-lq38l.5 — merge_channels self-merge + per-result reporting
# ---------------------------------------------------------------------------

class TestMergeChannelsSafety:
    @pytest.mark.asyncio
    async def test_self_merge_is_refused_before_backend_call(self):
        """merge_channels(X, [X]) must refuse without any backend call (data loss)."""
        mcp = _make_mcp_and_register()

        client = AsyncMock()
        client.call_endpoint.side_effect = AssertionError("backend must NOT be called")

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "merge_channels",
                {"target_channel_id": 12343, "source_channel_ids": [12343]},
            )

        text = result[0][0].text
        assert "Refused" in text or "itself" in text.lower()
        assert "12343" in text
        client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonexistent_target_reports_failure_not_success(self):
        """A per-result failure (bad target) must be reported, not the requested count."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            # Mirror backend: bad target -> per-result success=False, error set.
            return {
                "merged": 0,
                "failed": 1,
                "results": [
                    {
                        "target_channel_id": 99999999,
                        "success": False,
                        "error": "HTTPStatusError",
                    }
                ],
            }

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "merge_channels",
                {"target_channel_id": 99999999, "source_channel_ids": [12342]},
            )

        text = result[0][0].text
        assert "fail" in text.lower(), f"Expected failure report: {text!r}"
        assert "Merged 1" not in text, f"Must NOT claim false success: {text!r}"
        assert "HTTPStatusError" in text

    @pytest.mark.asyncio
    async def test_successful_merge_reports_actual_count(self):
        """A genuine success reports the backend's sources_deleted count."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            return {
                "merged": 1,
                "failed": 0,
                "results": [
                    {
                        "target_channel_id": 100,
                        "target_name": "Keeper",
                        "sources_deleted": 2,
                        "total_streams": 5,
                        "success": True,
                    }
                ],
            }

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "merge_channels",
                {"target_channel_id": 100, "source_channel_ids": [101, 102]},
            )

        text = result[0][0].text
        assert "Merged 2 channels into channel 100" in text


# ---------------------------------------------------------------------------
# bd-lq38l.6 — build_channel_lineup created-only count + matching
# ---------------------------------------------------------------------------

class TestBuildChannelLineupCreatedOnly:
    @pytest.mark.asyncio
    async def test_counts_only_newly_created_via_tempidmap(self):
        """Pre-existing group channels must not be counted as created or matched."""
        mcp = _make_mcp_and_register()

        # Group already has 3 pre-existing channels; we ask to create ONE (id 555).
        group_channels = [
            {"id": 12340, "name": "MCPTEST_FoxNewsHD", "channel_number": 360, "streams": []},
            {"id": 12341, "name": "US: ESPN U", "channel_number": 12, "streams": [1, 2]},
            {"id": 12344, "name": "MCPTEST_Sac2", "channel_number": 12, "streams": []},
            {"id": 555, "name": "MCPTEST Lineup ESPN", "channel_number": 901, "streams": []},
        ]

        async def post_side_effect(path, **kwargs):
            if path == "/api/channels/bulk-commit":
                # tempId -1 -> realId 555 (the only channel this call created)
                return {"success": True, "tempIdMap": {"-1": 555}}
            # add-stream: nothing matched in this scenario
            return {}

        async def get_side_effect(path, **kwargs):
            if path == "/api/channels":
                return {"results": group_channels, "next": None}
            if path == "/api/streams":
                return {"results": []}  # no streams available -> 0 matched
            return {}

        client = AsyncMock()
        client.post.side_effect = post_side_effect
        client.get.side_effect = get_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "build_channel_lineup",
                {
                    "channels": [{"name": "MCPTEST Lineup ESPN", "number": 901}],
                    "group_id": 1351,
                    "provider_id": 3,
                },
            )

        text = result[0][0].text
        assert "1 channels created" in text, f"Must count ONLY the created channel: {text!r}"
        assert "4 channels created" not in text, f"Must not count the whole group: {text!r}"
        # Pre-existing channels must never appear in the unmatched list.
        assert "MCPTEST_FoxNewsHD" not in text
        assert "MCPTEST_Sac2" not in text
        # The single created channel is the only unmatched one (no streams available).
        assert "1 unmatched" in text
        assert "MCPTEST Lineup ESPN" in text

    @pytest.mark.asyncio
    async def test_fallback_to_name_number_when_tempidmap_absent(self):
        """Without tempIdMap, fall back to name+number — still excludes pre-existing."""
        mcp = _make_mcp_and_register()

        group_channels = [
            {"id": 12340, "name": "Pre A", "channel_number": 1, "streams": []},
            {"id": 12341, "name": "Pre B", "channel_number": 2, "streams": []},
            {"id": 700, "name": "New One", "channel_number": 700, "streams": []},
        ]

        async def post_side_effect(path, **kwargs):
            if path == "/api/channels/bulk-commit":
                return {"success": True}  # no tempIdMap
            return {}

        async def get_side_effect(path, **kwargs):
            if path == "/api/channels":
                return {"results": group_channels, "next": None}
            if path == "/api/streams":
                return {"results": []}
            return {}

        client = AsyncMock()
        client.post.side_effect = post_side_effect
        client.get.side_effect = get_side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "build_channel_lineup",
                {
                    "channels": [{"name": "New One", "number": 700}],
                    "group_id": 99,
                },
            )

        text = result[0][0].text
        assert "1 channels created" in text, f"Fallback must count only requested: {text!r}"
        assert "Pre A" not in text
        assert "Pre B" not in text
