"""bd-0hjrk.5 — persist + list + restore on-demand backups.

The MCP ``create_backup`` tool streamed a ZIP to the HTTP client and persisted
nothing, so on-demand backups were neither listable nor restorable. This module
proves the new server-side persistence + restore path:

  * POST /api/backup/save writes an ``ecm-backup-<ts>.zip`` into BACKUPS_DIR and
    returns ``{filename, size_bytes, created_at}`` (it does NOT stream).
  * GET /api/backup/saved now lists ``.zip`` artifacts with ``type="zip"``
    alongside the existing ``.yaml`` entries (``type="yaml"``), newest first.
  * POST /api/backup/restore-saved restores from an on-disk ``.zip`` reusing the
    same restore code path as the uploaded-ZIP POST /restore.

SECURITY (prior P0s: bd-rbmkt path-injection, bd-l0nhi/0i2vt.1 redaction): the
filename-addressed endpoints (restore-saved, delete) must reject ``../`` traversal,
absolute paths, and any non-matching name with HTTP 400 *before* any filesystem
call reaches the attacker-controlled path. The admin guard
(RequireAdminIfEnabled) must be enforced.

Mocks at router module level per backend/CLAUDE.md
(``patch("routers.backup.X", ...)``). Uses a tmp BACKUPS_DIR fixture.
"""
import io
import json
import os
import zipfile

import pytest
from unittest.mock import MagicMock, patch

from .test_backup import _minimal_journal_db_bytes


def _make_backup_zip() -> bytes:
    """Build a minimal valid ECM backup ZIP (manifest + settings + sqlite db)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("settings.json", json.dumps({"url": "http://test:9191"}))
        zf.writestr("journal.db", _minimal_journal_db_bytes())
        zf.writestr(
            "ecm_backup.json",
            json.dumps(
                {
                    "version": "0.17.3-0001",
                    "created_at": "2026-05-24T00:00:00+00:00",
                    "files": ["settings.json", "journal.db"],
                }
            ),
        )
    buf.seek(0)
    return buf.getvalue()


class _CloseCountingFile:
    def __init__(self, file):
        self.file = file
        self.close_count = 0

    def close(self):
        self.close_count += 1
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __getattr__(self, name):
        return getattr(self.file, name)


@pytest.fixture
def backups_dir(tmp_path):
    """A clean on-disk BACKUPS_DIR for the save/list/restore endpoints."""
    d = tmp_path / "backups"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# POST /api/backup/save — persist
# ---------------------------------------------------------------------------
class TestSaveBackup:
    @pytest.mark.asyncio
    async def test_save_writes_zip_and_returns_metadata(self, async_client, backups_dir):
        """POST /save writes an ecm-backup-<ts>.zip into BACKUPS_DIR and returns
        {filename, size_bytes, created_at}; it does not stream the archive."""
        zip_bytes = _make_backup_zip()
        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup._create_backup_zip", return_value=io.BytesIO(zip_bytes)
        ):
            response = await async_client.post("/api/backup/save")

        assert response.status_code == 200
        data = response.json()
        assert data["filename"].startswith("ecm-backup-")
        assert data["filename"].endswith(".zip")
        assert data["size_bytes"] == len(zip_bytes)
        assert "created_at" in data

        # The file landed on disk inside BACKUPS_DIR.
        written = backups_dir / data["filename"]
        assert written.exists()
        assert written.read_bytes() == zip_bytes

    @pytest.mark.asyncio
    async def test_save_creates_backups_dir_when_absent(self, async_client, tmp_path):
        """First-ever save creates BACKUPS_DIR if it does not yet exist."""
        missing = tmp_path / "config" / "backups"
        assert not missing.exists()
        zip_bytes = _make_backup_zip()
        with patch("routers.backup.BACKUPS_DIR", missing), patch(
            "routers.backup._create_backup_zip", return_value=io.BytesIO(zip_bytes)
        ):
            response = await async_client.post("/api/backup/save")

        assert response.status_code == 200
        assert missing.exists()
        assert (missing / response.json()["filename"]).exists()

    @pytest.mark.asyncio
    async def test_save_admin_guarded(self, async_client, backups_dir):
        """POST /save is admin-gated — a non-admin caller gets 403."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            with patch("routers.backup.BACKUPS_DIR", backups_dir):
                response = await async_client.post("/api/backup/save")
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/backup/saved — list (now enumerates .zip too)
# ---------------------------------------------------------------------------
class TestListSavedIncludesZip:
    @pytest.mark.asyncio
    async def test_list_includes_zip_with_type_field(self, async_client, backups_dir):
        """A saved .zip is listed with type='zip'; .yaml entries keep type='yaml'."""
        (backups_dir / "ecm-backup-2026-01-01_000000.yaml").write_text("yaml-body")
        (backups_dir / "ecm-backup-2026-01-02_120000.zip").write_bytes(_make_backup_zip())

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get("/api/backup/saved")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        by_name = {e["filename"]: e for e in data}
        assert by_name["ecm-backup-2026-01-02_120000.zip"]["type"] == "zip"
        assert by_name["ecm-backup-2026-01-01_000000.yaml"]["type"] == "yaml"
        # Every entry carries filename/size_bytes/created_at.
        for e in data:
            assert "size_bytes" in e and "created_at" in e

    @pytest.mark.asyncio
    async def test_list_newest_first_across_formats(self, async_client, backups_dir):
        """Newest-first ordering holds across mixed .yaml/.zip artifacts."""
        (backups_dir / "ecm-backup-2026-01-01_000000.yaml").write_text("old")
        (backups_dir / "ecm-backup-2026-03-01_000000.zip").write_bytes(_make_backup_zip())
        (backups_dir / "ecm-backup-2026-02-01_000000.yaml").write_text("mid")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get("/api/backup/saved")

        names = [e["filename"] for e in response.json()]
        assert names == [
            "ecm-backup-2026-03-01_000000.zip",
            "ecm-backup-2026-02-01_000000.yaml",
            "ecm-backup-2026-01-01_000000.yaml",
        ]


# ---------------------------------------------------------------------------
# POST /api/backup/restore-saved — restore from on-disk zip
# ---------------------------------------------------------------------------
class TestRestoreSaved:
    @staticmethod
    def _track_archive_open(path):
        real_fdopen = os.fdopen
        opened = []
        target = path.stat()

        def fdopen(descriptor, mode):
            opened_file = os.fstat(descriptor)
            if (opened_file.st_dev, opened_file.st_ino) != (target.st_dev, target.st_ino):
                return real_fdopen(descriptor, mode)
            archive = _CloseCountingFile(real_fdopen(descriptor, mode))
            opened.append(archive)
            return archive

        return opened, fdopen

    @pytest.mark.asyncio
    async def test_restore_saved_valid_filename(self, async_client, backups_dir, tmp_path):
        """A valid on-disk .zip is restored via the shared restore path; the
        response echoes the restored files and backup version."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).write_bytes(_make_backup_zip())

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup.CONFIG_DIR", tmp_path
        ), patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), patch(
            "routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"
        ), patch(
            "routers.backup.close_db"
        ), patch(
            "routers.backup.init_db"
        ), patch(
            "routers.backup.clear_settings_cache"
        ), patch(
            "routers.backup.reset_client"
        ):
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "settings.json" in data["restored_files"]
        assert "journal.db" in data["restored_files"]
        # The shared restore path actually wrote the files back to disk.
        assert (tmp_path / "settings.json").exists()
        assert (tmp_path / "journal.db").exists()

    @pytest.mark.asyncio
    async def test_restore_saved_missing_file_returns_404(self, async_client, backups_dir):
        """A well-formed name that isn't on disk returns 404, not 400."""
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.post(
                "/api/backup/restore-saved",
                json={"filename": "ecm-backup-2099-12-31_235959.zip"},
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_restore_saved_closes_archive_when_zip_open_raises_oserror(
        self, async_client, backups_dir
    ):
        fname = "ecm-backup-2026-01-01_000000.zip"
        path = backups_dir / fname
        path.write_bytes(_make_backup_zip())
        opened, fdopen = self._track_archive_open(path)

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup.os.fdopen", side_effect=fdopen
        ), patch("routers.backup.zipfile.ZipFile", side_effect=OSError("zip read failed")):
            with pytest.raises(OSError, match="zip read failed"):
                await async_client.post(
                    "/api/backup/restore-saved", json={"filename": fname}
                )

        assert len(opened) == 1
        assert opened[0].close_count == 1

    @pytest.mark.asyncio
    async def test_restore_saved_closes_archive_once_for_bad_zip(
        self, async_client, backups_dir
    ):
        fname = "ecm-backup-2026-01-01_000000.zip"
        path = backups_dir / fname
        path.write_bytes(b"not a zip")
        opened, fdopen = self._track_archive_open(path)

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup.os.fdopen", side_effect=fdopen
        ):
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Saved file is not a valid zip archive"
        assert len(opened) == 1
        assert opened[0].close_count == 1

    @pytest.mark.asyncio
    async def test_restore_saved_closes_archive_once_for_valid_zip(
        self, async_client, backups_dir
    ):
        fname = "ecm-backup-2026-01-01_000000.zip"
        path = backups_dir / fname
        path.write_bytes(_make_backup_zip())
        opened, fdopen = self._track_archive_open(path)
        manifest = MagicMock()
        manifest.get.side_effect = {"version": "test", "created_at": "today"}.get

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup.os.fdopen", side_effect=fdopen
        ), patch("routers.backup._validate_backup_zip", return_value=manifest), patch(
            "routers.backup._restore_from_zip", return_value=[]
        ):
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 200
        assert len(opened) == 1
        assert opened[0].close_count == 1

    @pytest.mark.asyncio
    async def test_restore_saved_rejects_symlink(self, async_client, backups_dir, tmp_path):
        """A valid-name symlink is not a saved regular-file archive."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        outside = tmp_path / "outside.zip"
        outside.write_bytes(_make_backup_zip())
        (backups_dir / fname).symlink_to(outside)

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup._restore_from_zip"
        ) as mock_restore:
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 404
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_saved_rejects_non_regular_entry(self, async_client, backups_dir):
        """A direct child with a valid archive name must still be a regular file."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).mkdir()

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup._restore_from_zip"
        ) as mock_restore:
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 404
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_saved_does_not_follow_substituted_symlink(
        self, async_client, backups_dir, tmp_path
    ):
        """A regular file replaced after enumeration cannot redirect the open."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        selected = backups_dir / fname
        selected.write_bytes(_make_backup_zip())
        outside = tmp_path / "outside.zip"
        outside.write_bytes(_make_backup_zip())
        real_open = os.open

        def substitute_then_open(path, flags, *args, **kwargs):
            selected.unlink()
            selected.symlink_to(outside)
            return real_open(path, flags, *args, **kwargs)

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup.os.open", side_effect=substitute_then_open
        ), patch("routers.backup._restore_from_zip") as mock_restore:
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": fname}
            )

        assert response.status_code == 404
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_saved_rejects_yaml_artifact(self, async_client, backups_dir):
        """restore-saved is .zip-only — a .yaml name is rejected with 400
        (YAML section-import is a different path, out of scope)."""
        (backups_dir / "ecm-backup-2026-01-01_000000.yaml").write_text("body")
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.post(
                "/api/backup/restore-saved",
                json={"filename": "ecm-backup-2026-01-01_000000.yaml"},
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_restore_saved_admin_guarded(self, async_client, backups_dir):
        """restore-saved is admin-gated — a non-admin caller gets 403.

        bead 6n76m: the endpoint now uses ``RequireHumanAdminIfEnabled`` (which
        additionally rejects the MCP service principal), so the override targets
        that dependency.
        """
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireHumanAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            with patch("routers.backup.BACKUPS_DIR", backups_dir):
                response = await async_client.post(
                    "/api/backup/restore-saved",
                    json={"filename": "ecm-backup-2026-01-01_000000.zip"},
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# SECURITY — path-injection rejection on restore-saved + delete (zip names)
# ---------------------------------------------------------------------------
class TestRestoreSavedPathInjection:
    """Traversal / absolute / null-byte / backslash payloads must be rejected at
    the validation boundary before Path.read_bytes / zipfile.ZipFile touches an
    attacker-controlled path. Mirrors TestSavedBackupsPathInjection for the new
    JSON-body restore-saved endpoint and the zip-extended delete allowlist."""

    REJECTED_FILENAMES = [
        "../etc/passwd",
        "../../etc/passwd",
        "../ecm-backup-2026-01-01_000000.zip",
        "/etc/passwd",
        "/tmp/ecm-backup-2026-01-01_000000.zip",
        "ecm-backup-2026-01-01_000000.zip\x00.evil",
        "..\\ecm-backup-2026-01-01_000000.zip",
        "evil-file.txt",
        "ecm-backup-bad.zip",
        "ecm-backup-2026-01-01_000000.tar",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", REJECTED_FILENAMES)
    async def test_restore_saved_rejects_bad_filename(self, async_client, backups_dir, payload):
        """restore-saved rejects traversal / absolute / non-matching names with 400.

        Patch the restore internals so that, if the guard ever failed open, the
        test would still not perform a real restore — but the assertion is that
        we never get past the 400 boundary."""
        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "routers.backup._restore_from_zip"
        ) as mock_restore:
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": payload}
            )
        assert response.status_code == 400
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            "../ecm-backup-2026-01-01_000000.zip",
            "/etc/passwd",
            "evil-file.exe",
            "ecm-backup-2026-01-01_000000.bin",
        ],
    )
    async def test_delete_rejects_bad_zip_filename(self, async_client, backups_dir, payload):
        """The zip-extended delete allowlist still rejects bad names with 400
        before Path.unlink is reached."""
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete(f"/api/backup/saved/{payload}")
        # URL-routing may turn some payloads into 404; the guarantee is no 2xx.
        assert response.status_code in (400, 404, 422)

    @pytest.mark.asyncio
    async def test_delete_accepts_valid_zip_filename(self, async_client, backups_dir):
        """A well-formed ecm-backup-<ts>.zip is now deletable (allowlist extended)."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        f = backups_dir / fname
        f.write_bytes(_make_backup_zip())
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete(f"/api/backup/saved/{fname}")
        assert response.status_code == 200
        assert not f.exists()
