"""MCP discovery and call-result contract for flag clearing (hbjuu).

Synthetic endpoint responses only. The backend route contract is exercised in
backend/tests/routers/test_hbjuu_clear_flags_contract.py.
"""

import re
from unittest.mock import AsyncMock, patch

import pytest

from _endpoint_contracts import ENDPOINTS
from server import mcp
from tools import _guardrails


def _text(result):
    return result[0][0].text


def _token(result):
    match = re.search(r"confirm_token='([^']+)'", _text(result))
    assert match, _text(result)
    return match.group(1)


@pytest.mark.asyncio
async def test_discovery_describes_flags_and_keeps_destructive_confirmation_tier():
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "clear_auto_created")
    description = " ".join(tool.description.split())
    assert "auto_created=False" in description
    assert "auto_created_by=None" in description
    assert "does not delete channels" in description
    assert "first 10 channel IDs" in description
    assert "requires explicit group_ids" in description
    assert "would be deleted" not in description
    assert tool.annotations.destructiveHint is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [{"group_ids": [5]}, {"all_groups": True}])
async def test_preview_and_stale_preview_name_flags_without_mutation(scope):
    upstream = AsyncMock()
    upstream.call_endpoint.return_value = {
        "results": [{"id": 17, "auto_created": True, "channel_group_id": 5}],
        "next": None,
    }
    with patch("tools.channels.get_ecm_client", return_value=upstream):
        preview = await mcp.call_tool("clear_auto_created", scope)
        upstream.call_endpoint.return_value["results"].append(
            {"id": 18, "auto_created": True, "channel_group_id": 5}
        )
        stale = await mcp.call_tool("clear_auto_created", {**scope, "confirm_token": _token(preview)})

    for result in (preview, stale):
        assert "clear auto-created flags" in _text(result)
        assert "No channels will be deleted" in _text(result)
        if scope.get("all_groups"):
            assert "requires explicit group_ids" in _text(result)
    assert "did not match" in _text(stale)
    assert all(call.args[0] is ENDPOINTS["channels_list"] for call in upstream.call_endpoint.await_args_list)


@pytest.mark.asyncio
async def test_confirmed_group_call_reports_updates_and_replay_cannot_mutate():
    upstream = AsyncMock()

    async def respond(endpoint, **kwargs):
        if endpoint is ENDPOINTS["channels_list"]:
            return {"results": [{"id": 17, "auto_created": True, "channel_group_id": 5}], "next": None}
        return {"updated_count": 1, "failed_channels": []}

    upstream.call_endpoint.side_effect = respond
    with patch("tools.channels.get_ecm_client", return_value=upstream):
        preview = await mcp.call_tool("clear_auto_created", {"group_ids": [5]})
        args = {"group_ids": [5], "confirm_token": _token(preview)}
        result = await mcp.call_tool("clear_auto_created", args)
        replay = await mcp.call_tool("clear_auto_created", args)

    assert _text(result) == "Cleared auto-created flags from 1 channel(s) in 1 group. No channels were deleted."
    mutations = [call for call in upstream.call_endpoint.await_args_list if call.args[0].method != "GET"]
    assert len(mutations) == 1
    assert mutations[0].args == (ENDPOINTS["channels_clear_auto_created"],)
    assert mutations[0].kwargs == {"body": {"group_ids": [5]}}
    assert "did not match" in _text(replay)
    assert "already used" in _text(replay)


@pytest.mark.asyncio
async def test_expired_flag_clear_confirmation_does_not_mutate():
    upstream = AsyncMock()
    upstream.call_endpoint.return_value = {
        "results": [{"id": 17, "auto_created": True, "channel_group_id": 5}],
        "next": None,
    }
    with patch("tools.channels.get_ecm_client", return_value=upstream):
        with patch.object(_guardrails.time, "time", return_value=1_800_000_000):
            preview = await mcp.call_tool("clear_auto_created", {"group_ids": [5]})
        with patch.object(_guardrails.time, "time", return_value=1_800_000_301):
            result = await mcp.call_tool("clear_auto_created", {"group_ids": [5], "confirm_token": _token(preview)})

    assert "did not match" in _text(result)
    assert "expired" in _text(result)
    assert all(call.args[0].method == "GET" for call in upstream.call_endpoint.await_args_list)
