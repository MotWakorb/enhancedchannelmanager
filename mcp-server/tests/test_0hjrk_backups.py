"""bd-0hjrk.5 — MCP backup discoverability (persist + list + restore).

Before this change the MCP ``create_backup`` tool streamed a ZIP to the HTTP
client and returned only a size string, ``list_saved_backups`` enumerated YAML
only, and there was no restore tool. This module proves the fixed MCP surface:

  (a) create_backup now persists server-side and returns the STABLE filename,
  (b) list_saved_backups renders both .zip and .yaml entries with their type,
  (c) restore_backup calls POST /api/backup/restore-saved and surfaces a
      backend validation error clearly.

Test patterns mirror tests/test_0hjrk_get_journal.py (FastMCP register +
AsyncMock ecm_client patched at tools.system.get_ecm_client).
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP


def _register_system() -> FastMCP:
    mcp = FastMCP("test")
    from tools.system import register

    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# create_backup — persists + returns the stable filename
# ---------------------------------------------------------------------------
class TestCreateBackup:
    @pytest.mark.asyncio
    async def test_returns_persisted_filename(self):
        """create_backup calls POST /api/backup/save and surfaces the stable
        filename + size so the operator can list/restore it later."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "filename": "ecm-backup-2026-05-24_120000.zip",
            "size_bytes": 42229504,
            "created_at": "2026-05-24T12:00:00+00:00",
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_backup", {})

        text = _text(result)
        assert "ecm-backup-2026-05-24_120000.zip" in text
        # The contract endpoint used is backup_save (POST /api/backup/save).
        ep = mock_client.call_endpoint.call_args.args[0]
        assert ep.name == "backup_save"
        assert ep.method == "POST"
        # restore/list guidance is surfaced so the operator knows the next step.
        assert "restore_backup" in text or "list_saved_backups" in text

    @pytest.mark.asyncio
    async def test_surfaces_error(self):
        """A backend failure is surfaced, not swallowed."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError("boom")
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_backup", {})
        assert "Error" in _text(result)


# ---------------------------------------------------------------------------
# list_saved_backups — renders zip + yaml with type
# ---------------------------------------------------------------------------
class TestListSavedBackups:
    @pytest.mark.asyncio
    async def test_renders_zip_and_yaml_entries(self):
        """Both .zip and .yaml saved backups are shown with their type and the
        filename (the stable id used by restore_backup / delete_saved_backup)."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = [
            {
                "filename": "ecm-backup-2026-05-24_120000.zip",
                "size_bytes": 42229504,
                "created_at": "2026-05-24T12:00:00+00:00",
                "type": "zip",
            },
            {
                "filename": "ecm-backup-2026-05-01_030000.yaml",
                "size_bytes": 1024,
                "created_at": "2026-05-01T03:00:00+00:00",
                "type": "yaml",
            },
        ]
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_saved_backups", {})

        text = _text(result)
        assert "ecm-backup-2026-05-24_120000.zip" in text
        assert "ecm-backup-2026-05-01_030000.yaml" in text
        # The tool renders [zip] / [yaml] type labels — assert the bracketed form
        # so the test fails if the label is removed or changed, not just if the
        # filename extension appears (which is incidental).
        assert "[zip]" in text, f"Expected '[zip]' type label in output, got: {text!r}"
        assert "[yaml]" in text, f"Expected '[yaml]' type label in output, got: {text!r}"

    @pytest.mark.asyncio
    async def test_empty(self):
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = []
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("list_saved_backups", {})
        assert "No saved backups" in _text(result)


# ---------------------------------------------------------------------------
# restore_backup — new tool calling POST /api/backup/restore-saved
# ---------------------------------------------------------------------------
class TestRestoreBackup:
    @pytest.mark.asyncio
    async def test_calls_restore_saved_endpoint(self):
        """restore_backup passes the filename to backup_restore_saved and
        reports the restored result."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "ok",
            "backup_version": "0.17.3-0001",
            "restored_files": ["settings.json", "journal.db"],
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_backup", {"filename": "ecm-backup-2026-05-24_120000.zip"}
            )

        ep = mock_client.call_endpoint.call_args.args[0]
        assert ep.name == "backup_restore_saved"
        assert ep.method == "POST"
        # The filename rides in the request body per the contract.
        assert mock_client.call_endpoint.call_args.kwargs["body"] == {
            "filename": "ecm-backup-2026-05-24_120000.zip"
        }
        assert "ok" in _text(result).lower() or "restored" in _text(result).lower()

    @pytest.mark.asyncio
    async def test_surfaces_validation_error(self):
        """A backend 400 (bad filename) is surfaced to the caller, not hidden."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/backup/restore-saved -> HTTP 400 Bad Request: Invalid filename"
        )
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_backup", {"filename": "../etc/passwd"}
            )
        text = _text(result)
        assert "Error" in text
        assert "400" in text or "Invalid filename" in text
