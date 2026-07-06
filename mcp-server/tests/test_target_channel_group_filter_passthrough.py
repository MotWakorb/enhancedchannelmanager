"""TDD tests for bd-0emgo.3 — merge_streams target-channel group filter (MCP).

``target_channel_not_in_group`` / ``target_channel_in_group`` are per-action
params that live inside a merge_streams action dict. create_auto_creation_rule /
update_auto_creation_rule forward the ``actions`` list verbatim to the backend,
so these lists ride through untouched (like loose_name_match / find_channel_by).
These tests pin that behavior so a future refactor that strips/filters action
params is caught.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mock(return_value=None):
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value if return_value is not None else {}
    return mock


def _register_and_get_mcp():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _sent_payload(mock_client):
    """Return the body dict passed to the (single) call_endpoint invocation."""
    call = mock_client.call_endpoint.call_args
    return call.kwargs.get("body")


class TestTargetChannelGroupFilterCreate:
    """create_auto_creation_rule forwards the group filter inside the action."""

    @pytest.mark.asyncio
    async def test_not_in_group_survives_to_payload(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 11}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "target_channel_not_in_group": [567],
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "Keep merges out of 567",
                    "conditions": [{"type": "always"}],
                    "actions": [action],
                },
            )

        payload = _sent_payload(mock_client)
        assert payload is not None
        sent_action = payload["actions"][0]
        assert sent_action["type"] == "merge_streams"
        assert sent_action["target_channel_not_in_group"] == [567]

    @pytest.mark.asyncio
    async def test_in_group_survives_to_payload(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 12}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "target_channel_in_group": [42, 43],
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "Only merge into 42/43",
                    "conditions": [{"type": "always"}],
                    "actions": [action],
                },
            )

        sent_action = _sent_payload(mock_client)["actions"][0]
        assert sent_action["target_channel_in_group"] == [42, 43]


class TestTargetChannelGroupFilterUpdate:
    """update_auto_creation_rule forwards the group filter inside the action."""

    @pytest.mark.asyncio
    async def test_update_forwards_not_in_group(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 13}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "target_channel_not_in_group": [567],
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_auto_creation_rule",
                {"rule_id": 13, "actions": [action]},
            )

        payload = _sent_payload(mock_client)
        assert payload is not None
        sent_action = payload["actions"][0]
        assert sent_action["target_channel_not_in_group"] == [567]


class TestTargetChannelGroupFilterContract:
    """The filter rides inside the top-level ``actions`` field, which IS in the
    create/update contract — no per-action contract entry is required."""

    def test_actions_is_in_create_contract(self):
        from _endpoint_contracts import ENDPOINTS

        assert "actions" in ENDPOINTS["ac_create_rule"].request_fields
        assert "actions" in ENDPOINTS["ac_update_rule"].request_fields
