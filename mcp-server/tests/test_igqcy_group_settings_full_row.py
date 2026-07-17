"""MCP group-settings tools vs Dispatcharr's full-row upsert + v0.24+ serializer
(bead enhancedchannelmanager-igqcy).

Two defects pinned here, both verified live against Dispatcharr v0.27.2:

1. **Name resolution**: Dispatcharr's account serializer stopped providing a
   ``name`` field on ``channel_groups[]`` entries in v0.24.0 — the old
   resolver read that field and returned "group not found" for every call.
   Names must be resolved by joining ``channel_groups[].channel_group`` (an
   int id) against the global channel-group list (``groups_list`` endpoint).

2. **Full-row writes**: Dispatcharr's group-settings upsert resets every
   omitted field (enabled -> True, auto_channel_sync -> False, start/end ->
   None, custom_properties -> {}). The tools previously sent only
   ``{channel_group, enabled}`` — silently wiping native auto-sync config.
   Every write must carry the group's complete current row with only
   ``enabled`` overlaid, custom_properties verbatim (unknown keys survive).

The embedded-row shape below is the REAL v0.27.2 serializer output recorded
during the bd-478fe QA pass: no ``name`` key, ``auto_sync_channel_end``
present, sync-consumed custom_properties keys ECM does not model.
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP


# Real v0.27.2 channel_groups[] entry shape (recorded): NO "name" field.
def _v0272_row(**overrides) -> dict:
    row = {
        "channel_group": 1304,
        "enabled": True,
        "auto_channel_sync": True,
        "auto_sync_channel_start": 1,
        "auto_sync_channel_end": 500,
        "custom_properties": {
            "group_override": 7,                   # ECM-known key
            "channel_numbering_mode": "assigned",  # unknown to ECM
            "force_dummy_epg": True,               # unknown to ECM
        },
        "is_stale": False,
        "last_seen": "2026-07-16T23:34:02.303249Z",
        "stream_count": 56,
    }
    row.update(overrides)
    return row


def _account_v0272(rows: list[dict]) -> dict:
    return {"id": 11, "name": "HD Homerun", "channel_groups": rows}


def _register() -> FastMCP:
    from tools.m3u import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


class TestNameResolutionV024Serializer:
    """Resolver must find groups whose serializer entries have no name field."""

    @pytest.mark.asyncio
    async def test_resolves_nameless_group_via_groups_list_join(self):
        mcp = _register()
        mock_client = AsyncMock()
        # Call order: m3u_get_account (no names) -> groups_list (join source).
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}, {"id": 2, "name": "Other"}],
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 11, "group_name": "HD Homerun", "enabled": False},
            )

        text = result[0][0].text
        assert "not found" not in text.lower(), f"resolver failed: {text}"
        assert "disabled" in text.lower()
        mock_client.patch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_name_still_reports_not_found(self):
        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}],
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 11, "group_name": "DoesNotExist", "enabled": False},
            )

        assert "not found" in result[0][0].text.lower()
        mock_client.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_embedded_names_skip_the_groups_list_fetch(self):
        """Pre-v0.24 payloads (name embedded) resolve without the extra call."""
        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _account_v0272(
            [_v0272_row(name="Sports")]
        )
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 11, "group_name": "Sports", "enabled": False},
            )

        assert "not found" not in result[0][0].text.lower()
        # Exactly ONE call_endpoint call — the account fetch, no groups_list.
        assert mock_client.call_endpoint.await_count == 1


class TestFullRowWrites:
    """Writes must carry the complete current row — Dispatcharr's upsert
    resets every omitted field."""

    @pytest.mark.asyncio
    async def test_update_sends_complete_row_with_unknown_cp_keys(self):
        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}],
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 11, "group_name": "HD Homerun", "enabled": False},
            )

        body = mock_client.patch.call_args.kwargs.get("json_data") \
            or mock_client.patch.call_args.args[1]
        row = body["group_settings"][0]
        assert row["channel_group"] == 1304
        assert row["enabled"] is False                    # the intended change
        assert row["auto_channel_sync"] is True           # preserved
        assert row["auto_sync_channel_start"] == 1        # preserved
        assert row["auto_sync_channel_end"] == 500        # preserved (v0.25.0+)
        # custom_properties verbatim — unknown keys survive the round-trip.
        assert row["custom_properties"] == {
            "group_override": 7,
            "channel_numbering_mode": "assigned",
            "force_dummy_epg": True,
        }
        # Read-only serializer fields must not leak into the write.
        assert "is_stale" not in row
        assert "last_seen" not in row
        assert "stream_count" not in row

    @pytest.mark.asyncio
    async def test_bulk_update_sends_complete_rows(self):
        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([
                _v0272_row(),
                _v0272_row(
                    channel_group=2, enabled=False, auto_channel_sync=False,
                    auto_sync_channel_start=None, auto_sync_channel_end=None,
                    custom_properties={},
                ),
            ]),
            [{"id": 1304, "name": "HD Homerun"}, {"id": 2, "name": "News"}],
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_update_m3u_group_settings",
                {"account_id": 11, "groups": {"HD Homerun": False, "News": True}},
            )

        body = mock_client.patch.call_args.kwargs.get("json_data") \
            or mock_client.patch.call_args.args[1]
        rows = {r["channel_group"]: r for r in body["group_settings"]}
        assert rows[1304]["enabled"] is False
        assert rows[1304]["auto_channel_sync"] is True
        assert rows[1304]["auto_sync_channel_end"] == 500
        assert rows[1304]["custom_properties"]["channel_numbering_mode"] == "assigned"
        assert rows[2]["enabled"] is True
        assert rows[2]["auto_channel_sync"] is False
        assert "2" in result[0][0].text  # 2 groups updated


class TestTriggerRefresh:
    """Optional trigger_refresh chains an account refresh after the save;
    the default (False) leaves refresh to the schedule and says so."""

    @pytest.mark.asyncio
    async def test_default_does_not_refresh(self):
        from _endpoint_contracts import ENDPOINTS

        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}],
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {"account_id": 11, "group_name": "HD Homerun", "enabled": True},
            )

        refresh_calls = [
            c for c in mock_client.call_endpoint.await_args_list
            if c.args and c.args[0] is ENDPOINTS["m3u_refresh_account"]
        ]
        assert not refresh_calls
        assert "next m3u refresh" in result[0][0].text.lower()

    @pytest.mark.asyncio
    async def test_trigger_refresh_calls_refresh_endpoint(self):
        from _endpoint_contracts import ENDPOINTS

        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}],
            {"success": True},  # m3u_refresh_account
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_m3u_group_settings",
                {
                    "account_id": 11,
                    "group_name": "HD Homerun",
                    "enabled": True,
                    "trigger_refresh": True,
                },
            )

        refresh_calls = [
            c for c in mock_client.call_endpoint.await_args_list
            if c.args and c.args[0] is ENDPOINTS["m3u_refresh_account"]
        ]
        assert len(refresh_calls) == 1
        assert refresh_calls[0].kwargs["path_args"] == {"account_id": 11}
        assert "refresh started" in result[0][0].text.lower()

    @pytest.mark.asyncio
    async def test_bulk_trigger_refresh(self):
        from _endpoint_contracts import ENDPOINTS

        mcp = _register()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = [
            _account_v0272([_v0272_row()]),
            [{"id": 1304, "name": "HD Homerun"}],
            {"success": True},  # m3u_refresh_account
        ]
        mock_client.patch.return_value = None

        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_update_m3u_group_settings",
                {
                    "account_id": 11,
                    "groups": {"HD Homerun": False},
                    "trigger_refresh": True,
                },
            )

        refresh_calls = [
            c for c in mock_client.call_endpoint.await_args_list
            if c.args and c.args[0] is ENDPOINTS["m3u_refresh_account"]
        ]
        assert len(refresh_calls) == 1
        assert "refresh started" in result[0][0].text.lower()
