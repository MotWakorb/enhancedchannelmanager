"""bd-p8fx9 (W4) — settings-driven batch-size caps on bulk MCP tools.

Two-level caps that share W2's confirm-token mechanism:

* SOFT cap → force the Tier-B token even on the first call.
* HARD cap → REFUSE outright (never silent truncation), naming the limit.

Caps are read from the backend GET /api/settings response with conservative
defaults. Tests assert behaviour at/around each threshold and that the refusal
message names the limit.
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP

from tools._guardrails import (
    DEFAULT_BULK_DELETE_HARD_CAP,
    DEFAULT_BULK_DELETE_SOFT_CAP,
    DEFAULT_BULK_MERGE_HARD_CAP,
    SETTING_BULK_DELETE_HARD_CAP,
    SETTING_BULK_DELETE_SOFT_CAP,
    derive_token,
)


def _register_channels() -> FastMCP:
    mcp = FastMCP("test")
    from tools.channels import register

    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


def _mutation_calls(mock_client) -> list:
    calls = []
    for c in mock_client.call_endpoint.call_args_list:
        ep = c.args[0]
        if getattr(ep, "method", "GET").upper() != "GET":
            calls.append(c)
    return calls


def _settings(**overrides) -> dict:
    base = {}
    base.update(overrides)
    return base


# ===========================================================================
# bulk_delete_channels caps
# ===========================================================================
class TestBulkDeleteCaps:
    @pytest.mark.asyncio
    async def test_under_soft_cap_with_token_proceeds(self):
        """A small batch (below soft cap) still needs the token (Tier B), and
        proceeds once the correct token is supplied — no cap friction added."""
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = None
        ids = list(range(1, 6))  # 5 << soft cap 25
        token = derive_token(ids)
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "bulk_delete_channels", {"channel_ids": ids, "confirm_token": token}
            )
        assert len(_mutation_calls(mock_client)) == 5

    @pytest.mark.asyncio
    async def test_at_soft_cap_requires_token(self):
        """At exactly the soft cap the token is required (preview, no mutation)."""
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _settings()  # settings read → defaults
        ids = list(range(1, DEFAULT_BULK_DELETE_SOFT_CAP + 1))  # == soft cap
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("bulk_delete_channels", {"channel_ids": ids})
        assert _mutation_calls(mock_client) == []
        assert derive_token(ids) in _text(result)

    @pytest.mark.asyncio
    async def test_between_soft_and_hard_requires_token(self):
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _settings()
        ids = list(range(1, 101))  # 100: between 25 and 500
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("bulk_delete_channels", {"channel_ids": ids})
        assert _mutation_calls(mock_client) == []
        assert derive_token(ids) in _text(result)

    @pytest.mark.asyncio
    async def test_over_hard_cap_refused_even_with_token(self):
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _settings()
        ids = list(range(1, DEFAULT_BULK_DELETE_HARD_CAP + 50))  # over hard cap
        token = derive_token(ids)
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_delete_channels", {"channel_ids": ids, "confirm_token": token}
            )
        text = _text(result)
        assert _mutation_calls(mock_client) == []
        assert str(DEFAULT_BULK_DELETE_HARD_CAP) in text  # names the limit

    @pytest.mark.asyncio
    async def test_caps_read_from_settings(self):
        """A lowered hard cap from settings is honoured: a batch that would pass
        the default cap is refused under the tighter setting."""
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _settings(
            **{SETTING_BULK_DELETE_SOFT_CAP: 2, SETTING_BULK_DELETE_HARD_CAP: 10}
        )
        ids = list(range(1, 21))  # 20: over the lowered hard cap of 10
        token = derive_token(ids)
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_delete_channels", {"channel_ids": ids, "confirm_token": token}
            )
        text = _text(result)
        assert _mutation_calls(mock_client) == []
        assert "10" in text  # the configured hard cap


# ===========================================================================
# clear_auto_created — all_groups always token-gated
# ===========================================================================
class TestClearAutoCreatedCaps:
    def _page(self, channels):
        return {"results": channels, "next": None, "count": len(channels)}

    @pytest.mark.asyncio
    async def test_all_groups_always_gated_regardless_of_count(self):
        mcp = _register_channels()
        mock_client = AsyncMock()
        # Even a single auto channel → token required (always-gated path).
        mock_client.call_endpoint.return_value = self._page([
            {"id": 1, "name": "Solo", "auto_created": True, "channel_group_id": 1},
        ])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"all_groups": True})
        assert _mutation_calls(mock_client) == []
        assert derive_token([1]) in _text(result)


# ===========================================================================
# bulk_merge_duplicate_channels caps (counted in merge OPS)
# ===========================================================================
class TestBulkMergeCaps:
    @pytest.mark.asyncio
    async def test_over_hard_cap_refused(self):
        mcp = _register_channels()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = _settings()
        # More merge ops than the hard cap (200).
        merges = [
            {"target_channel_id": i, "source_channel_ids": [i + 1000]}
            for i in range(DEFAULT_BULK_MERGE_HARD_CAP + 5)
        ]
        all_sources = [m["source_channel_ids"][0] for m in merges]
        token = derive_token(all_sources)
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_merge_duplicate_channels",
                {"merges": merges, "confirm_token": token},
            )
        text = _text(result)
        assert _mutation_calls(mock_client) == []
        assert str(DEFAULT_BULK_MERGE_HARD_CAP) in text
