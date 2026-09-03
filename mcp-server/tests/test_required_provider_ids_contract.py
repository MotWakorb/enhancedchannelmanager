from unittest.mock import AsyncMock, patch

import pytest

from ecm_client import ECMClient


def _mcp_and_client(verb: str, response: dict):
    from mcp.server.fastmcp import FastMCP
    from tools.channel_pipeline import register

    mcp = FastMCP("test")
    register(mcp)
    client = ECMClient()
    setattr(client, verb, AsyncMock(return_value=response))
    return mcp, client


@pytest.mark.asyncio
async def test_create_rule_forwards_required_provider_ids_through_contract_guard():
    mcp, client = _mcp_and_client("post", {"rule": {"id": 43}})

    with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
        result = await mcp.call_tool("create_channel_pipeline_rule", {
            "name": "Provider coverage",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "create_channel"}],
            "required_provider_ids": [11, 22],
        })

    assert "not in this endpoint's request_fields" not in str(result)
    assert client.post.call_args.kwargs["json_data"]["required_provider_ids"] == [11, 22]


@pytest.mark.asyncio
async def test_update_rule_forwards_required_provider_ids_through_contract_guard():
    mcp, client = _mcp_and_client("put", {"rule": {"id": 43}})

    with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
        result = await mcp.call_tool("update_channel_pipeline_rule", {
            "rule_id": 43,
            "required_provider_ids": [11, 22],
        })

    assert "not in this endpoint's request_fields" not in str(result)
    assert client.put.call_args.kwargs["json_data"]["required_provider_ids"] == [11, 22]
