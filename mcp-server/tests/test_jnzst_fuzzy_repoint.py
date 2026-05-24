"""MCP layer repoint to the backend scoring core (enhancedchannelmanager-jnzst).

Pins:
* The MCP-local scorer symbols (_score_match / _generate_variants /
  _normalize_channel / _strip_stream_prefix / _fuzzy_helpers) are GONE.
* match_streams_to_channels and fuzzy_match_stream call the backend fuzzy
  preview endpoint (ac_fuzzy_preview) instead of scoring client-side.
* match_streams_to_channels DEFAULTS to dry-run (no add-stream writes).
* preview_fuzzy_matches exists and calls the backend.
* build_channel_lineup no longer imports the deleted helpers.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    from mcp.server.fastmcp import FastMCP
    from tools.streams import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# Deleted symbols
# ---------------------------------------------------------------------------

class TestLocalScorerDeleted:
    def test_score_match_gone(self):
        import tools.streams as s
        assert not hasattr(s, "_score_match")

    def test_generate_variants_gone(self):
        import tools.streams as s
        assert not hasattr(s, "_generate_variants")

    def test_normalize_channel_gone(self):
        import tools.streams as s
        assert not hasattr(s, "_normalize_channel")

    def test_strip_stream_prefix_gone(self):
        import tools.streams as s
        assert not hasattr(s, "_strip_stream_prefix")

    def test_fuzzy_helpers_export_gone(self):
        import tools.streams as s
        assert not hasattr(s, "_fuzzy_helpers")

    def test_channels_does_not_import_fuzzy_helpers(self):
        # The build_channel_lineup repoint must not reference the deleted export.
        import inspect
        import tools.channels as c
        src = inspect.getsource(c)
        assert "_fuzzy_helpers" not in src
        assert "_score_match" not in src

    def test_build_channel_lineup_dropped_dead_params(self):
        # FIX 5: provider_id and market were dead after the repoint — removed
        # from the signature (the docstring may explain the removal).
        import inspect
        import tools.channels as c
        src = inspect.getsource(c)
        # No parameter DECLARATION for the removed args (a colon-typed param).
        assert "provider_id: int" not in src
        assert "market: str" not in src


# ---------------------------------------------------------------------------
# Repointed tools call the backend
# ---------------------------------------------------------------------------

def _preview_response(triples, total_pages=1):
    return {"triples": triples, "total": len(triples), "page": 1,
            "page_size": 200, "total_pages": total_pages,
            "min_score": 0.6, "truncated": False}


class TestMatchStreamsToChannelsDryRunDefault:
    @pytest.mark.asyncio
    async def test_defaults_to_dry_run_no_writes(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            if endpoint.name == "channels_list":
                return {"results": [{"id": 1, "name": "WBAY", "streams": []}],
                        "next": None}
            if endpoint.name == "ac_fuzzy_preview":
                return _preview_response([
                    {"stream_id": 201, "stream_name": "ABC 2 WBAY",
                     "channel_id": "1", "channel_name": "WBAY",
                     "score": 0.95, "callsign_verdict": "match",
                     "signal": "fuzzy-with-callsign"},
                ])
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "match_streams_to_channels",
                {"group_ids": [42], "min_score": 0.6},
            )
        text = result[0][0].text
        assert "DRY-RUN" in text
        # It used the backend scoring core...
        assert "ac_fuzzy_preview" in calls
        # ...and made NO add-stream write.
        assert "channels_add_stream" not in calls

    @pytest.mark.asyncio
    async def test_apply_true_writes(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            if endpoint.name == "channels_list":
                return {"results": [{"id": 1, "name": "WBAY", "streams": []}],
                        "next": None}
            if endpoint.name == "ac_fuzzy_preview":
                return _preview_response([
                    {"stream_id": 201, "stream_name": "ABC 2 WBAY",
                     "channel_id": "1", "channel_name": "WBAY",
                     "score": 0.95, "callsign_verdict": "match",
                     "signal": "fuzzy-with-callsign"},
                ])
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "match_streams_to_channels",
                {"group_ids": [42], "min_score": 0.6, "apply": True},
            )
        text = result[0][0].text
        assert "Assigned" in text
        assert "channels_add_stream" in calls


class TestFuzzyMatchStreamCallsBackend:
    @pytest.mark.asyncio
    async def test_uses_preview_endpoint(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            if endpoint.name == "ac_fuzzy_preview":
                return _preview_response([
                    {"stream_id": 201, "stream_name": "ABC 2 WBAY",
                     "channel_id": "1", "channel_name": "WBAY",
                     "score": 0.95, "callsign_verdict": "match",
                     "signal": "fuzzy-with-callsign"},
                ])
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "fuzzy_match_stream", {"group_ids": [42], "min_score": 0.6},
            )
        text = result[0][0].text
        assert "ac_fuzzy_preview" in calls
        assert "score=0.95" in text


class TestWriteToolsInheritAdmissionPolicy:
    """FIX 2 — the MCP write tools assign purely on the triples the backend
    preview returns. Because the endpoint returns ADMISSIBLE-only triples
    (is_admissible: no conflict, no default absent), the write path inherits
    M1/M2 with no consumer-side policy: it can only assign what it is given.

    These tests verify the MCP layer does NOT re-admit a pair the endpoint
    excluded, and that allow_no_callsign threads through to the endpoint."""

    @pytest.mark.asyncio
    async def test_apply_assigns_only_returned_triples(self):
        # The endpoint returns exactly ONE admissible triple. A second channel
        # exists with no triple (the backend excluded it as absent/conflict).
        # apply=true must assign ONLY the returned one — no re-scoring locally.
        mcp = _make_mcp_and_register()
        add_stream_bodies = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "channels_list":
                return {"results": [
                    {"id": 1, "name": "WBAY", "streams": []},
                    {"id": 2, "name": "Cartoon Network", "streams": []},
                ], "next": None}
            if endpoint.name == "ac_fuzzy_preview":
                # Only channel 1 is admissible; channel 2 (e.g. an absent 1.0
                # subset-match) was filtered server-side and is NOT present.
                return _preview_response([
                    {"stream_id": 201, "stream_name": "ABC 2 WBAY",
                     "channel_id": "1", "channel_name": "WBAY",
                     "score": 0.95, "callsign_verdict": "match",
                     "signal": "fuzzy-with-callsign"},
                ])
            if endpoint.name == "channels_add_stream":
                add_stream_bodies.append(kwargs.get("body"))
                return {}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "match_streams_to_channels",
                {"group_ids": [42], "min_score": 0.0, "apply": True},
            )
        text = result[0][0].text
        # Exactly one assignment — channel 2 never got a stream (it was not an
        # admissible triple, and the tool does no score-only re-admission).
        assert "Assigned 1 of 1" in text
        assert len(add_stream_bodies) == 1
        assert add_stream_bodies[0] == {"stream_id": 201}

    @pytest.mark.asyncio
    async def test_preview_triples_threads_allow_no_callsign(self):
        from tools.streams import _preview_triples
        seen_queries = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            seen_queries.append(kwargs.get("query"))
            return _preview_response([])

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        # Default: allow_no_callsign False is sent.
        await _preview_triples(client, [42], 0.6)
        assert seen_queries[-1]["allow_no_callsign"] is False
        # Opt-in threads through.
        await _preview_triples(client, [42], 0.6, allow_no_callsign=True)
        assert seen_queries[-1]["allow_no_callsign"] is True


class TestPreviewFuzzyMatchesTool:
    @pytest.mark.asyncio
    async def test_preview_tool_calls_backend(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            if endpoint.name == "ac_fuzzy_preview":
                return _preview_response([
                    {"stream_id": 201, "stream_name": "ABC 2 WBAY",
                     "channel_id": "1", "channel_name": "WBAY",
                     "score": 0.95, "callsign_verdict": "match",
                     "signal": "fuzzy-with-callsign"},
                ])
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "preview_fuzzy_matches", {"group_ids": [42], "min_score": 0.6},
            )
        text = result[0][0].text
        assert "ac_fuzzy_preview" in calls
        assert "PREVIEW" in text
        assert "no writes" in text.lower()
