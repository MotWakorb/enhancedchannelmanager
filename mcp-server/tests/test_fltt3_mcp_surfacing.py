"""MCP surfacing batch for today's shipped features (bead enhancedchannelmanager-fltt3).

Four gaps closed here, each: an ``Endpoint`` entry in ``_endpoint_contracts.py``
+ an ``@mcp.tool`` (new or renderer-only) in ``tools/*.py``:

1. get_stale_stream_ids (tools/streams.py) — GET /api/streams/stale-ids
   (bd-po78p), cross-referenced with the heavier get_stale_streams report.
2. get_provider_stats metric="stream_usage" (tools/stats.py) —
   GET /api/stats/providers/stream-usage (bd-n5cwp).
3. get_m3u_digest_settings / update_m3u_digest_settings (tools/m3u.py) —
   GET/PUT /api/m3u/digest/settings, incl. account_ids (bd-wwovg).
4. preview_event_sync renderer (tools/channel_pipeline.py) — surfaces
   preflight.warnings and the stale_suspect/freshness_unknown summary counts
   (bd-2ey2y / bd-jqwfq).
"""
import pytest
from unittest.mock import AsyncMock, patch


async def _tool_description(mcp, name: str) -> str:
    for t in await mcp.list_tools():
        if t.name == name:
            return t.description or ""
    raise AssertionError(f"tool {name!r} not registered")


# =============================================================================
# Gap 1 — get_stale_stream_ids (+ get_stale_streams cross-reference)
# =============================================================================


def _register_streams(mcp):
    from tools.streams import register
    register(mcp)


class TestGetStaleStreamIds:
    @pytest.mark.asyncio
    async def test_calls_streams_stale_ids_endpoint(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append((endpoint.name, kwargs.get("query")))
            return {"stale_stream_ids": [], "last_seen": {}, "count": 0}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            await mcp.call_tool("get_stale_stream_ids", {})

        assert calls == [("streams_stale_ids", {"bypass_cache": False})]

    @pytest.mark.asyncio
    async def test_bypass_cache_forwarded(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(kwargs.get("query"))
            return {"stale_stream_ids": [], "last_seen": {}, "count": 0}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.streams.get_ecm_client", return_value=client):
            await mcp.call_tool("get_stale_stream_ids", {"bypass_cache": True})

        assert calls == [{"bypass_cache": True}]

    @pytest.mark.asyncio
    async def test_empty_result_returns_message(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        client = AsyncMock()
        client.call_endpoint.return_value = {"stale_stream_ids": [], "last_seen": {}, "count": 0}
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_stale_stream_ids", {})

        text = result[0][0].text
        assert "No stale stream IDs" in text

    @pytest.mark.asyncio
    async def test_populated_result_renders_ids_and_last_seen(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        # Real shape: backend/routers/streams.py get_stale_stream_ids —
        # JSON round-trip turns dict int keys into strings.
        response = {
            "stale_stream_ids": [101, 102],
            "last_seen": {"101": "2026-07-10T12:00:00Z", "102": None},
            "count": 2,
        }
        client = AsyncMock()
        client.call_endpoint.return_value = response
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_stale_stream_ids", {})

        text = result[0][0].text
        assert "Stale stream IDs (2):" in text
        assert "id=101" in text
        assert "2026-07-10T12:00:00Z" in text
        assert "id=102" in text
        assert "unknown" in text

    @pytest.mark.asyncio
    async def test_truncation_message_beyond_fifty(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        ids = list(range(60))
        response = {
            "stale_stream_ids": ids,
            "last_seen": {str(i): None for i in ids},
            "count": 60,
        }
        client = AsyncMock()
        client.call_endpoint.return_value = response
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_stale_stream_ids", {})

        text = result[0][0].text
        assert "... and 10 more" in text

    @pytest.mark.asyncio
    async def test_error_reported_not_raised(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError("backend down")
        with patch("tools.streams.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_stale_stream_ids", {})

        text = result[0][0].text
        assert "Error getting stale stream ids" in text
        assert "backend down" in text

    @pytest.mark.asyncio
    async def test_cross_referenced_in_both_docstrings(self):
        """get_stale_streams and get_stale_stream_ids must each mention the
        other so an operator picking the wrong one finds the right one."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_streams(mcp)

        stale_streams_doc = await _tool_description(mcp, "get_stale_streams")
        stale_ids_doc = await _tool_description(mcp, "get_stale_stream_ids")

        assert "get_stale_stream_ids" in stale_streams_doc
        assert "get_stale_streams" in stale_ids_doc


# =============================================================================
# Gap 2 — get_provider_stats metric="stream_usage"
# =============================================================================


def _register_stats(mcp):
    from tools.stats import register
    register(mcp)


class TestGetProviderStatsStreamUsage:
    @pytest.mark.asyncio
    async def test_calls_stream_usage_endpoint(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            return {"data": [], "meta": {"total_rows": 0}, "pagination": None}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.stats.get_ecm_client", return_value=client):
            await mcp.call_tool("get_provider_stats", {"metric": "stream_usage"})

        assert calls == ["stats_providers_stream_usage"]

    @pytest.mark.asyncio
    async def test_empty_data_returns_message(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        client = AsyncMock()
        client.call_endpoint.return_value = {"data": [], "meta": {"total_rows": 0}, "pagination": None}
        with patch("tools.stats.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "stream_usage"})

        assert "No provider stream-usage data" in result[0][0].text

    @pytest.mark.asyncio
    async def test_populated_renders_table_incl_unknown_bucket(self):
        """Real shape from backend/routers/stats.py get_provider_stream_usage:
        {data: [{provider_id, provider_name, total_streams, assigned_streams,
        total_assignments, utilization_pct}], meta: {...}, pagination: null}.
        A stream whose m3u_account isn't in the current accounts list (or is
        None) buckets into a synthetic provider_name="Unknown" entry."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        response = {
            "data": [
                {
                    "provider_id": 1,
                    "provider_name": "FuboProvider",
                    "total_streams": 500,
                    "assigned_streams": 120,
                    "total_assignments": 140,
                    "utilization_pct": 24.0,
                },
                {
                    "provider_id": None,
                    "provider_name": "Unknown",
                    "total_streams": 0,
                    "assigned_streams": 3,
                    "total_assignments": 3,
                    "utilization_pct": 0.0,
                },
            ],
            "meta": {"total_rows": 2},
            "pagination": None,
        }
        client = AsyncMock()
        client.call_endpoint.return_value = response
        with patch("tools.stats.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "stream_usage"})

        text = result[0][0].text
        assert "Provider Stream Usage (2 providers):" in text
        assert "FuboProvider" in text
        assert "120 assigned streams" in text
        assert "of 500 total" in text
        assert "24.0% utilization" in text
        assert "140 total channel-assignments" in text
        assert "Unknown" in text

    @pytest.mark.asyncio
    async def test_stream_usage_in_valid_metrics_list(self):
        """metric parameter validation includes the new value."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        client = AsyncMock()
        with patch("tools.stats.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_provider_stats", {"metric": "bogus"})

        text = result[0][0].text
        assert "stream_usage" in text


# =============================================================================
# Gap 3 — get_m3u_digest_settings / update_m3u_digest_settings
# =============================================================================


def _register_m3u(mcp):
    from tools.m3u import register
    register(mcp)


# Real shape: backend models.py M3UDigestSettings.to_dict()
def _digest_settings(**overrides) -> dict:
    settings = {
        "id": 1,
        "enabled": False,
        "frequency": "daily",
        "email_recipients": [],
        "include_group_changes": True,
        "include_stream_changes": True,
        "show_detailed_list": True,
        "min_changes_threshold": 1,
        "send_to_discord": False,
        "exclude_group_patterns": [],
        "exclude_stream_patterns": [],
        "account_ids": [],
        "last_digest_at": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    settings.update(overrides)
    return settings


class TestGetM3UDigestSettings:
    @pytest.mark.asyncio
    async def test_calls_get_settings_endpoint(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            return _digest_settings()

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("get_m3u_digest_settings", {})

        assert calls == ["m3u_digest_get_settings"]

    @pytest.mark.asyncio
    async def test_default_settings_render(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        client = AsyncMock()
        client.call_endpoint.return_value = _digest_settings()
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_digest_settings", {})

        text = result[0][0].text
        assert "Enabled: False" in text
        assert "Frequency: daily" in text
        assert "Email recipients: none" in text
        assert "Account filter: all accounts" in text
        assert "Last digest sent: never" in text

    @pytest.mark.asyncio
    async def test_populated_settings_render_recipients_and_account_filter(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        response = _digest_settings(
            enabled=True,
            frequency="weekly",
            email_recipients=["ops@example.com", "alerts@example.com"],
            send_to_discord=True,
            account_ids=[3, 7],
            exclude_group_patterns=["^Test.*"],
            last_digest_at="2026-07-15T09:00:00Z",
        )
        client = AsyncMock()
        client.call_endpoint.return_value = response
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_digest_settings", {})

        text = result[0][0].text
        assert "Enabled: True" in text
        assert "Frequency: weekly" in text
        assert "ops@example.com, alerts@example.com" in text
        assert "Send to Discord: True" in text
        assert "Account filter: 3, 7" in text
        assert "Exclude group patterns: ^Test.*" in text
        assert "2026-07-15T09:00:00Z" in text

    @pytest.mark.asyncio
    async def test_error_reported_not_raised(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError("backend down")
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_digest_settings", {})

        text = result[0][0].text
        assert "Error getting M3U digest settings" in text


class TestUpdateM3UDigestSettings:
    @pytest.mark.asyncio
    async def test_no_args_returns_no_changes(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        client = AsyncMock()
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_m3u_digest_settings", {})

        assert "No changes specified" in result[0][0].text
        client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_passed_fields_sent(self):
        """Caller passing only account_ids must not send any other field —
        the backend's UpdateModel treats unset fields as None/no-op, but a
        stray key here would be a silent behavior change vs. what the
        operator asked for."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        bodies = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            bodies.append((endpoint.name, kwargs.get("body")))
            return _digest_settings(account_ids=[3, 7])

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("update_m3u_digest_settings", {"account_ids": [3, 7]})

        assert bodies == [("m3u_digest_update_settings", {"account_ids": [3, 7]})]

    @pytest.mark.asyncio
    async def test_multiple_fields_sent_together(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        bodies = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            bodies.append(kwargs.get("body"))
            return _digest_settings(enabled=True, frequency="hourly")

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "update_m3u_digest_settings",
                {"enabled": True, "frequency": "hourly"},
            )

        assert bodies == [{"enabled": True, "frequency": "hourly"}]
        text = result[0][0].text
        assert "enabled=True" in text
        assert "frequency=hourly" in text

    @pytest.mark.asyncio
    async def test_error_reported_not_raised(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_m3u(mcp)

        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError("validation failed")
        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_m3u_digest_settings", {"enabled": True})

        text = result[0][0].text
        assert "Error updating M3U digest settings" in text
        assert "validation failed" in text


# =============================================================================
# Gap 4 — preview_event_sync: preflight.warnings + staleness summary fields
# =============================================================================


def _register_channel_pipeline(mcp):
    from tools.channel_pipeline import register
    register(mcp)


def _preview_response(**overrides):
    response = {
        "preflight": {"ok": True, "failures": [], "warnings": []},
        "summary": {
            "secondary_streams": 3,
            "would_attach": 1,
            "ambiguous_skipped": 1,
            "unmatched": 1,
            "parse_failed": 0,
            "master_channels": 2,
            "master_channels_unparsed": 0,
            "stale_suspect_streams": 0,
            "freshness_unknown_streams": 0,
        },
        "streams": [],
        "unmatched_streams": [],
        "parse_failures": [],
        "unparsed_master_channels": [],
        "truncated": False,
    }
    response.update(overrides)
    return response


async def _call_preview(mcp, client, args):
    with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
        result = await mcp.call_tool("preview_event_sync", args)
    return result[0][0].text


class TestPreviewEventSyncWarningsAndStaleness:
    @pytest.mark.asyncio
    async def test_no_warnings_no_warning_line(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_channel_pipeline(mcp)
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response()
        text = await _call_preview(
            mcp, client, {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}}
        )
        assert "WARNING" not in text

    @pytest.mark.asyncio
    async def test_warning_present_even_when_preflight_ok(self):
        """bead 2ey2y: the staleness-rail warning fires alongside ok=True —
        it's advisory, never a failure."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_channel_pipeline(mcp)
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(preflight={
            "ok": True,
            "failures": [],
            "warnings": [{
                "check": "staleness_rail_snapshots",
                "expected": "a previous-day M3U snapshot covering at least "
                            "one secondary stream's provider + group",
                "got": "no snapshot coverage for any of 3 secondary stream(s)",
                "message": "The stale-dateless guard is enabled but "
                           "currently INERT: no previous-day M3U snapshot "
                           "covers this rule's secondary streams.",
            }],
        })
        text = await _call_preview(
            mcp, client, {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}}
        )
        assert "Pre-flight: OK" in text
        assert "WARNING: The stale-dateless guard is enabled" in text

    @pytest.mark.asyncio
    async def test_warning_renders_alongside_failures(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_channel_pipeline(mcp)
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(preflight={
            "ok": False,
            "failures": [{
                "group_id": 10, "role": "master", "check": "master_auto_sync_on",
                "expected": "auto_channel_sync ON", "got": "auto_channel_sync OFF",
                "message": "Master group 10 has auto_channel_sync OFF in Dispatcharr",
            }],
            "warnings": [{
                "check": "staleness_rail_snapshots",
                "expected": "snapshot coverage", "got": "none",
                "message": "Rail is inert.",
            }],
        })
        text = await _call_preview(
            mcp, client, {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}}
        )
        assert "Pre-flight: FAILED" in text
        assert "auto_channel_sync OFF" in text
        assert "WARNING: Rail is inert." in text

    @pytest.mark.asyncio
    async def test_summary_line_includes_staleness_counts(self):
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_channel_pipeline(mcp)
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(summary={
            "secondary_streams": 10,
            "would_attach": 4,
            "ambiguous_skipped": 2,
            "unmatched": 3,
            "parse_failed": 1,
            "master_channels": 5,
            "master_channels_unparsed": 0,
            "stale_suspect_streams": 6,
            "freshness_unknown_streams": 2,
        })
        text = await _call_preview(
            mcp, client, {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}}
        )
        assert "6 stale-suspect" in text
        assert "2 freshness-unknown" in text

    @pytest.mark.asyncio
    async def test_summary_staleness_counts_default_to_zero_when_absent(self):
        """Older/legacy response shapes without the jqwfq fields must not crash."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_channel_pipeline(mcp)
        client = AsyncMock()
        response = _preview_response()
        del response["summary"]["stale_suspect_streams"]
        del response["summary"]["freshness_unknown_streams"]
        client.call_endpoint.return_value = response
        text = await _call_preview(
            mcp, client, {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}}
        )
        assert "0 stale-suspect" in text
        assert "0 freshness-unknown" in text
