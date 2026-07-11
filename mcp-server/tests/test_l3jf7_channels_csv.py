"""TDD tests for enhancedchannelmanager-l3jf7 — channel CSV export/import/
preview tools.

Covers export_channels_csv (raw text/csv response, truncated with a warning
above a char cap), preview_channels_csv (JSON body, the preview-first
companion), and import_channels_csv (multipart upload, confirm-gated,
docstring mandates preview-first).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _mcp():
    from mcp.server.fastmcp import FastMCP
    from tools.channels_csv import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


class TestExportChannelsCsv:
    @pytest.mark.asyncio
    async def test_returns_raw_csv_under_cap(self):
        mcp = _mcp()
        client = AsyncMock()
        csv_body = "channel_number,name,group_name,tvg_id,gracenote_id,logo_url,stream_urls\n1,ESPN,Sports,espn.us,,,\n"
        client.get_text.return_value = csv_body

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("export_channels_csv", {})

        assert _text(result) == csv_body
        client.get_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_above_cap_with_warning(self):
        mcp = _mcp()
        client = AsyncMock()
        header = "channel_number,name,group_name,tvg_id,gracenote_id,logo_url,stream_urls\n"
        row = "1,Channel,Group,tvg,,,\n"
        huge_csv = header + row * 20000  # well over any reasonable char cap
        client.get_text.return_value = huge_csv

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("export_channels_csv", {})

        text = _text(result)
        assert len(text) < len(huge_csv)
        assert "TRUNCATED" in text
        # Truncated output must still be valid up to the cut — no partial line.
        body_before_marker = text.split("# TRUNCATED", 1)[0]
        assert body_before_marker.endswith("\n") or body_before_marker == ""


class TestPreviewChannelsCsv:
    @pytest.mark.asyncio
    async def test_reports_rows_and_errors(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "rows": [{"name": "ESPN", "group_name": "Sports"}, {"name": "FOX", "group_name": "Sports"}],
            "errors": [{"row": 3, "error": "Missing required field: name"}],
        }

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("preview_channels_csv", {"content": "name,group_name\nESPN,Sports\n"})

        text = _text(result)
        assert "2 row(s)" in text
        assert "1 error(s)" in text
        assert "Row 3" in text
        assert "ESPN" in text
        body = client.call_endpoint.call_args.kwargs["body"]
        assert body == {"content": "name,group_name\nESPN,Sports\n"}

    @pytest.mark.asyncio
    async def test_no_errors(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {"rows": [{"name": "ESPN"}], "errors": []}

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("preview_channels_csv", {"content": "name\nESPN\n"})

        text = _text(result)
        assert "0 error(s)" in text


class TestImportChannelsCsv:
    @pytest.mark.asyncio
    async def test_preview_on_confirm_false_imports_nothing(self):
        mcp = _mcp()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "rows": [{"name": "ESPN"}, {"name": "FOX"}],
            "errors": [{"row": 2, "error": "bad"}],
        }

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("import_channels_csv", {"content": "name\nESPN\nFOX\n"})

        text = _text(result)
        assert "2 channel(s)" in text
        assert "1 error(s)" in text
        assert "confirm=True" in text
        client.post_multipart.assert_not_called()
        called = [c.args[0].name for c in client.call_endpoint.call_args_list]
        assert called == ["channels_preview_csv"]

    @pytest.mark.asyncio
    async def test_confirm_true_imports_via_multipart(self):
        mcp = _mcp()
        client = AsyncMock()
        client.post_multipart.return_value = {
            "success": True, "channels_created": 2, "groups_created": 1,
            "streams_linked": 0, "errors": [], "warnings": [],
        }

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("import_channels_csv", {
                "content": "name,group_name\nESPN,Sports\nFOX,Sports\n", "confirm": True,
            })

        text = _text(result)
        assert "2 channel(s) created" in text
        assert "1 group(s) created" in text
        client.call_endpoint.assert_not_called()
        call = client.post_multipart.call_args
        assert call.args[0] == "/api/channels/import-csv"
        files = call.kwargs["files"]
        assert files["file"][1] == b"name,group_name\nESPN,Sports\nFOX,Sports\n"

    @pytest.mark.asyncio
    async def test_confirm_true_reports_row_errors(self):
        mcp = _mcp()
        client = AsyncMock()
        client.post_multipart.return_value = {
            "success": False, "channels_created": 1, "groups_created": 0,
            "streams_linked": 0, "errors": ["Row 2: Missing name"], "warnings": [],
        }

        with patch("tools.channels_csv.get_ecm_client", return_value=client):
            result = await mcp.call_tool("import_channels_csv", {"content": "name\nESPN\n\n", "confirm": True})

        text = _text(result)
        assert "1 error(s)" in text
        assert "Missing name" in text
