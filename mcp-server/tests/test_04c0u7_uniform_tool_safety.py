"""Registry-level safety contract for every ECM MCP tool (04c0u.7)."""

import json
import re
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from tools import register_all_tools
from tools._guardrails import derive_token, token_matches
from tools._safety_policy import (
    SAFETY_INVENTORY,
    ToolSafety,
    confirmation_token,
    install_safety_policy,
)


def _registry() -> FastMCP:
    mcp = FastMCP("safety-test")
    register_all_tools(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


def _token(text: str) -> str:
    match = re.search(r"confirmation_token: ([^\s]+)", text)
    assert match, text
    return match.group(1)


def test_live_registry_is_completely_and_only_inventoried():
    mcp = _registry()
    assert set(SAFETY_INVENTORY) == set(mcp._tool_manager._tools)
    assert all(isinstance(value, ToolSafety) for value in SAFETY_INVENTORY.values())


def test_every_live_tool_has_explicit_mcp_annotations():
    mcp = _registry()
    for name, tool in mcp._tool_manager._tools.items():
        classification = SAFETY_INVENTORY[name]
        assert tool.annotations is not None, name
        assert tool.annotations.readOnlyHint is (classification is ToolSafety.READ_ONLY), name
        assert tool.annotations.destructiveHint is (classification is ToolSafety.DESTRUCTIVE), name


def test_registry_fails_closed_for_unclassified_tool():
    mcp = FastMCP("mutant")

    @mcp.tool()
    async def surprise_delete() -> str:
        return "deleted"

    with pytest.raises(RuntimeError, match="unclassified.*surprise_delete"):
        install_safety_policy(mcp)


def test_registry_fails_closed_for_one_call_destructive_mutant():
    mcp = FastMCP("mutant")

    @mcp.tool()
    async def delete_saved_backup(filename: str) -> str:
        return f"deleted {filename}"

    with patch.dict(SAFETY_INVENTORY, {"delete_saved_backup": ToolSafety.DESTRUCTIVE}, clear=True):
        install_safety_policy(mcp)
        tool = mcp._tool_manager._tools["delete_saved_backup"]
        assert "confirmation_token" in tool.parameters["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "tool_name", "arguments"),
    [
        ("system", "delete_saved_backup", {"filename": "backup.yaml"}),
        ("epg", "delete_epg_source", {"source_id": 7}),
        ("tasks", "delete_task_schedule", {"task_id": "refresh", "schedule_id": 3}),
        ("tags", "delete_tag", {"tag_id": 9}),
        ("streams", "cleanup_struck_out_streams", {"delete_empty_channels": True}),
    ],
)
async def test_first_destructive_call_is_a_mutation_free_preview(module, tool_name, arguments):
    mcp = _registry()
    client = AsyncMock()
    with patch(f"tools.{module}.get_ecm_client", return_value=client):
        result = await mcp.call_tool(tool_name, arguments)
    assert "PREVIEW" in _text(result)
    assert "confirmation_token:" in _text(result)
    client.call_endpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_is_content_bound_and_drift_invalidates_it():
    mcp = _registry()
    preview = await mcp.call_tool("delete_saved_backup", {"filename": "a.yaml"})
    token = _token(_text(preview))
    client = AsyncMock()
    with patch("tools.system.get_ecm_client", return_value=client):
        result = await mcp.call_tool(
            "delete_saved_backup",
            {"filename": "b.yaml", "confirmation_token": token},
        )
    assert "confirmation does not match" in _text(result).lower()
    client.call_endpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_confirmation_cannot_mutate():
    mcp = _registry()
    token = confirmation_token("delete_saved_backup", {"filename": "a.yaml"}, issued_at=1)
    client = AsyncMock()
    with patch("tools._safety_policy.time.time", return_value=10_000), patch(
        "tools.system.get_ecm_client", return_value=client
    ):
        result = await mcp.call_tool(
            "delete_saved_backup",
            {"filename": "a.yaml", "confirmation_token": token},
        )
    assert "expired" in _text(result).lower()
    client.call_endpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_uniform_confirmation_executes_exactly_once():
    mcp = _registry()
    preview = await mcp.call_tool("delete_saved_backup", {"filename": "a.yaml"})
    token = _token(_text(preview))
    client = AsyncMock()
    with patch("tools.system.get_ecm_client", return_value=client):
        await mcp.call_tool(
            "delete_saved_backup",
            {"filename": "a.yaml", "confirmation_token": token},
        )
    mutation_calls = [
        call for call in client.call_endpoint.await_args_list
        if getattr(call.args[0], "method", "GET").upper() != "GET"
    ]
    assert len(mutation_calls) == 1


@pytest.mark.asyncio
async def test_uniform_destructive_batch_cap_refuses_without_entering_tool():
    mcp = _registry()
    client = AsyncMock()
    with patch("tools.channels.get_ecm_client", return_value=client):
        result = await mcp.call_tool(
            "bulk_add_streams_to_channel",
            {"channel_id": 1, "stream_ids": list(range(501))},
        )
    assert "hard cap is 500" in _text(result)
    client.call_endpoint.assert_not_awaited()


def test_resolved_target_token_expires_and_rejects_drift():
    with patch("tools._guardrails.time.time", return_value=100):
        token = derive_token([3, 1, 2])
    with patch("tools._guardrails.time.time", return_value=399):
        assert token_matches(token, [1, 2, 3])
        assert not token_matches(token, [1, 2, 4])
    with patch("tools._guardrails.time.time", return_value=401):
        assert not token_matches(token, [1, 2, 3])
