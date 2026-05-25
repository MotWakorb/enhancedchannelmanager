"""TDD tests for bd-0hjrk.3 and bd-0hjrk.4 — channel_groups MCP tools.

bd-0hjrk.3: get_auto_created_groups must render BOTH the total membership count
  and the auto-created subset. The backend returns ``channel_count`` (total)
  and ``auto_created_count`` (subset); the tool previously read a key that did
  not exist and always printed 0.

bd-0hjrk.4: list_channel_groups dumped ALL groups (~1115 live) with no compact
  mode and no pagination, blowing the MCP token limit. It must mirror
  list_channels: ``compact=True`` (pipe-delimited, ignores pagination, shows
  all) plus ``page``/``page_size`` so the default human call is bounded.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    from mcp.server.fastmcp import FastMCP
    from tools.channel_groups import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


class TestGetAutoCreatedGroupsRendersBothCounts:
    """get_auto_created_groups shows total membership AND auto-created subset."""

    @pytest.mark.asyncio
    async def test_renders_membership_and_auto_created_counts(self):
        """Render must read channel_count (170) and auto_created_count (12)."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_auto_created":
                return {
                    "groups": [
                        {
                            "id": 1,
                            "name": "Sports",
                            "channel_count": 170,
                            "auto_created_count": 12,
                            "sample_channels": [],
                        }
                    ],
                    "total_auto_created_channels": 12,
                }
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_auto_created_groups", {})

        text = result[0][0].text
        assert "170 channels" in text, (
            f"Expected total membership '170 channels' in output, got: {text!r}"
        )
        assert "12 auto-created" in text, (
            f"Expected '12 auto-created' subset in output, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_does_not_print_zero_for_populated_group(self):
        """Regression: tool must NOT print 0 channels for a populated group."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_auto_created":
                return {
                    "groups": [
                        {"id": 5, "name": "Movies", "channel_count": 88,
                         "auto_created_count": 3, "sample_channels": []}
                    ],
                    "total_auto_created_channels": 3,
                }
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_auto_created_groups", {})

        text = result[0][0].text
        assert "Movies" in text
        # Must not render the broken "0 channels" for a group with 88 members.
        assert "0 channels" not in text, (
            f"Tool printed '0 channels' for a populated group: {text!r}"
        )


def _make_groups(n):
    """Build n fake group dicts for pagination tests."""
    return [
        {"id": i, "name": f"Group {i}", "channel_count": i}
        for i in range(1, n + 1)
    ]


class TestListChannelGroupsCompact:
    """list_channel_groups compact mode: pipe-delimited, all rows, header."""

    @pytest.mark.asyncio
    async def test_compact_returns_header_and_pipe_rows(self):
        """compact=True returns 'id|name|channel_count' header + pipe rows."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_list":
                return [
                    {"id": 1, "name": "Sports", "channel_count": 170},
                    {"id": 2, "name": "Movies", "channel_count": 88},
                ]
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_channel_groups", {"compact": True})

        text = result[0][0].text
        lines = text.splitlines()
        assert lines[0] == "id|name|channel_count", (
            f"Expected pipe header first, got: {lines[0]!r}"
        )
        assert "1|Sports|170" in lines
        assert "2|Movies|88" in lines

    @pytest.mark.asyncio
    async def test_compact_shows_all_rows_ignoring_pagination(self):
        """compact=True shows ALL groups, ignoring page/page_size (like list_channels)."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_list":
                return _make_groups(250)
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "list_channel_groups", {"compact": True, "page": 1, "page_size": 100}
            )

        text = result[0][0].text
        data_rows = [ln for ln in text.splitlines() if "|" in ln and not ln.startswith("id|")]
        assert len(data_rows) == 250, (
            f"compact must show all 250 rows ignoring page_size, got {len(data_rows)}"
        )


class TestListChannelGroupsPagination:
    """list_channel_groups human mode: paginate so the default call is bounded."""

    @pytest.mark.asyncio
    async def test_default_call_does_not_dump_everything(self):
        """Default (non-compact) call must NOT dump all 1115 groups."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_list":
                return _make_groups(1115)
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_channel_groups", {})

        text = result[0][0].text
        rendered_groups = [ln for ln in text.splitlines() if "(id=" in ln]
        # Default page_size is 100 — far fewer than 1115.
        assert len(rendered_groups) <= 100, (
            f"Default call dumped {len(rendered_groups)} groups — must be bounded by page_size"
        )

    @pytest.mark.asyncio
    async def test_paged_call_returns_only_page_size_rows(self):
        """A paged call returns only page_size rows and notes the page."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_list":
                return _make_groups(250)
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "list_channel_groups", {"page": 2, "page_size": 50}
            )

        text = result[0][0].text
        rendered_groups = [ln for ln in text.splitlines() if "(id=" in ln]
        assert len(rendered_groups) == 50, (
            f"Expected exactly 50 rows on page 2, got {len(rendered_groups)}: {text!r}"
        )
        # Page 2 of page_size 50 → ids 51..100
        assert "(id=51)" in text, f"Expected page-2 first row id=51, got: {text!r}"
        assert "(id=100)" in text, f"Expected page-2 last row id=100, got: {text!r}"
        assert "(id=50)" not in text, "Page 2 must not include page-1 rows"
        # Output should note pagination context (page number / total).
        assert "page 2" in text.lower() or "page=2" in text.lower(), (
            f"Expected page context in output: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_empty_groups_message(self):
        """No groups still returns a friendly message (no crash)."""
        mcp = _make_mcp_and_register()

        async def call_endpoint_side_effect(endpoint, **kwargs):
            if endpoint.name == "groups_list":
                return []
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_channel_groups", {})

        text = result[0][0].text
        assert "No channel groups" in text
