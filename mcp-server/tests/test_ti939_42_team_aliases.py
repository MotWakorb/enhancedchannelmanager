"""Event Sync team-alias dictionary MCP tools (bead enhancedchannelmanager-ti939.4.2).

Pins:
* get/update route through call_endpoint with the es_*_team_aliases
  contract entries (the contract-checked path);
* update is a FULL-REPLACE write with the exact {"groups": [...]} body the
  backend PUT expects — no partial-merge client logic;
* both tools degrade to an "Error ..." string on client failure (never
  raise into the MCP transport).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.event_sync_aliases import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


_GROUPS = [
    {"terms": ["Red Devils", "Manchester United", "MUFC"], "note": "corpus 2026-07-18"},
    {"terms": ["Spurs", "Tottenham Hotspur"], "note": None},
]


class TestGetTeamAliases:
    @pytest.mark.asyncio
    async def test_empty_dictionary_reports_shipped_default(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"groups": []}

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_event_sync_team_aliases", {})

        endpoint = client.call_endpoint.call_args.args[0]
        assert endpoint.name == "es_get_team_aliases"
        assert "empty" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_lists_groups_with_terms_and_notes(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"groups": _GROUPS}

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_event_sync_team_aliases", {})

        text = _text(result)
        assert "2 group(s)" in text
        assert "Red Devils == Manchester United == MUFC" in text
        assert "corpus 2026-07-18" in text
        assert "Spurs == Tottenham Hotspur" in text

    @pytest.mark.asyncio
    async def test_client_failure_returns_error_string(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError("backend down")

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_event_sync_team_aliases", {})

        assert "Error getting team aliases" in _text(result)


class TestUpdateTeamAliases:
    @pytest.mark.asyncio
    async def test_sends_full_replace_body_through_contract(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"groups": _GROUPS}

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "update_event_sync_team_aliases", {"groups": _GROUPS}
            )

        endpoint = client.call_endpoint.call_args.args[0]
        assert endpoint.name == "es_update_team_aliases"
        assert client.call_endpoint.call_args.kwargs["body"] == {"groups": _GROUPS}
        text = _text(result)
        assert "2 group(s)" in text
        assert "5 term(s)" in text

    @pytest.mark.asyncio
    async def test_clearing_reports_zero_groups(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"groups": []}

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "update_event_sync_team_aliases", {"groups": []}
            )

        assert "cleared" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_backend_validation_error_surfaces_as_error_string(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError(
            "400: Alias group 1 needs at least 2 terms"
        )

        with patch("tools.event_sync_aliases.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "update_event_sync_team_aliases",
                {"groups": [{"terms": ["Manchester United"]}]},
            )

        text = _text(result)
        assert "Error updating team aliases" in text
        assert "at least 2 terms" in text
