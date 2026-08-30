"""bead 6n76m — the MCP service principal must NOT be able to restore backups.

SECURITY REGRESSION (team-review finding, GH #591 review):
``routers.settings._resolve_settings_admin`` deliberately treats the static MCP
service principal as NON-admin for POST /api/settings (kgz3k), so the MCP key
cannot rewrite outbound base URLs, integration secrets, or the OOM safety caps.
But the backup-restore endpoints were gated by the plain ``RequireAdminIfEnabled``
(which only checks ``is_admin``, and ``_build_mcp_service_principal`` sets
``is_admin=True``) and write the settings blob WHOLESALE — so an MCP-key holder
denied every admin-only field via /api/settings could flip them all (and restore
non-redacted credentials from a legacy ZIP) by driving a restore instead.

Fix: the three ECM-settings-writing restore endpoints now use
``RequireHumanAdminIfEnabled``, which rejects the MCP principal (403) while
still admitting a human operator admin. These tests pin that:

  * MCP principal -> 403 on /restore, /restore-saved, /restore-yaml, and NO
    settings write happens (the restore body is never reached).
  * A real admin / auth-disabled caller still succeeds (positive control).

Auth is exercised through the REAL dependency path: a static MCP key on the
Bearer header + ``auth.dependencies.get_auth_settings`` forced to require_auth
so ``RequireHumanAdminIfEnabled`` actually enforces. Mirrors the
``TestSettingsMcpKeyGate`` pattern in test_settings.py.
"""
import io
import json
import zipfile

import pytest
from unittest.mock import patch

from config import DispatcharrSettings

from .test_backup import _minimal_journal_db_bytes


MCP_KEY = "mcp-secret-key-6n76m"


def _runtime_settings_with_mcp_key():
    """Config settings carrying the static MCP key so ``_is_mcp_service_token``
    matches the Bearer token and ``get_current_user`` returns the principal."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000", username="u", password="p",
        mcp_api_key=MCP_KEY,
    )


def _auth_enabled_mcp_patches():
    """Context managers that force auth ON at the dependency layer and wire the
    static MCP key. Returns a list of patchers the caller enters."""
    return [
        # _is_mcp_service_token() + get_current_user() read config via
        # auth.dependencies.get_settings.
        patch("auth.dependencies.get_settings", return_value=_runtime_settings_with_mcp_key()),
        # require_admin_if_enabled() reads auth.dependencies.get_auth_settings.
        patch("auth.dependencies.get_auth_settings"),
    ]


def _make_backup_zip() -> bytes:
    """A minimal valid ECM backup ZIP (manifest + settings + sqlite db)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("settings.json", json.dumps({"url": "http://attacker:9191"}))
        zf.writestr("journal.db", _minimal_journal_db_bytes())
        zf.writestr(
            "ecm_backup.json",
            json.dumps({
                "version": "0.17.3-0001",
                "created_at": "2026-05-24T00:00:00+00:00",
                "files": ["settings.json", "journal.db"],
            }),
        )
    buf.seek(0)
    return buf.getvalue()


@pytest.fixture
def backups_dir(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# MCP principal is rejected (403) on every settings-writing restore endpoint
# ---------------------------------------------------------------------------
class TestMcpPrincipalRejectedOnRestore:
    @pytest.mark.asyncio
    async def test_restore_saved_rejects_mcp_principal(self, async_client, backups_dir):
        """POST /restore-saved with the MCP key -> 403, and the restore body
        (which writes the settings blob) is never reached."""
        fname = "ecm-backup-2026-05-24_120000.zip"
        (backups_dir / fname).write_bytes(_make_backup_zip())

        p_settings, p_auth = _auth_enabled_mcp_patches()
        with p_settings, p_auth as auth_mock, \
             patch("routers.backup.BACKUPS_DIR", backups_dir), \
             patch("routers.backup._restore_from_zip") as mock_restore:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/backup/restore-saved",
                json={"filename": fname},
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 403, response.json()
        assert "MCP service principal" in response.json()["detail"]
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_upload_rejects_mcp_principal(self, async_client):
        """POST /restore (uploaded ZIP) with the MCP key -> 403, no restore."""
        p_settings, p_auth = _auth_enabled_mcp_patches()
        with p_settings, p_auth as auth_mock, \
             patch("routers.backup._restore_from_zip") as mock_restore:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/backup/restore",
                files={"file": ("backup.zip", _make_backup_zip(), "application/zip")},
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 403, response.json()
        assert "MCP service principal" in response.json()["detail"]
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_yaml_rejects_mcp_principal(self, async_client):
        """POST /restore-yaml with the MCP key -> 403, no settings section write."""
        p_settings, p_auth = _auth_enabled_mcp_patches()
        with p_settings, p_auth as auth_mock, \
             patch("routers.backup._restore_settings") as mock_restore_settings:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/backup/restore-yaml",
                data={"sections": json.dumps(["settings"])},
                files={"file": ("export.yaml", b"settings:\n  url: http://x\n", "text/yaml")},
                headers={"Authorization": f"Bearer {MCP_KEY}"},
            )

        assert response.status_code == 403, response.json()
        assert "MCP service principal" in response.json()["detail"]
        mock_restore_settings.assert_not_called()


# ---------------------------------------------------------------------------
# Positive control — a human/admin (auth-disabled default) still succeeds
# ---------------------------------------------------------------------------
class TestHumanAdminRestoreStillWorks:
    @pytest.mark.asyncio
    async def test_restore_saved_succeeds_without_mcp_key(
        self, async_client, backups_dir, tmp_path
    ):
        """With auth disabled (default async_client) the restore still runs — the
        6n76m gate only rejects the MCP principal, not ordinary admin callers."""
        fname = "ecm-backup-2026-05-24_120000.zip"
        (backups_dir / fname).write_bytes(_make_backup_zip())

        with patch("routers.backup.BACKUPS_DIR", backups_dir), \
             patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db"), \
             patch("routers.backup.init_db"), \
             patch("routers.backup.reset_client"), \
             patch("routers.backup.clear_settings_cache"):
            response = await async_client.post(
                "/api/backup/restore-saved",
                json={"filename": fname},
            )

        assert response.status_code == 200, response.json()
        assert "settings.json" in response.json()["restored_files"]
