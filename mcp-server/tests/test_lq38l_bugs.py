"""Regression tests for lq38l.9 and lq38l.10.

lq38l.9 — get_channel_popularity and get_user_channel_breakdown called
           call_endpoint(..., path_params=...) but ECMClient.call_endpoint()
           accepts path_args, not path_params.  Fixed in stats.py.

lq38l.10 — create_backup routed through ECMClient.get() which called r.json()
            on the binary ZIP response and crashed with UnicodeDecodeError.
            Fixed in system.py using a short-lived httpx.AsyncClient that reads
            raw bytes and never calls .json().
"""
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_call_endpoint_spy(return_value):
    """AsyncMock whose call_endpoint records kwargs so we can assert on them."""
    mock = AsyncMock()
    mock.call_endpoint.return_value = return_value
    return mock


def _register_stats(mcp):
    from tools.stats import register
    register(mcp)


def _register_system(mcp):
    from tools.system import register
    register(mcp)


# ---------------------------------------------------------------------------
# lq38l.9 — path_args (not path_params) forwarded by stats tools
# ---------------------------------------------------------------------------

class TestPathArgsForwarded:
    """Asserts that path-param stats tools pass path_args= to call_endpoint."""

    @pytest.mark.asyncio
    async def test_get_channel_popularity_passes_path_args(self):
        """get_channel_popularity passes path_args={'channel_id': ...} — not path_params."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        score_response = {
            "channel_id": "ch-abc",
            "channel_name": "ESPN",
            "score": 75.0,
            "rank": 5,
            "trend": "stable",
            "trend_percent": 0.0,
            "watch_count_7d": 80,
            "watch_time_7d": 28800,
            "unique_viewers_7d": 20,
            "bandwidth_7d": 1_000_000_000,
            "calculated_at": "2026-05-22T06:00:00Z",
        }

        mock_client = _make_call_endpoint_spy(score_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("get_channel_popularity", {"channel_id": "ch-abc"})

        # Must have been called exactly once
        mock_client.call_endpoint.assert_called_once()
        _, kwargs = mock_client.call_endpoint.call_args

        # path_args must be present with the correct key
        assert "path_args" in kwargs, (
            "call_endpoint must receive path_args= (not path_params=)"
        )
        assert kwargs["path_args"] == {"channel_id": "ch-abc"}

        # path_params must NOT be passed — that was the bug
        assert "path_params" not in kwargs, (
            "path_params= must not appear; it is not a valid call_endpoint kwarg"
        )

        # Sanity: the tool returned useful output, not an error
        text = result[0][0].text
        assert "ESPN" in text
        assert "Error" not in text

    @pytest.mark.asyncio
    async def test_get_user_channel_breakdown_dispatcharr_passes_path_args(self):
        """get_user_channel_breakdown source=dispatcharr passes path_args={'user_id': ...}."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        breakdown_response = {
            "data": [
                {
                    "channel_id": "ch-abc",
                    "channel_name": "ESPN",
                    "total_watch_seconds": 3600,
                    "session_count": 2,
                    "last_watched": "2026-05-22T10:00:00Z",
                    "latest_stream_name": None,
                }
            ],
            "meta": {"from_iso": None, "to_iso": None, "group_by": "channel", "total_rows": 1},
            "pagination": None,
        }

        mock_client = _make_call_endpoint_spy(breakdown_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_user_channel_breakdown", {"user_id": "2", "source": "dispatcharr"}
            )

        mock_client.call_endpoint.assert_called_once()
        _, kwargs = mock_client.call_endpoint.call_args
        assert "path_args" in kwargs
        assert kwargs["path_args"] == {"user_id": "2"}
        assert "path_params" not in kwargs

        text = result[0][0].text
        assert "ESPN" in text
        assert "Error" not in text

    @pytest.mark.asyncio
    async def test_get_user_channel_breakdown_emby_passes_path_args(self):
        """get_user_channel_breakdown source=emby passes path_args={'emby_user_id': ...}."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_stats(mcp)

        breakdown_response = {
            "data": [
                {
                    "channel_id": "ch-xyz",
                    "channel_name": "CNN",
                    "total_watch_seconds": 1800,
                    "session_count": 1,
                    "last_watched": "2026-05-21T20:00:00Z",
                    "latest_stream_name": None,
                }
            ],
            "meta": {"from_iso": None, "to_iso": None, "group_by": "channel", "total_rows": 1},
            "pagination": None,
        }

        mock_client = _make_call_endpoint_spy(breakdown_response)
        with patch("tools.stats.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "get_user_channel_breakdown",
                {"user_id": "emby-guid-abc", "source": "emby"},
            )

        mock_client.call_endpoint.assert_called_once()
        _, kwargs = mock_client.call_endpoint.call_args
        assert "path_args" in kwargs
        assert kwargs["path_args"] == {"emby_user_id": "emby-guid-abc"}
        assert "path_params" not in kwargs

        text = result[0][0].text
        assert "CNN" in text
        assert "Error" not in text


# ---------------------------------------------------------------------------
# lq38l.10 — create_backup reads binary content, never calls .json()
# ---------------------------------------------------------------------------

class TestCreateBackupBinaryHandling:
    """create_backup behaviour.

    History: lq38l.10 fixed a UnicodeDecodeError by having create_backup read
    the streamed binary ZIP via a short-lived httpx client (never .json()).
    bd-0hjrk.5 then REPLACED the stream-and-discard flow with POST
    /api/backup/save, which PERSISTS the backup server-side and returns JSON
    metadata. The binary-decode concern is therefore moot — the tool no longer
    downloads the ZIP at all — so these tests now assert the JSON-metadata path
    (full coverage of the new surface lives in test_0hjrk_backups.py)."""

    @pytest.mark.asyncio
    async def test_create_backup_returns_success_with_filename_and_size(self):
        """create_backup reports the persisted filename + KB size from the
        backup_save JSON response."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_system(mcp)

        mock_client = _make_call_endpoint_spy(
            {
                "filename": "ecm-backup-2026-05-24_120000.zip",
                "size_bytes": 2048,
                "created_at": "2026-05-24T12:00:00+00:00",
            }
        )
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_backup", {})

        text = result[0][0].text
        assert "ecm-backup-2026-05-24_120000.zip" in text
        assert "2 KB" in text, f"Expected size in output, got: {text!r}"
        assert "Error" not in text, f"Got an error response: {text!r}"
        # Routed through the backup_save contract endpoint (POST /api/backup/save).
        ep = mock_client.call_endpoint.call_args.args[0]
        assert ep.name == "backup_save" and ep.method == "POST"

    @pytest.mark.asyncio
    async def test_create_backup_returns_error_on_http_failure(self):
        """create_backup wraps backend errors and returns a clean message."""
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        _register_system(mcp)

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError("connection refused")
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_backup", {})

        text = result[0][0].text
        assert "Error creating backup" in text
        assert "connection refused" in text
