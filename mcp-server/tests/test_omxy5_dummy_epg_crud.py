"""TDD tests for enhancedchannelmanager-omxy5 — dummy-EPG profile CRUD tools.

Covers get/create/update/delete_dummy_epg_profile and its test companion
preview_dummy_epg. Delete is confirm-gated and previews channel-group
assignments (the blast radius), mirroring delete_logo /
delete_normalization_group.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.epg import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


class TestGetDummyEpgProfile:
    @pytest.mark.asyncio
    async def test_shows_config_and_group_count(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 1, "name": "Sports Placeholder", "enabled": True,
            "name_source": "channel", "stream_index": 1,
            "title_pattern": r"^(.*?) - ", "time_pattern": None, "date_pattern": None,
            "title_template": "{title}", "event_timezone": "US/Eastern",
            "program_duration": 180, "channel_group_ids": [5, 7],
            "substitution_pairs": [{"find": "HD", "replace": ""}],
        }

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_dummy_epg_profile", {"profile_id": 1})

        text = _text(result)
        assert "Sports Placeholder" in text
        assert "2 group(s) assigned" in text
        assert "1 configured" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["dummy_epg_get_profile"]


class TestCreateDummyEpgProfile:
    @pytest.mark.asyncio
    async def test_creates_with_defaults(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 10, "name": "News Placeholder"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_dummy_epg_profile", {"name": "News Placeholder"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["name"] == "News Placeholder"
        assert body["enabled"] is True
        assert body["name_source"] == "channel"
        assert body["stream_index"] == 1
        assert body["event_timezone"] == "US/Eastern"
        assert body["program_duration"] == 180
        assert body["tvg_id_template"] == "ecm-{channel_number}"
        for absent in ("title_pattern", "substitution_pairs", "pattern_variants", "channel_group_ids"):
            assert absent not in body, f"{absent} should be omitted when not given"
        assert "id=10" in _text(result)

    @pytest.mark.asyncio
    async def test_creates_with_optional_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 11, "name": "Sports"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            await mcp.call_tool("create_dummy_epg_profile", {
                "name": "Sports",
                "title_pattern": r"^(.*?) - ",
                "substitution_pairs": [{"find": "HD", "replace": ""}],
                "channel_group_ids": [5, 7],
            })

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["title_pattern"] == r"^(.*?) - "
        assert body["substitution_pairs"] == [{"find": "HD", "replace": ""}]
        assert body["channel_group_ids"] == [5, 7]


class TestUpdateDummyEpgProfile:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Sports"}

        with patch("tools.epg.get_ecm_client", return_value=client):
            await mcp.call_tool("update_dummy_epg_profile", {"profile_id": 1, "enabled": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_dummy_epg_profile", {"profile_id": 1})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteDummyEpgProfile:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 1, "name": "Sports", "channel_group_ids": [5, 7]}

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_dummy_epg_profile", {"profile_id": 1})

        text = _text(result)
        assert "Sports" in text
        assert "WARNING" in text
        assert "2 channel group" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["dummy_epg_get_profile"]

    @pytest.mark.asyncio
    async def test_preview_no_warning_when_unassigned(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 2, "name": "Unused", "channel_group_ids": []}

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_dummy_epg_profile", {"profile_id": 2})

        text = _text(result)
        assert "WARNING" not in text

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = None

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_dummy_epg_profile", {"profile_id": 1, "confirm": True})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["dummy_epg_delete_profile"]


class TestPreviewDummyEpg:
    @pytest.mark.asyncio
    async def test_matched_preview(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "original_name": "ESPN - Game Time 8:00PM",
            "substituted_name": "ESPN - Game Time 8:00PM",
            "matched": True,
            "matched_variant": None,
            "rendered": {"title": "Game Time", "description": "Live on ESPN"},
        }

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("preview_dummy_epg", {
                "sample_name": "ESPN - Game Time 8:00PM",
                "title_pattern": r"^.*? - (.*?) \d",
                "title_template": "{title}",
            })

        text = _text(result)
        assert "MATCHED" in text
        assert "Game Time" in text
        body = client.call_endpoint.call_args.kwargs["body"]
        assert body["sample_name"] == "ESPN - Game Time 8:00PM"
        assert body["title_pattern"] == r"^.*? - (.*?) \d"
        assert "substitution_pairs" not in body

    @pytest.mark.asyncio
    async def test_not_matched_shows_fallback(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "original_name": "Random Channel",
            "substituted_name": "Random Channel",
            "matched": False,
            "matched_variant": None,
            "rendered": {"title": "", "fallback_title": "Programming"},
        }

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("preview_dummy_epg", {"sample_name": "Random Channel"})

        text = _text(result)
        assert "NOT MATCHED" in text
        assert "Programming" in text
