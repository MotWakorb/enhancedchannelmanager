"""assign_channel_numbers forwards the AGENT's baselines and reads nothing itself.

``POST /api/channels/assign-numbers`` refuses to renumber a channel whose
number has moved since its caller last read it. The protection only means
something if "last read" is the agent's own read: a tool that fetched the
current numbers just before writing would always send matching baselines and
protect nothing. So the tool takes them as a required parameter and the only
backend call it makes is the write.
"""
import inspect

import pytest
from unittest.mock import AsyncMock, patch

from _endpoint_contracts import ENDPOINTS


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.channels import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


@pytest.mark.asyncio
async def test_the_agents_baselines_are_sent_with_the_write():
    mcp = _mcp()
    client = AsyncMock()
    client.call_endpoint.return_value = {"success": True}

    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("assign_channel_numbers", {
            "channel_ids": [1, 2],
            "starting_number": 100,
            "expected_numbers": {"1": 5, "2": None},
        })

    assert client.call_endpoint.await_count == 1
    endpoint = client.call_endpoint.await_args.args[0]
    assert endpoint is ENDPOINTS["channels_assign_numbers"]
    assert client.call_endpoint.await_args.kwargs["body"] == {
        "channel_ids": [1, 2],
        "starting_number": 100,
        "expected_numbers": {1: 5, 2: None},
    }


@pytest.mark.asyncio
async def test_the_tool_makes_no_read_of_its_own():
    mcp = _mcp()
    client = AsyncMock()
    client.call_endpoint.return_value = {"success": True}

    with patch("tools.channels.get_ecm_client", return_value=client):
        await mcp.call_tool("assign_channel_numbers", {
            "channel_ids": [1], "expected_numbers": {"1": 5},
        })

    called = [call.args[0].name for call in client.call_endpoint.await_args_list]
    assert called == ["channels_assign_numbers"]
    client.get.assert_not_called()


def test_expected_numbers_is_a_required_parameter():
    from tools import channels

    mcp = _mcp()
    schema = mcp._tool_manager._tools["assign_channel_numbers"].parameters
    assert "expected_numbers" in schema["required"]
    source = inspect.getsource(channels)
    assert "expected_numbers" in source


def test_the_description_tells_the_agent_to_read_first():
    mcp = _mcp()
    description = mcp._tool_manager._tools["assign_channel_numbers"].description
    assert "list_channels" in description
    assert "get_channel" in description
    assert "expected_numbers" in description


@pytest.mark.asyncio
async def test_a_refusal_is_reported_with_the_backend_detail():
    mcp = _mcp()
    client = AsyncMock()
    client.call_endpoint.side_effect = RuntimeError(
        "POST /api/channels/assign-numbers -> HTTP 409 Conflict: "
        "{'message': 'Channel was not renumbered: channel 2 is on channel number 6, "
        "and this request expected 99. Somebody else changed it.'}"
    )

    with patch("tools.channels.get_ecm_client", return_value=client):
        result = await mcp.call_tool("assign_channel_numbers", {
            "channel_ids": [2], "starting_number": 100, "expected_numbers": {"2": 99},
        })

    text = _text(result)
    assert "Somebody else changed it" in text
    assert "Nothing was renumbered" in text
    assert "re-read" in text.lower()


def test_the_endpoint_contract_names_the_new_field():
    assert "expected_numbers" in ENDPOINTS["channels_assign_numbers"].request_fields
