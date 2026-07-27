"""bd-0wmeg — MCP tools for the v0.18.0 DBAS backup/restore surface.

Two new tools on tools/system.py:

  * create_dbas_backup — triggers the dbas_backup task via
    POST /api/tasks/dbas_backup/run, optionally passphrase-encrypted + cred
    carrying.
  * restore_dbas_backup_saved — restores a SAVED DBAS artifact by filename via
    POST /api/backup/restore-dbas-saved, dry-run by default.

The LOAD-BEARING assertion (PO hard requirement): a passphrase passed to either
tool must NEVER appear in the tool's return value NOR in any log line the tool
emits. It must, however, reach the backend request body so an encrypted backup
can actually be made / decrypted.

Patterns mirror tests/test_0hjrk_backups.py (FastMCP register + AsyncMock
ecm_client patched at tools.system.get_ecm_client).
"""
import logging
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP

SECRET = "S3cr3t-Passphrase-Do-Not-Leak"


def _register_system() -> FastMCP:
    mcp = FastMCP("test")
    from tools.system import register

    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# create_dbas_backup
# ---------------------------------------------------------------------------
class TestCreateDbasBackup:
    @pytest.mark.asyncio
    async def test_redacted_default_no_passphrase(self):
        """No passphrase -> redacted backup; the task is run via tasks_run with
        task_id=dbas_backup and NO passphrase in the body."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "completed",
            "message": "Built DBAS backup ecm-backup-2026-06-20_120000.zip",
            "details": {"filename": "ecm-backup-2026-06-20_120000.zip"},
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("create_dbas_backup", {})

        text = _text(result)
        assert "ecm-backup-2026-06-20_120000.zip" in text
        assert "redacted" in text.lower()

        ep = mock_client.call_endpoint.call_args.args[0]
        assert ep.name == "tasks_run"
        assert ep.method == "POST"
        kwargs = mock_client.call_endpoint.call_args.kwargs
        assert kwargs["path_args"] == {"task_id": "dbas_backup"}
        params = kwargs["body"]["parameters"]
        assert "passphrase" not in params
        assert params["include_credentials"] is False
        assert params["acknowledge_unrecoverable"] is False

    @pytest.mark.asyncio
    async def test_encrypted_forwards_passphrase_to_body(self):
        """With a passphrase + acks, the passphrase is forwarded to the task
        body so the backend can actually encrypt the artifact."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "completed",
            "details": {"filename": "ecm-backup-2026-06-20_130000.zip"},
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_dbas_backup",
                {
                    "passphrase": SECRET,
                    "include_credentials": True,
                    "acknowledge_unrecoverable": True,
                },
            )

        text = _text(result)
        assert "encrypted" in text.lower()
        params = mock_client.call_endpoint.call_args.kwargs["body"]["parameters"]
        assert params["passphrase"] == SECRET
        assert params["include_credentials"] is True
        assert params["acknowledge_unrecoverable"] is True

    @pytest.mark.asyncio
    async def test_passphrase_never_in_result_or_logs(self, caplog):
        """LOAD-BEARING: the passphrase must NOT appear in the tool's return
        value NOR in any log record emitted by the tool."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "completed",
            "message": "Built DBAS backup ecm-backup-2026-06-20_140000.zip",
            "details": {"filename": "ecm-backup-2026-06-20_140000.zip"},
        }
        with caplog.at_level(logging.DEBUG, logger="tools.system"):
            with patch("tools.system.get_ecm_client", return_value=mock_client):
                result = await mcp.call_tool(
                    "create_dbas_backup",
                    {
                        "passphrase": SECRET,
                        "include_credentials": True,
                        "acknowledge_unrecoverable": True,
                    },
                )

        assert SECRET not in _text(result)
        for rec in caplog.records:
            assert SECRET not in rec.getMessage()
            assert SECRET not in str(rec.args or "")

    @pytest.mark.asyncio
    async def test_passphrase_not_in_error_path(self, caplog):
        """Even when the backend call raises, the passphrase must not leak into
        the surfaced error or the logs."""
        mcp = _register_system()
        mock_client = AsyncMock()
        # call_endpoint surfaces endpoint+status+detail, never the request body.
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/tasks/dbas_backup/run -> HTTP 500 Internal Server Error"
        )
        with caplog.at_level(logging.DEBUG, logger="tools.system"):
            with patch("tools.system.get_ecm_client", return_value=mock_client):
                result = await mcp.call_tool(
                    "create_dbas_backup",
                    {"passphrase": SECRET, "acknowledge_unrecoverable": True},
                )

        text = _text(result)
        assert "Error" in text
        assert SECRET not in text
        for rec in caplog.records:
            assert SECRET not in rec.getMessage()


# ---------------------------------------------------------------------------
# restore_dbas_backup_saved
# ---------------------------------------------------------------------------
class TestRestoreDbasBackupSaved:
    @pytest.mark.asyncio
    async def test_dry_run_is_default(self):
        """confirm_apply omitted -> dry-run; calls backup_restore_dbas_saved
        with confirm_apply False and no passphrase."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "started",
            "task_id": "dbas_restore",
            "is_dry_run": True,
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_dbas_backup_saved",
                {"filename": "ecm-backup-2026-06-20_120000.zip"},
            )

        text = _text(result)
        assert "dry-run" in text.lower()

        ep = mock_client.call_endpoint.call_args.args[0]
        assert ep.name == "backup_restore_dbas_saved"
        assert ep.method == "POST"
        body = mock_client.call_endpoint.call_args.kwargs["body"]
        assert body["filename"] == "ecm-backup-2026-06-20_120000.zip"
        assert body["confirm_apply"] is False
        assert "passphrase" not in body

    @pytest.mark.asyncio
    async def test_confirm_apply_forwarded(self):
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "started",
            "task_id": "dbas_restore",
            "is_dry_run": False,
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_dbas_backup_saved",
                {"filename": "ecm-backup-2026-06-20_120000.zip", "confirm_apply": True},
            )

        assert "apply" in _text(result).lower()
        body = mock_client.call_endpoint.call_args.kwargs["body"]
        assert body["confirm_apply"] is True

    @pytest.mark.asyncio
    async def test_passphrase_forwarded_to_body(self):
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "started",
            "task_id": "dbas_restore",
            "is_dry_run": True,
        }
        with patch("tools.system.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "restore_dbas_backup_saved",
                {"filename": "ecm-backup-2026-06-20_120000.zip", "passphrase": SECRET},
            )
        body = mock_client.call_endpoint.call_args.kwargs["body"]
        assert body["passphrase"] == SECRET

    @pytest.mark.asyncio
    async def test_passphrase_never_in_result_or_logs(self, caplog):
        """LOAD-BEARING: the restore passphrase must not appear in the return
        value NOR in any log line."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "status": "started",
            "task_id": "dbas_restore",
            "is_dry_run": True,
        }
        with caplog.at_level(logging.DEBUG, logger="tools.system"):
            with patch("tools.system.get_ecm_client", return_value=mock_client):
                result = await mcp.call_tool(
                    "restore_dbas_backup_saved",
                    {"filename": "ecm-backup-2026-06-20_120000.zip", "passphrase": SECRET},
                )

        assert SECRET not in _text(result)
        for rec in caplog.records:
            assert SECRET not in rec.getMessage()
            assert SECRET not in str(rec.args or "")

    @pytest.mark.asyncio
    async def test_surfaces_validation_error_without_passphrase(self, caplog):
        """A backend 400 (bad filename) is surfaced; the passphrase never
        leaks via the error path."""
        mcp = _register_system()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/backup/restore-dbas-saved -> HTTP 400 Bad Request: Invalid filename"
        )
        with caplog.at_level(logging.DEBUG, logger="tools.system"):
            with patch("tools.system.get_ecm_client", return_value=mock_client):
                result = await mcp.call_tool(
                    "restore_dbas_backup_saved",
                    {"filename": "../etc/passwd", "passphrase": SECRET},
                )
        text = _text(result)
        assert "Error" in text
        assert "400" in text or "Invalid filename" in text
        assert SECRET not in text
        for rec in caplog.records:
            assert SECRET not in rec.getMessage()
