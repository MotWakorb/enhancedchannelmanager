"""bd-onazy (W2) — dry-run / confirm gating on destructive MCP tools.

These tools previously mutated on the FIRST call with no preview. This module
proves the two-call contract:

Tier A (single entity, ``confirm: bool``): delete_channel, delete_m3u_account,
delete_auto_creation_rule, and update_channel (gated ONLY on rename / group-change
of a manual channel).

Tier B (bulk / fan-out, content-derived ``confirm_token``): bulk_delete_channels,
clear_auto_created, bulk_merge_duplicate_channels.

Test patterns mirror tests/test_0hjrk_*.py: FastMCP register + AsyncMock
ecm_client patched at the tool module's ``get_ecm_client``. We assert backend
*mutation* call counts (not just output strings) so a tool that previews but
still mutates is caught.
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP

from tools._guardrails import derive_token


def _register(module_name: str) -> FastMCP:
    mcp = FastMCP("test")
    if module_name == "channels":
        from tools.channels import register
    elif module_name == "m3u":
        from tools.m3u import register
    elif module_name == "auto_creation":
        from tools.auto_creation import register
    else:  # pragma: no cover
        raise ValueError(module_name)
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


def _mutation_calls(mock_client) -> list:
    """call_endpoint invocations whose endpoint is a write verb (mutations).

    GET reads (fetching the preview target) are excluded so we can assert that a
    preview-only call performs ZERO mutations.
    """
    calls = []
    for c in mock_client.call_endpoint.call_args_list:
        ep = c.args[0]
        if getattr(ep, "method", "GET").upper() != "GET":
            calls.append(c)
    return calls


# ===========================================================================
# Tier A — delete_channel
# ===========================================================================
class TestDeleteChannelConfirm:
    @pytest.mark.asyncio
    async def test_default_previews_and_does_not_delete(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 412, "name": "ESPN HD", "channel_group_id": 7,
            "channel_number": 412, "streams": [1, 2, 3], "auto_created": False,
        }
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_channel", {"channel_id": 412})
        text = _text(result)
        assert "ESPN HD" in text  # names the entity
        assert "412" in text
        assert "confirm" in text.lower()
        assert _mutation_calls(mock_client) == []  # nothing deleted

    @pytest.mark.asyncio
    async def test_confirm_true_deletes_once(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = None
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "delete_channel", {"channel_id": 412, "confirm": True}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_delete"
        assert "deleted" in _text(result).lower()


# ===========================================================================
# Tier A — delete_m3u_account
# ===========================================================================
class TestDeleteM3UConfirm:
    @pytest.mark.asyncio
    async def test_default_previews(self):
        mcp = _register("m3u")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 5, "name": "Provider X", "url": "http://x/list.m3u",
        }
        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_m3u_account", {"account_id": 5})
        text = _text(result)
        assert "Provider X" in text
        assert "confirm" in text.lower()
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_confirm_deletes(self):
        mcp = _register("m3u")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = None
        with patch("tools.m3u.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "delete_m3u_account", {"account_id": 5, "confirm": True}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "m3u_delete_account"


# ===========================================================================
# Tier A — delete_auto_creation_rule
# ===========================================================================
class TestDeleteRuleConfirm:
    @pytest.mark.asyncio
    async def test_default_previews(self):
        mcp = _register("auto_creation")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {"id": 9, "name": "Sports Rule"}
        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("delete_auto_creation_rule", {"rule_id": 9})
        text = _text(result)
        assert "Sports Rule" in text
        assert "confirm" in text.lower()
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_confirm_deletes(self):
        mcp = _register("auto_creation")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = None
        with patch("tools.auto_creation.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "delete_auto_creation_rule", {"rule_id": 9, "confirm": True}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "ac_delete_rule"


# ===========================================================================
# Tier A — update_channel (gated only on rename/group-change of MANUAL channel)
# ===========================================================================
class TestUpdateChannelConfirm:
    @pytest.mark.asyncio
    async def test_rename_manual_channel_is_gated(self):
        """Renaming a manual (auto_created=False) channel previews, no mutation."""
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 50, "name": "Old Name", "channel_group_id": 3,
            "channel_number": 50, "streams": [], "auto_created": False,
        }
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_channel", {"channel_id": 50, "name": "New Name"}
            )
        text = _text(result)
        assert "confirm" in text.lower()
        assert "Old Name" in text or "New Name" in text
        assert _mutation_calls(mock_client) == []  # not patched yet

    @pytest.mark.asyncio
    async def test_group_change_manual_channel_is_gated(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 50, "name": "Keep Name", "channel_group_id": 3,
            "channel_number": 50, "streams": [], "auto_created": False,
        }
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_channel", {"channel_id": 50, "group_id": 9}
            )
        assert "confirm" in _text(result).lower()
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_rename_manual_with_confirm_proceeds(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        # First GET (state fetch) then PATCH (the update).
        mock_client.call_endpoint.side_effect = [
            {"id": 50, "name": "Old Name", "channel_group_id": 3,
             "channel_number": 50, "streams": [], "auto_created": False},
            {"id": 50, "name": "New Name", "channel_group_id": 3, "channel_number": 50},
        ]
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_channel",
                {"channel_id": 50, "name": "New Name", "confirm": True},
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_update"

    @pytest.mark.asyncio
    async def test_benign_edit_on_auto_channel_not_gated(self):
        """A cheap field edit (channel_number) on an AUTO channel stays
        frictionless — patches immediately, no preview required."""
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 60, "name": "Auto Ch", "channel_group_id": 3, "channel_number": 99,
        }
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_channel", {"channel_id": 60, "channel_number": 99}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_update"

    @pytest.mark.asyncio
    async def test_rename_auto_channel_not_gated(self):
        """Renaming an AUTO-created channel is frictionless — only MANUAL
        channels get the rename/group gate."""
        mcp = _register("channels")
        mock_client = AsyncMock()
        # GET state (auto_created=True) then PATCH.
        mock_client.call_endpoint.side_effect = [
            {"id": 70, "name": "Auto Old", "channel_group_id": 3,
             "channel_number": 70, "streams": [], "auto_created": True},
            {"id": 70, "name": "Auto New", "channel_group_id": 3, "channel_number": 70},
        ]
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "update_channel", {"channel_id": 70, "name": "Auto New"}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_update"


# ===========================================================================
# Tier B — bulk_delete_channels (content-derived token)
# ===========================================================================
class TestBulkDeleteToken:
    @pytest.mark.asyncio
    async def test_no_token_previews_with_token_and_no_mutation(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_delete_channels", {"channel_ids": [1, 2, 3]}
            )
        text = _text(result)
        token = derive_token([1, 2, 3])
        assert token in text
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_correct_token_deletes_set(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = None
        token = derive_token([1, 2, 3])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "bulk_delete_channels",
                {"channel_ids": [1, 2, 3], "confirm_token": token},
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 3  # one delete per id
        assert all(m.args[0].name == "channels_delete" for m in muts)

    @pytest.mark.asyncio
    async def test_stale_token_refused_zero_mutation(self):
        """A token computed for a DIFFERENT set (set drifted) is refused."""
        mcp = _register("channels")
        mock_client = AsyncMock()
        stale = derive_token([1, 2, 3, 99])  # token for a different set
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_delete_channels",
                {"channel_ids": [1, 2, 3], "confirm_token": stale},
            )
        text = _text(result)
        assert _mutation_calls(mock_client) == []
        # Refusal surfaces the fresh token for re-confirmation.
        assert derive_token([1, 2, 3]) in text


# ===========================================================================
# Tier B — clear_auto_created
# ===========================================================================
class TestClearAutoCreatedToken:
    def _channels_page(self, channels, nxt=None):
        return {"results": channels, "next": nxt, "count": len(channels)}

    @pytest.mark.asyncio
    async def test_all_groups_no_token_previews_over_resolved_ids(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        # Enumerate auto-created channels across all groups (single page).
        mock_client.call_endpoint.return_value = self._channels_page([
            {"id": 11, "name": "A", "auto_created": True, "channel_group_id": 1},
            {"id": 12, "name": "B", "auto_created": True, "channel_group_id": 2},
            {"id": 13, "name": "Manual", "auto_created": False, "channel_group_id": 2},
        ])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"all_groups": True})
        text = _text(result)
        token = derive_token([11, 12])  # manual id 13 excluded
        assert token in text
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_all_groups_correct_token_clears(self):
        mcp = _register("channels")
        mock_client = AsyncMock()

        def _side(ep, **kw):
            if ep.name == "channels_list":
                return self._channels_page([
                    {"id": 11, "name": "A", "auto_created": True, "channel_group_id": 1},
                    {"id": 12, "name": "B", "auto_created": True, "channel_group_id": 2},
                ])
            return {"deleted": 2}

        mock_client.call_endpoint.side_effect = _side
        token = derive_token([11, 12])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created", {"all_groups": True, "confirm_token": token}
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_clear_auto_created"
        assert "Cleared 2" in _text(result)

    @pytest.mark.asyncio
    async def test_all_groups_always_gated_even_when_empty(self):
        """all_groups path is token-gated regardless of resolved count — even a
        single resolved channel requires the token (no frictionless threshold)."""
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = self._channels_page([
            {"id": 11, "name": "A", "auto_created": True, "channel_group_id": 1},
        ])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"all_groups": True})
        assert _mutation_calls(mock_client) == []
        assert derive_token([11]) in _text(result)

    @pytest.mark.asyncio
    async def test_stale_token_refused(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = self._channels_page([
            {"id": 11, "name": "A", "auto_created": True, "channel_group_id": 1},
            {"id": 12, "name": "B", "auto_created": True, "channel_group_id": 2},
        ])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"all_groups": True, "confirm_token": "2-deadbeef"},
            )
        assert _mutation_calls(mock_client) == []
        assert derive_token([11, 12]) in _text(result)


# ===========================================================================
# Tier B — bulk_merge_duplicate_channels (token + manual-channel flag)
# ===========================================================================
class TestBulkMergeToken:
    @pytest.mark.asyncio
    async def test_no_token_previews_token_over_source_ids(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        # State lookups for flagging manual channels.
        mock_client.call_endpoint.return_value = {
            "id": 0, "name": "x", "auto_created": True,
        }
        merges = [{"target_channel_id": 100, "source_channel_ids": [101, 102]}]
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_merge_duplicate_channels", {"merges": merges}
            )
        text = _text(result)
        token = derive_token([101, 102])  # source ids deleted
        assert token in text
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_correct_token_merges(self):
        mcp = _register("channels")
        mock_client = AsyncMock()

        def _side(ep, **kw):
            if ep.name == "channels_get":
                return {"id": 1, "name": "x", "auto_created": True}
            return {"merged": 1, "failed": 0, "results": []}

        mock_client.call_endpoint.side_effect = _side
        merges = [{"target_channel_id": 100, "source_channel_ids": [101, 102]}]
        token = derive_token([101, 102])
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "bulk_merge_duplicate_channels",
                {"merges": merges, "confirm_token": token},
            )
        muts = _mutation_calls(mock_client)
        assert len(muts) == 1
        assert muts[0].args[0].name == "channels_bulk_merge"

    @pytest.mark.asyncio
    async def test_preview_flags_manual_target(self):
        """If a target or source channel is manual (auto_created=False), the
        preview must LOUDLY flag it (W1 bleed, operator-driven)."""
        mcp = _register("channels")
        mock_client = AsyncMock()

        def _side(ep, **kw):
            # target 100 is manual; sources auto.
            cid = kw.get("path_args", {}).get("channel_id")
            return {"id": cid, "name": f"Ch{cid}", "auto_created": cid != 100}

        mock_client.call_endpoint.side_effect = _side
        merges = [{"target_channel_id": 100, "source_channel_ids": [101, 102]}]
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_merge_duplicate_channels", {"merges": merges}
            )
        text = _text(result).lower()
        assert "manual" in text
        assert "100" in _text(result)
        assert _mutation_calls(mock_client) == []

    @pytest.mark.asyncio
    async def test_stale_token_refused(self):
        mcp = _register("channels")
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "id": 0, "name": "x", "auto_created": True,
        }
        merges = [{"target_channel_id": 100, "source_channel_ids": [101, 102]}]
        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "bulk_merge_duplicate_channels",
                {"merges": merges, "confirm_token": "2-deadbeef"},
            )
        assert _mutation_calls(mock_client) == []
        assert derive_token([101, 102]) in _text(result)
