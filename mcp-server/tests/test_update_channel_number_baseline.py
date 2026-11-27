"""update_channel forwards the AGENT's baseline whenever it sets a number.

``PATCH /api/channels/{id}`` refuses a ``channel_number`` write that carries no
``expectedNumber``, and refuses with 409 a write whose channel has moved since
its caller read it. As with ``assign_channel_numbers``, the protection only
means something if "read" is the agent's own read, so the tool takes the
baseline as a parameter and never fetches it to fill the field.
"""
import re

import pytest
from unittest.mock import AsyncMock, patch

from _endpoint_contracts import ENDPOINTS


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.channels import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _registry():
    from mcp.server.fastmcp import FastMCP
    from tools import register_all_tools

    mcp = FastMCP("safety-test")
    register_all_tools(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


def _client(response=None):
    client = AsyncMock()
    client.call_endpoint.return_value = response or {
        "id": 1, "name": "ESPN", "channel_number": 20, "channel_group_id": 3,
    }
    return client


def _update_bodies(client) -> list[dict]:
    return [
        call.kwargs["body"] for call in client.call_endpoint.await_args_list
        if call.args[0] is ENDPOINTS["channels_update"]
    ]


@pytest.mark.asyncio
async def test_the_agents_baseline_is_sent_with_the_number():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("update_channel", {
            "channel_id": 1, "channel_number": 20, "expected_number": {"number": 5},
        })
    assert _update_bodies(client) == [
        {"channel_number": 20, "expectedNumber": {"number": 5}},
    ]


@pytest.mark.asyncio
async def test_a_null_baseline_says_the_channel_had_no_number():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("update_channel", {
            "channel_id": 1, "channel_number": 20, "expected_number": {"number": None},
        })
    assert _update_bodies(client) == [
        {"channel_number": 20, "expectedNumber": {"number": None}},
    ]


@pytest.mark.asyncio
async def test_an_off_grid_baseline_reaches_the_backend_unrounded():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("update_channel", {
            "channel_id": 1, "channel_number": 20, "expected_number": {"number": 8.25},
        })
    assert _update_bodies(client)[0]["expectedNumber"] == {"number": 8.25}


@pytest.mark.asyncio
async def test_a_number_change_without_a_baseline_is_refused_with_no_call():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        result = await mcp.call_tool("update_channel", {"channel_id": 1, "channel_number": 20})
    text = _text(result)
    assert "expected_number" in text
    assert "get_channel" in text
    client.call_endpoint.assert_not_called()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_the_tool_makes_no_read_of_its_own_for_a_number_change():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("update_channel", {
            "channel_id": 1, "channel_number": 20, "expected_number": {"number": 5},
        })
    called = [call.args[0].name for call in client.call_endpoint.await_args_list]
    assert called == ["channels_update"]
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_a_baseline_without_a_number_change_is_not_sent():
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("update_channel", {
            "channel_id": 1, "tvg_id": "espn.us", "expected_number": {"number": 5},
        })
    assert _update_bodies(client) == [{"tvg_id": "espn.us"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [5, {}, {"number": True}, {"number": 5, "extra": 1}])
async def test_a_malformed_baseline_is_refused_before_any_call(bad):
    mcp = _mcp()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        with pytest.raises(Exception):
            await mcp.call_tool("update_channel", {
                "channel_id": 1, "channel_number": 20, "expected_number": bad,
            })
    client.call_endpoint.assert_not_called()


@pytest.mark.asyncio
async def test_a_409_tells_the_agent_to_read_again():
    mcp = _mcp()
    client = AsyncMock()
    client.call_endpoint.side_effect = RuntimeError(
        "PATCH /api/channels/1 -> HTTP 409 Conflict: "
        "{'message': 'Channel was not renumbered: channel 1 is on channel number 6, "
        "and this request expected 5. Somebody else changed it.'}"
    )
    with patch("tools.channels.get_ecm_client", return_value=client):
        result = await mcp.call_tool("update_channel", {
            "channel_id": 1, "channel_number": 20, "expected_number": {"number": 5},
        })
    text = _text(result)
    assert "Somebody else changed it" in text
    assert "was not updated" in text
    assert "re-read" in text.lower()


def test_the_description_tells_the_agent_to_read_first():
    mcp = _mcp()
    tool = mcp._tool_manager._tools["update_channel"]
    assert "expected_number" in tool.description
    assert "get_channel" in tool.description
    assert "expected_number" in tool.parameters["properties"]


def test_the_endpoint_contract_names_the_field():
    assert "expectedNumber" in ENDPOINTS["channels_update"].request_fields


@pytest.mark.asyncio
async def test_the_baseline_survives_the_confirmation_flow():
    # update_channel is gated by the uniform confirmation token; the baseline
    # the agent sent must still be the one on the write after confirming.
    mcp = _registry()
    current = {"id": 1, "name": "ESPN", "channel_number": 5, "auto_created": True}
    client = AsyncMock()
    # Preview read, confirm-time re-resolve, the tool's own drift check, write.
    client.call_endpoint.side_effect = [
        current, current, current,
        {"id": 1, "name": "ESPN", "channel_number": 20, "channel_group_id": 3},
    ]
    arguments = {"channel_id": 1, "channel_number": 20, "expected_number": {"number": 5}}
    with patch("tools.channels.get_ecm_client", return_value=client):
        preview = await mcp.call_tool("update_channel", arguments)
        token = re.search(r"confirmation_token: ([^\s]+)", _text(preview)).group(1)
        await mcp.call_tool("update_channel", {**arguments, "confirmation_token": token})
    assert _update_bodies(client) == [
        {"channel_number": 20, "expectedNumber": {"number": 5}},
    ]


@pytest.mark.asyncio
async def test_a_missing_baseline_is_refused_before_a_token_is_issued():
    # Through the safety wrapper: no preview read, and no confirmation token
    # for a call that could never be confirmed.
    mcp = _registry()
    client = _client()
    with patch("tools.channels.get_ecm_client", return_value=client):
        result = await mcp.call_tool("update_channel", {"channel_id": 1, "channel_number": 20})
    text = _text(result)
    assert "expected_number" in text
    assert "confirmation_token" not in text
    client.call_endpoint.assert_not_called()
