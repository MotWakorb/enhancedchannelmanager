"""TDD tests for enhancedchannelmanager-dswrl — tags CRUD tools.

Covers list/create/update/delete_tag_group, add_tags_to_group, update_tag,
delete_tag, and test_tag_group. delete_tag_group cascades to all tags in the
group — confirm-gated with a tag-count preview, mirroring
delete_normalization_group's cascade preview.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.tags import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


class TestListTagGroups:
    @pytest.mark.asyncio
    async def test_lists_groups_with_counts(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "groups": [
                {"id": 1, "name": "Quality", "is_builtin": True, "tag_count": 5},
                {"id": 2, "name": "Custom", "is_builtin": False, "tag_count": 0},
            ]
        }

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_tag_groups", {})

        text = _text(result)
        assert "Quality" in text
        assert "5 tag" in text
        assert "builtin" in text.lower()

    @pytest.mark.asyncio
    async def test_no_groups(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"groups": []}

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_tag_groups", {})

        assert "No tag groups" in _text(result)


class TestCreateTagGroup:
    @pytest.mark.asyncio
    async def test_creates_group(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 3, "name": "Networks"}

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("create_tag_group", {"name": "Networks", "description": "TV networks"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "Networks", "description": "TV networks"}
        assert "id=3" in _text(result)

    @pytest.mark.asyncio
    async def test_omits_description_when_not_given(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 4, "name": "Bare"}

        with patch("tools.tags.get_ecm_client", return_value=client):
            await mcp.call_tool("create_tag_group", {"name": "Bare"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "Bare"}


class TestUpdateTagGroup:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"name": "Renamed"}

        with patch("tools.tags.get_ecm_client", return_value=client):
            await mcp.call_tool("update_tag_group", {"group_id": 2, "name": "Renamed"})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"name": "Renamed"}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_tag_group", {"group_id": 2})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteTagGroup:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_deletes_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 2, "name": "Custom", "is_builtin": False,
            "tags": [{"id": 10}, {"id": 11}, {"id": 12}],
        }

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_tag_group", {"group_id": 2})

        text = _text(result)
        assert "Custom" in text
        assert "3 tag" in text
        assert "confirm=True" in text
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["tags_get_group"]

    @pytest.mark.asyncio
    async def test_builtin_group_refused_before_confirm(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 1, "name": "Quality", "is_builtin": True, "tags": [{"id": 1}],
        }

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_tag_group", {"group_id": 1})

        text = _text(result)
        assert "built-in" in text.lower() or "cannot" in text.lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["tags_get_group"]

    @pytest.mark.asyncio
    async def test_confirm_true_deletes(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "deleted", "id": 2}

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_tag_group", {"group_id": 2, "confirm": True})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["tags_delete_group"]


class TestAddTagsToGroup:
    @pytest.mark.asyncio
    async def test_adds_tags(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"created": ["ESPN", "FOX"], "skipped": ["NBC"], "group_id": 2}

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("add_tags_to_group", {"group_id": 2, "tags": ["ESPN", "FOX", "NBC"]})

        text = _text(result)
        assert "2 created" in text or "created" in text.lower()
        assert "NBC" in text
        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"tags": ["ESPN", "FOX", "NBC"], "case_sensitive": False}


class TestUpdateTag:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"id": 10, "value": "ESPN"}

        with patch("tools.tags.get_ecm_client", return_value=client):
            await mcp.call_tool("update_tag", {"group_id": 2, "tag_id": 10, "enabled": False})

        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"enabled": False}

    @pytest.mark.asyncio
    async def test_no_changes_short_circuits(self):
        mcp = _mcp()
        client = AsyncMock()

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("update_tag", {"group_id": 2, "tag_id": 10})

        assert "No changes specified" in _text(result)
        client.call_endpoint.assert_not_called()


class TestDeleteTag:
    @pytest.mark.asyncio
    async def test_deletes_tag(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"status": "deleted", "id": 10}

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("delete_tag", {"group_id": 2, "tag_id": 10})

        assert "deleted" in _text(result).lower()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["tags_delete_tag"]


class TestTestTagGroup:
    @pytest.mark.asyncio
    async def test_matches_reported(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "text": "ESPN HD Feed",
            "group_id": 1,
            "group_name": "Quality",
            "matches": [{"tag_id": 5, "value": "HD", "case_sensitive": False}],
            "match_count": 1,
        }

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("test_tag_group", {"text": "ESPN HD Feed", "group_id": 1})

        text = _text(result)
        assert "1 match" in text
        assert "HD" in text

    @pytest.mark.asyncio
    async def test_no_matches(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "text": "Random", "group_id": 1, "group_name": "Quality",
            "matches": [], "match_count": 0,
        }

        with patch("tools.tags.get_ecm_client", return_value=client):
            result = await mcp.call_tool("test_tag_group", {"text": "Random", "group_id": 1})

        assert "No tags matched" in _text(result)
