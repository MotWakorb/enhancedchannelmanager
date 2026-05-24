"""TDD tests for bd-0emgo.1 — merge_streams loose_name_match pass-through (MCP).

The ``loose_name_match`` flag is a per-action param that lives inside a
merge_streams action dict. create_auto_creation_rule / update_auto_creation_rule
forward the ``actions`` list verbatim to the backend, so the flag rides through
untouched (like find_channel_by / remove_non_matching). These tests pin that
behavior so a future refactor that strips/filters action params is caught.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mock(return_value=None):
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value if return_value is not None else {}
    return mock


def _register_and_get_mcp():
    from tools.auto_creation import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _sent_payload(mock_client):
    """Return the body dict passed to the (single) call_endpoint invocation."""
    call = mock_client.call_endpoint.call_args
    return call.kwargs.get("body")


class TestLooseNameMatchPassthroughCreate:
    """create_auto_creation_rule forwards loose_name_match inside the action."""

    @pytest.mark.asyncio
    async def test_loose_name_match_true_survives_to_payload(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 7}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "loose_name_match": True,
        }

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "Sky Sport merge (loose)",
                    "conditions": [{"type": "always"}],
                    "actions": [action],
                },
            )

        payload = _sent_payload(mock_client)
        assert payload is not None
        sent_action = payload["actions"][0]
        assert sent_action["type"] == "merge_streams"
        assert sent_action["loose_name_match"] is True

    @pytest.mark.asyncio
    async def test_loose_name_match_false_survives_to_payload(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 8}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "loose_name_match": False,
        }

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "Sky Sport merge (exact)",
                    "conditions": [{"type": "always"}],
                    "actions": [action],
                },
            )

        sent_action = _sent_payload(mock_client)["actions"][0]
        assert sent_action["loose_name_match"] is False


class TestLooseNameMatchPassthroughUpdate:
    """update_auto_creation_rule forwards loose_name_match inside the action."""

    @pytest.mark.asyncio
    async def test_update_forwards_loose_name_match(self):
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"rule": {"id": 9}})
        action = {
            "type": "merge_streams",
            "target": "auto",
            "loose_name_match": True,
        }

        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_auto_creation_rule",
                {"rule_id": 9, "actions": [action]},
            )

        payload = _sent_payload(mock_client)
        assert payload is not None
        sent_action = payload["actions"][0]
        assert sent_action["loose_name_match"] is True


class TestLooseNameMatchContract:
    """The flag rides inside the top-level ``actions`` field, which IS in the
    create/update contract — no per-action contract entry is required."""

    def test_actions_is_in_create_contract(self):
        from _endpoint_contracts import ENDPOINTS

        assert "actions" in ENDPOINTS["ac_create_rule"].request_fields
        assert "actions" in ENDPOINTS["ac_update_rule"].request_fields
