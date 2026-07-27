"""TDD tests for enhancedchannelmanager-yd6qh — parameter-drift batch fix.

Pass-through coverage for the missing params identified in coverage audit
0bzm4: create_channel (logo_id, tvg_id, normalize); update_channel (tvg_id,
logo_id, streams); bulk_commit_channels (groupsToCreate, consolidate);
create_m3u_account (full field set + account_type fix); update_m3u_account
(is_active); create/update_epg_source (is_active, refresh_interval, priority,
custom_properties); create/update_channel_pipeline_rule
(quality_m3u_tie_break_enabled, incl. deprecated alias forwarding);
create_task_schedule (parameters).

Every case follows the same shape: register the tool module on a fresh
FastMCP, mock get_ecm_client so call_endpoint is inspectable, invoke the
tool, and assert the payload sent to the backend has (or omits) the field
under test — proving omitted-when-None semantics hold.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp_with(module_name: str):
    from mcp.server.fastmcp import FastMCP
    import importlib

    module = importlib.import_module(module_name)
    mcp = FastMCP("test")
    module.register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# create_channel / update_channel
# ---------------------------------------------------------------------------
class TestCreateChannelNewParams:
    @pytest.mark.asyncio
    async def test_logo_id_tvg_id_normalize_sent_when_given(self):
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "ESPN", "channel_group_id": None}

        with patch("tools.channels.get_ecm_client", return_value=client):
            await mcp.call_tool("create_channel", {
                "name": "ESPN", "logo_id": 42, "tvg_id": "espn.us", "normalize": True,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["logo_id"] == 42
        assert body["tvg_id"] == "espn.us"
        assert body["normalize"] is True

    @pytest.mark.asyncio
    async def test_omitted_fields_absent_from_payload(self):
        """Omitted-when-None: logo_id/tvg_id must not appear when not given."""
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "ESPN"}

        with patch("tools.channels.get_ecm_client", return_value=client):
            await mcp.call_tool("create_channel", {"name": "ESPN"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert "logo_id" not in body
        assert "tvg_id" not in body
        # normalize has a real (non-Optional) backend default False and is
        # always sent, matching the backend model's own default.
        assert body["normalize"] is False


class TestUpdateChannelNewParams:
    @pytest.mark.asyncio
    async def test_tvg_id_logo_id_streams_sent_when_given(self):
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()

        async def side_effect(endpoint, **kwargs):
            if endpoint.name == "channels_update":
                return {"name": "ESPN", "channel_number": 1, "channel_group_id": 5}
            return {}

        client.call_endpoint.side_effect = side_effect

        with patch("tools.channels.get_ecm_client", return_value=client):
            await mcp.call_tool("update_channel", {
                "channel_id": 1, "tvg_id": "espn.us", "logo_id": 7, "streams": [10, 11, 12],
            })

        calls = [c for c in client.call_endpoint.call_args_list if c.args[0].name == "channels_update"]
        assert len(calls) == 1
        body = calls[0].kwargs["body"]
        assert body == {"tvg_id": "espn.us", "logo_id": 7, "streams": [10, 11, 12]}

    @pytest.mark.asyncio
    async def test_no_changes_specified_short_circuits(self):
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_channel", {"channel_id": 1})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestBulkCommitChannelsNewParams:
    @pytest.mark.asyncio
    async def test_groups_to_create_and_consolidate_forwarded(self):
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "completed", "success": True}

        groups = [{"name": "New Group", "tempId": -1}]
        with patch("tools.channels.get_ecm_client", return_value=client):
            await mcp.call_tool("bulk_commit_channels", {
                "operations": [{"type": "createChannel", "tempId": -2, "name": "X", "newGroupName": "New Group"}],
                "groups_to_create": groups,
                "consolidate": True,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["groupsToCreate"] == groups
        assert body["consolidate"] is True

    @pytest.mark.asyncio
    async def test_groups_to_create_omitted_when_none(self):
        mcp = _mcp_with("tools.channels")
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "completed", "success": True}

        with patch("tools.channels.get_ecm_client", return_value=client):
            await mcp.call_tool("bulk_commit_channels", {"operations": []})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert "groupsToCreate" not in body
        assert body["consolidate"] is False


# ---------------------------------------------------------------------------
# create_m3u_account / update_m3u_account
# ---------------------------------------------------------------------------
class TestCreateM3UAccountFullFieldSet:
    @pytest.mark.asyncio
    async def test_full_field_set_forwarded(self):
        mcp = _mcp_with("tools.m3u")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 9, "name": "Provider"}

        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("create_m3u_account", {
                "name": "Provider",
                "url": "http://xtream.example.com",
                "account_type": "XC",
                "username": "user1",
                "password": "secret",
                "file_path": "/config/m3u_uploads/x.m3u",
                "server_group": 3,
                "max_streams": 5,
                "is_active": False,
                "refresh_interval": 12,
                "stale_stream_days": 3,
                "enable_vod": True,
                "auto_enable_new_groups_live": False,
                "auto_enable_new_groups_vod": True,
                "auto_enable_new_groups_series": True,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["account_type"] == "XC"
        assert body["username"] == "user1"
        assert body["password"] == "secret"
        assert body["file_path"] == "/config/m3u_uploads/x.m3u"
        assert body["server_group"] == 3
        assert body["max_streams"] == 5
        assert body["is_active"] is False
        assert body["refresh_interval"] == 12
        assert body["stale_stream_days"] == 3
        assert body["enable_vod"] is True
        assert body["auto_enable_new_groups_live"] is False
        assert body["auto_enable_new_groups_vod"] is True
        assert body["auto_enable_new_groups_series"] is True
        # The old bogus "server_type" key must never be sent to the backend.
        assert "server_type" not in body

    @pytest.mark.asyncio
    async def test_legacy_server_type_maps_to_account_type(self):
        """server_type is a deprecated alias — 'xtream' must map to account_type='XC'."""
        mcp = _mcp_with("tools.m3u")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 9, "name": "Provider"}

        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("create_m3u_account", {
                "name": "Provider", "url": "http://x.example.com", "server_type": "xtream",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["account_type"] == "XC"
        assert "server_type" not in body

    @pytest.mark.asyncio
    async def test_explicit_account_type_wins_over_legacy_server_type(self):
        mcp = _mcp_with("tools.m3u")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 9, "name": "Provider"}

        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("create_m3u_account", {
                "name": "Provider", "url": "http://x.example.com",
                "account_type": "XC", "server_type": "standard",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["account_type"] == "XC"

    @pytest.mark.asyncio
    async def test_credentials_never_echoed_in_result_text(self):
        mcp = _mcp_with("tools.m3u")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 9, "name": "Provider"}

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_m3u_account", {
                "name": "Provider", "url": "http://x.example.com",
                "account_type": "XC", "username": "user1", "password": "hunter2",
            })

        text = _text(result)
        assert "hunter2" not in text
        assert "user1" not in text


class TestUpdateM3UAccountIsActive:
    @pytest.mark.asyncio
    async def test_is_active_forwarded(self):
        mcp = _mcp_with("tools.m3u")
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Provider"}

        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("update_m3u_account", {"account_id": 1, "is_active": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"is_active": False}


# ---------------------------------------------------------------------------
# create_epg_source / update_epg_source
# ---------------------------------------------------------------------------
class TestEPGSourceNewParams:
    @pytest.mark.asyncio
    async def test_create_forwards_new_fields(self):
        mcp = _mcp_with("tools.epg")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "Src"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            await mcp.call_tool("create_epg_source", {
                "name": "Src", "url": "http://epg.example.com/guide.xml",
                "is_active": False, "refresh_interval": 6, "priority": 2,
                "custom_properties": {"foo": "bar"},
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["is_active"] is False
        assert body["refresh_interval"] == 6
        assert body["priority"] == 2
        assert body["custom_properties"] == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_update_forwards_new_fields(self):
        mcp = _mcp_with("tools.epg")
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Src", "url": "http://x"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            await mcp.call_tool("update_epg_source", {
                "source_id": 1, "is_active": True, "refresh_interval": 24,
                "priority": 0, "custom_properties": {"a": 1},
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {
            "is_active": True, "refresh_interval": 24, "priority": 0,
            "custom_properties": {"a": 1},
        }

    @pytest.mark.asyncio
    async def test_update_omits_unset_new_fields(self):
        mcp = _mcp_with("tools.epg")
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Src", "url": "http://x"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            await mcp.call_tool("update_epg_source", {"source_id": 1, "name": "New Name"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "New Name"}


# ---------------------------------------------------------------------------
# create_channel_pipeline_rule / update_channel_pipeline_rule +
# deprecated create_auto_creation_rule / update_auto_creation_rule aliases
# ---------------------------------------------------------------------------
_MIN_CREATE_ARGS = {
    "name": "Rule",
    "conditions": [{"type": "always"}],
    "actions": [{"type": "skip"}],
}


class TestQualityM3UTieBreakEnabled:
    @pytest.mark.asyncio
    async def test_create_channel_pipeline_rule_forwards_flag(self):
        mcp = _mcp_with("tools.channel_pipeline")
        client = AsyncMock()
        client.call_endpoint.return_value = {"rule": {"id": 1}}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool("create_channel_pipeline_rule", {
                **_MIN_CREATE_ARGS, "quality_m3u_tie_break_enabled": False,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["quality_m3u_tie_break_enabled"] is False

    @pytest.mark.asyncio
    async def test_create_channel_pipeline_rule_omits_when_none(self):
        mcp = _mcp_with("tools.channel_pipeline")
        client = AsyncMock()
        client.call_endpoint.return_value = {"rule": {"id": 1}}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool("create_channel_pipeline_rule", _MIN_CREATE_ARGS)

        body = client.call_endpoint.call_args.kwargs["body"]
        assert "quality_m3u_tie_break_enabled" not in body

    @pytest.mark.asyncio
    async def test_deprecated_create_alias_forwards_flag(self):
        """create_auto_creation_rule must forward the new param unchanged (zrte6 pattern)."""
        mcp = _mcp_with("tools.channel_pipeline")
        client = AsyncMock()
        client.call_endpoint.return_value = {"rule": {"id": 1}}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool("create_auto_creation_rule", {
                **_MIN_CREATE_ARGS, "quality_m3u_tie_break_enabled": True,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["quality_m3u_tie_break_enabled"] is True

    @pytest.mark.asyncio
    async def test_update_channel_pipeline_rule_forwards_flag(self):
        mcp = _mcp_with("tools.channel_pipeline")
        client = AsyncMock()
        client.call_endpoint.return_value = {"rule": {"id": 1, "name": "Rule"}}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool("update_channel_pipeline_rule", {
                "rule_id": 1, "quality_m3u_tie_break_enabled": False,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"quality_m3u_tie_break_enabled": False}

    @pytest.mark.asyncio
    async def test_deprecated_update_alias_forwards_flag(self):
        mcp = _mcp_with("tools.channel_pipeline")
        client = AsyncMock()
        client.call_endpoint.return_value = {"rule": {"id": 1, "name": "Rule"}}

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool("update_auto_creation_rule", {
                "rule_id": 1, "quality_m3u_tie_break_enabled": True,
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"quality_m3u_tie_break_enabled": True}


# ---------------------------------------------------------------------------
# create_task_schedule
# ---------------------------------------------------------------------------
class TestCreateTaskScheduleParameters:
    @pytest.mark.asyncio
    async def test_parameters_forwarded_when_given(self):
        mcp = _mcp_with("tools.tasks")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "description": "daily"}

        with patch("tools.tasks.get_ecm_client", return_value=client):
            await mcp.call_tool("create_task_schedule", {
                "task_id": "m3u_refresh", "schedule_type": "daily",
                "schedule_time": "03:00", "parameters": {"account_ids": [1, 2]},
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["parameters"] == {"account_ids": [1, 2]}

    @pytest.mark.asyncio
    async def test_parameters_omitted_when_none(self):
        mcp = _mcp_with("tools.tasks")
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 5, "description": "daily"}

        with patch("tools.tasks.get_ecm_client", return_value=client):
            await mcp.call_tool("create_task_schedule", {
                "task_id": "m3u_refresh", "schedule_type": "daily", "schedule_time": "03:00",
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert "parameters" not in body
