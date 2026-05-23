"""Tests for lq38l.8: count/URL fields render real values instead of 0/N/A.

Covers:
- list_m3u_accounts: stream count derived via streams_list; URL from server_url
- get_m3u_account: stream count derived via streams_list; URL from server_url
- get_groups_with_streams: no false "0 streams" (payload only has id/name)
- list_epg_sources: channel count from epg_data_count (not the absent channel_count)
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_m3u():
    from mcp.server.fastmcp import FastMCP
    from tools.m3u import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _make_mcp_groups():
    from mcp.server.fastmcp import FastMCP
    from tools.channel_groups import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _make_mcp_epg():
    from mcp.server.fastmcp import FastMCP
    from tools.epg import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


# ---------------------------------------------------------------------------
# list_m3u_accounts
# ---------------------------------------------------------------------------

class TestListM3UAccountsCountAndUrl:
    """list_m3u_accounts renders real stream count and does not use server_url
    key incorrectly."""

    @pytest.mark.asyncio
    async def test_stream_count_derived_from_streams_list(self):
        """Stream count is fetched from streams_list endpoint, not account payload."""
        mcp = _make_mcp_m3u()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_list_providers":
                # Dispatcharr payload — no stream_count field
                return [{"id": 3, "name": "Provider 1", "server_url": "http://p1.test/m3u", "is_active": True}]
            if endpoint.name == "streams_list":
                # page_size=1 probe: returns count=2624
                return {"count": 2624, "results": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_m3u_accounts", {})

        text = result[0][0].text
        assert "2624 streams" in text, f"Expected '2624 streams' in output: {text!r}"
        assert "Provider 1" in text

    @pytest.mark.asyncio
    async def test_stream_count_probe_uses_m3u_account_filter(self):
        """streams_list is called with the correct m3u_account id for count derivation."""
        mcp = _make_mcp_m3u()

        captured_queries = []

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_list_providers":
                return [{"id": 7, "name": "HD Homerun", "server_url": "http://hdhr.test/m3u", "is_active": True}]
            if endpoint.name == "streams_list":
                captured_queries.append(kwargs.get("query", {}))
                return {"count": 150, "results": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            await mcp.call_tool("list_m3u_accounts", {})

        assert captured_queries, "streams_list was not called"
        q = captured_queries[0]
        assert q.get("m3u_account") == 7, f"Expected m3u_account=7 in query: {q!r}"
        assert q.get("page_size") == 1, f"Expected page_size=1 in query: {q!r}"

    @pytest.mark.asyncio
    async def test_no_false_zero_when_streams_list_unavailable(self):
        """When streams_list fails, stream count is omitted rather than showing 0."""
        mcp = _make_mcp_m3u()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_list_providers":
                return [{"id": 5, "name": "Custom", "server_url": "http://custom.test/m3u"}]
            if endpoint.name == "streams_list":
                raise Exception("timeout")
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_m3u_accounts", {})

        text = result[0][0].text
        # Must list the account
        assert "Custom" in text
        # Must NOT display a misleading "0 streams"
        assert "0 streams" not in text, f"Must not show false '0 streams': {text!r}"


# ---------------------------------------------------------------------------
# get_m3u_account
# ---------------------------------------------------------------------------

class TestGetM3UAccountCountAndUrl:
    """get_m3u_account renders real stream count (via streams_list) and URL (from server_url)."""

    @pytest.mark.asyncio
    async def test_url_read_from_server_url_field(self):
        """URL is read from server_url, not the absent 'url' key."""
        mcp = _make_mcp_m3u()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_get_account":
                return {
                    "id": 3,
                    "name": "Provider 1",
                    "server_url": "http://real-url.example.com/m3u",
                    "account_type": "M3U",
                    "is_active": True,
                    "updated_at": "2026-05-22T12:00:00Z",
                }
            if endpoint.name == "streams_list":
                return {"count": 2624, "results": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_account", {"account_id": 3})

        text = result[0][0].text
        assert "real-url.example.com" in text, (
            f"Expected server_url to appear in output but got: {text!r}"
        )
        assert "N/A" not in text, f"URL should not be N/A when server_url is present: {text!r}"

    @pytest.mark.asyncio
    async def test_stream_count_derived_for_single_account(self):
        """Stream count is derived via streams_list for the single account."""
        mcp = _make_mcp_m3u()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_get_account":
                return {"id": 3, "name": "Provider 1", "server_url": "http://p.test/m3u"}
            if endpoint.name == "streams_list":
                return {"count": 2624, "results": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_account", {"account_id": 3})

        text = result[0][0].text
        assert "2624" in text, f"Expected count 2624 in output: {text!r}"

    @pytest.mark.asyncio
    async def test_url_absent_falls_back_gracefully(self):
        """When both server_url and url are absent, URL shows N/A (not a crash)."""
        mcp = _make_mcp_m3u()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "m3u_get_account":
                return {"id": 9, "name": "HD Homerun", "account_type": "HDHR"}
            if endpoint.name == "streams_list":
                return {"count": 0, "results": []}
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.m3u.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_m3u_account", {"account_id": 9})

        text = result[0][0].text
        assert "HD Homerun" in text
        assert "N/A" in text  # graceful fallback for missing URL


# ---------------------------------------------------------------------------
# get_groups_with_streams
# ---------------------------------------------------------------------------

class TestGetGroupsWithStreams:
    """get_groups_with_streams must not display a misleading '0 streams' count.

    The /api/channel-groups/with-streams backend returns {id, name} only —
    no per-group stream count. The tool should omit the count rather than
    display a false zero.
    """

    @pytest.mark.asyncio
    async def test_no_false_zero_streams(self):
        """Groups are listed without a false '0 streams' label."""
        mcp = _make_mcp_groups()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "groups_with_streams":
                return {
                    "groups": [
                        {"id": 10, "name": "Entertainment"},
                        {"id": 11, "name": "ESPN+"},
                        {"id": 12, "name": "Radio"},
                    ],
                    "total_groups": 3,
                }
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_groups_with_streams", {})

        text = result[0][0].text
        assert "Entertainment" in text
        assert "ESPN+" in text
        assert "Radio" in text
        # Must NOT claim "0 streams" for any group
        assert "0 streams" not in text, (
            f"Must not display misleading '0 streams' when count is unavailable: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_group_names_present_without_count(self):
        """Group names appear in the output even without a stream count."""
        mcp = _make_mcp_groups()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "groups_with_streams":
                return {
                    "groups": [{"id": 5, "name": "Sports HD"}],
                    "total_groups": 5,
                }
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.channel_groups.get_ecm_client", return_value=client):
            result = await mcp.call_tool("get_groups_with_streams", {})

        text = result[0][0].text
        assert "Sports HD" in text
        assert "id=5" in text


# ---------------------------------------------------------------------------
# list_epg_sources
# ---------------------------------------------------------------------------

class TestListEpgSourcesChannelCount:
    """list_epg_sources reads epg_data_count (the real Dispatcharr field) not
    the absent channel_count."""

    @pytest.mark.asyncio
    async def test_channel_count_from_epg_data_count(self):
        """Channel count is taken from epg_data_count field."""
        mcp = _make_mcp_epg()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "epg_list_sources":
                return [
                    {
                        "id": 1,
                        "name": "Teamarr",
                        "url": "http://epg1.test/xmltv",
                        "epg_data_count": "8752",  # real payload is a string
                        "is_active": True,
                    },
                    {
                        "id": 2,
                        "name": "Gracenote",
                        "url": None,
                        "epg_data_count": "12000",
                        "is_active": True,
                    },
                ]
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_epg_sources", {})

        text = result[0][0].text
        assert "8752 channels" in text, (
            f"Expected '8752 channels' from epg_data_count but got: {text!r}"
        )
        assert "12000 channels" in text, (
            f"Expected '12000 channels' from epg_data_count but got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_no_false_zero_channels_when_epg_data_count_absent(self):
        """When epg_data_count is absent (unusual), count is omitted rather than '0 channels'."""
        mcp = _make_mcp_epg()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "epg_list_sources":
                return [
                    {
                        "id": 3,
                        "name": "B1G EPG",
                        "url": "http://epg3.test/xmltv",
                        # neither epg_data_count nor channel_count present
                        "is_active": True,
                    }
                ]
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_epg_sources", {})

        text = result[0][0].text
        assert "B1G EPG" in text
        # Must NOT display a misleading "0 channels"
        assert "0 channels" not in text, (
            f"Must not show '0 channels' when field is missing: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_old_channel_count_key_still_works_as_fallback(self):
        """Falls back to channel_count if epg_data_count is absent (forward-compat)."""
        mcp = _make_mcp_epg()

        async def call_ep(endpoint, **kwargs):
            if endpoint.name == "epg_list_sources":
                return [
                    {
                        "id": 4,
                        "name": "Legacy Source",
                        "url": "http://legacy.test/xmltv",
                        "channel_count": 500,  # old-style key
                        "is_active": True,
                    }
                ]
            return {}

        client = AsyncMock()
        client.call_endpoint.side_effect = call_ep

        with patch("tools.epg.get_ecm_client", return_value=client):
            result = await mcp.call_tool("list_epg_sources", {})

        text = result[0][0].text
        assert "500 channels" in text, (
            f"Expected fallback to channel_count=500 in output: {text!r}"
        )
