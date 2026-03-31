"""
Unit tests for backup endpoints.

Tests: GET /api/backup/create, POST /api/backup/restore, POST /api/backup/restore-initial,
       GET /api/backup/export
Mocks: get_settings(), get_engine(), close_db(), init_db(), clear_settings_cache(), reset_client()
"""
import io
import json
import zipfile

import pytest
import yaml
from unittest.mock import AsyncMock, MagicMock, patch, call

from models import (
    AutoCreationRule,
    FFmpegProfile,
    NormalizationRuleGroup,
    NormalizationRule,
    ScheduledTask,
    TagGroup,
    Tag,
)


def _make_backup_zip(
    include_settings=True,
    include_db=True,
    include_manifest=True,
    manifest_override=None,
    settings_content=None,
    db_content=None,
    extra_files=None,
):
    """Create a backup zip in memory for testing."""
    buf = io.BytesIO()
    files = []
    with zipfile.ZipFile(buf, "w") as zf:
        if include_settings:
            content = settings_content or json.dumps({"url": "http://test:9191", "username": "admin", "password": "pass"})
            zf.writestr("settings.json", content)
            files.append("settings.json")

        if include_db:
            # SQLite magic bytes
            content = db_content or (b"SQLite format 3\x00" + b"\x00" * 100)
            zf.writestr("journal.db", content)
            files.append("journal.db")

        if extra_files:
            for name, content in extra_files.items():
                zf.writestr(name, content)
                files.append(name)

        if include_manifest:
            manifest = manifest_override or {
                "version": "0.15.0",
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": files,
            }
            zf.writestr("ecm_backup.json", json.dumps(manifest))

    buf.seek(0)
    return buf


class TestCreateBackup:
    """Tests for GET /api/backup/create."""

    @pytest.mark.asyncio
    async def test_creates_backup_zip(self, async_client, tmp_path):
        """Returns a valid zip file with settings, db, and manifest."""
        # Create test files in tmp config dir
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"url": "http://test:9191"}')
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", settings_file), \
             patch("routers.backup.JOURNAL_DB_FILE", db_file), \
             patch("routers.backup.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/backup/create")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "ecm-backup-" in response.headers["content-disposition"]

        # Validate zip contents
        buf = io.BytesIO(response.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "ecm_backup.json" in names
            assert "settings.json" in names
            assert "journal.db" in names

            # Validate manifest
            manifest = json.loads(zf.read("ecm_backup.json"))
            assert "version" in manifest
            assert "created_at" in manifest
            assert "files" in manifest

    @pytest.mark.asyncio
    async def test_creates_backup_with_logo_dir(self, async_client, tmp_path):
        """Includes logo files in backup when they exist."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"url": "test"}')
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)

        # Create logo directory with a file
        logos_dir = tmp_path / "uploads" / "logos"
        logos_dir.mkdir(parents=True)
        (logos_dir / "test.png").write_bytes(b"PNG_DATA")

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", settings_file), \
             patch("routers.backup.JOURNAL_DB_FILE", db_file), \
             patch("routers.backup.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/backup/create")

        assert response.status_code == 200
        buf = io.BytesIO(response.content)
        with zipfile.ZipFile(buf) as zf:
            assert "uploads/logos/test.png" in zf.namelist()

    @pytest.mark.asyncio
    async def test_creates_backup_without_optional_dirs(self, async_client, tmp_path):
        """Creates backup successfully when optional dirs don't exist."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{}')
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", settings_file), \
             patch("routers.backup.JOURNAL_DB_FILE", db_file), \
             patch("routers.backup.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/backup/create")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_wal_checkpoint_failure_non_fatal(self, async_client, tmp_path):
        """WAL checkpoint failure does not prevent backup creation."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{}')
        db_file = tmp_path / "journal.db"
        db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)

        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("WAL error")

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", settings_file), \
             patch("routers.backup.JOURNAL_DB_FILE", db_file), \
             patch("routers.backup.get_engine", return_value=mock_engine):
            response = await async_client.get("/api/backup/create")

        assert response.status_code == 200


class TestRestoreBackup:
    """Tests for POST /api/backup/restore."""

    @pytest.mark.asyncio
    async def test_restores_from_valid_backup(self, async_client, tmp_path):
        """Restores files from a valid backup zip."""
        backup = _make_backup_zip()

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db"), \
             patch("routers.backup.init_db"), \
             patch("routers.backup.clear_settings_cache"), \
             patch("routers.backup.reset_client"):
            response = await async_client.post(
                "/api/backup/restore",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "settings.json" in data["restored_files"]
        assert "journal.db" in data["restored_files"]
        assert data["backup_version"] == "0.15.0"

        # Verify files were written
        assert (tmp_path / "settings.json").exists()
        assert (tmp_path / "journal.db").exists()

    @pytest.mark.asyncio
    async def test_restores_logo_files(self, async_client, tmp_path):
        """Restores logo directory files."""
        backup = _make_backup_zip(extra_files={
            "uploads/logos/logo1.png": b"PNG1",
            "uploads/logos/logo2.png": b"PNG2",
        })

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db"), \
             patch("routers.backup.init_db"), \
             patch("routers.backup.clear_settings_cache"), \
             patch("routers.backup.reset_client"):
            response = await async_client.post(
                "/api/backup/restore",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 200
        assert (tmp_path / "uploads" / "logos" / "logo1.png").exists()
        assert (tmp_path / "uploads" / "logos" / "logo2.png").exists()

    @pytest.mark.asyncio
    async def test_calls_close_db_and_init_db(self, async_client, tmp_path):
        """Restore closes and reinitializes database."""
        backup = _make_backup_zip()

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db") as mock_close, \
             patch("routers.backup.init_db") as mock_init, \
             patch("routers.backup.clear_settings_cache") as mock_clear, \
             patch("routers.backup.reset_client") as mock_reset:
            response = await async_client.post(
                "/api/backup/restore",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 200
        mock_close.assert_called_once()
        mock_init.assert_called_once()
        mock_clear.assert_called_once()
        mock_reset.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_non_zip_file(self, async_client):
        """Returns 400 for non-zip upload."""
        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", b"not a zip", "application/zip")},
        )
        assert response.status_code == 400
        assert "not a valid zip" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_zip_without_manifest(self, async_client):
        """Returns 400 for zip missing ecm_backup.json."""
        backup = _make_backup_zip(include_manifest=False)
        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", backup, "application/zip")},
        )
        assert response.status_code == 400
        assert "missing ecm_backup.json" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_settings_json(self, async_client):
        """Returns 400 when settings.json is not valid JSON."""
        backup = _make_backup_zip(settings_content="not json {{{")
        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", backup, "application/zip")},
        )
        assert response.status_code == 400
        assert "invalid settings.json" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_invalid_database(self, async_client):
        """Returns 400 when journal.db is not a SQLite file."""
        backup = _make_backup_zip(db_content=b"NOT_SQLITE_DATA_HERE")
        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", backup, "application/zip")},
        )
        assert response.status_code == 400
        assert "not a SQLite database" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, async_client):
        """Returns 400 for zip with path traversal entries."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("ecm_backup.json", json.dumps({"version": "1.0", "files": []}))
            zf.writestr("../../../etc/passwd", "evil")
        buf.seek(0)

        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", buf, "application/zip")},
        )
        assert response.status_code == 400
        assert "unsafe file paths" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_clears_existing_logo_dir_on_restore(self, async_client, tmp_path):
        """Existing logo directory is cleared before restoring."""
        # Create pre-existing logo
        logos_dir = tmp_path / "uploads" / "logos"
        logos_dir.mkdir(parents=True)
        (logos_dir / "old_logo.png").write_bytes(b"OLD")

        backup = _make_backup_zip(extra_files={
            "uploads/logos/new_logo.png": b"NEW",
        })

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db"), \
             patch("routers.backup.init_db"), \
             patch("routers.backup.clear_settings_cache"), \
             patch("routers.backup.reset_client"):
            response = await async_client.post(
                "/api/backup/restore",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 200
        # Old logo should be gone, new logo should exist
        assert not (logos_dir / "old_logo.png").exists()
        assert (logos_dir / "new_logo.png").exists()


class TestRestoreInitial:
    """Tests for POST /api/backup/restore-initial."""

    @pytest.mark.asyncio
    async def test_restores_when_unconfigured(self, async_client, tmp_path):
        """Allows restore when app is not yet configured."""
        backup = _make_backup_zip()
        mock_settings = MagicMock()
        mock_settings.is_configured.return_value = False

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", tmp_path / "settings.json"), \
             patch("routers.backup.JOURNAL_DB_FILE", tmp_path / "journal.db"), \
             patch("routers.backup.close_db"), \
             patch("routers.backup.init_db"), \
             patch("routers.backup.clear_settings_cache"), \
             patch("routers.backup.reset_client"):
            response = await async_client.post(
                "/api/backup/restore-initial",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_rejects_when_configured(self, async_client):
        """Returns 403 when app is already configured."""
        backup = _make_backup_zip()
        mock_settings = MagicMock()
        mock_settings.is_configured.return_value = True

        with patch("routers.backup.get_settings", return_value=mock_settings):
            response = await async_client.post(
                "/api/backup/restore-initial",
                files={"file": ("backup.zip", backup, "application/zip")},
            )

        assert response.status_code == 403
        assert "already configured" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_validates_zip_same_as_restore(self, async_client):
        """Initial restore endpoint validates zip the same way."""
        mock_settings = MagicMock()
        mock_settings.is_configured.return_value = False

        with patch("routers.backup.get_settings", return_value=mock_settings):
            response = await async_client.post(
                "/api/backup/restore-initial",
                files={"file": ("backup.zip", b"not a zip", "application/zip")},
            )

        assert response.status_code == 400
        assert "not a valid zip" in response.json()["detail"]


class TestExportYaml:
    """Tests for GET /api/backup/export."""

    @pytest.mark.asyncio
    async def test_returns_yaml_with_all_sections(self, async_client, test_session):
        """Export returns YAML with settings, database, and dispatcharr sections."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191", "username": "admin", "password": "secret"}

        mock_client = AsyncMock()
        mock_client.get_channels.return_value = [{"id": 1}]
        mock_client.get_channel_groups.return_value = [{"id": 1, "name": "News"}]
        mock_client.get_m3u_accounts.return_value = [{"id": 1, "name": "Provider", "url": "http://m3u"}]
        mock_client.get_epg_sources.return_value = [{"id": 1, "name": "EPG1", "url": "http://epg"}]
        mock_client.get_stream_profiles.return_value = [{"id": 1, "name": "Default"}]
        mock_client.get_channel_profiles.return_value = [{"id": 1, "name": "HD"}]

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=mock_client):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/yaml; charset=utf-8"
        assert "ecm-export-" in response.headers["content-disposition"]

        data = yaml.safe_load(response.text)
        assert "ecm_export" in data
        assert "settings" in data
        assert "database" in data
        assert "dispatcharr" in data

    @pytest.mark.asyncio
    async def test_redacts_passwords(self, async_client, test_session):
        """Export redacts sensitive fields from settings."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {
            "url": "http://test:9191",
            "password": "supersecret",
            "smtp_password": "smtpsecret",
            "username": "admin",
        }

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert data["settings"]["password"] == "***REDACTED***"
        assert data["settings"]["smtp_password"] == "***REDACTED***"
        assert data["settings"]["username"] == "admin"

    @pytest.mark.asyncio
    async def test_exports_db_tables(self, async_client, test_session):
        """Export includes DB records from key tables."""
        # Seed test data
        tg = TagGroup(name="Countries", description="Country tags", is_builtin=False)
        test_session.add(tg)
        test_session.flush()
        test_session.add(Tag(group_id=tg.id, value="US", case_sensitive=False, enabled=True, is_builtin=False))

        ng = NormalizationRuleGroup(name="TestGroup", description="Test", enabled=True, priority=1, is_builtin=False)
        test_session.add(ng)
        test_session.flush()
        test_session.add(NormalizationRule(
            group_id=ng.id, name="Rule1", enabled=True, priority=1,
            condition_type="contains", condition_value="HD",
            action_type="remove", action_value="HD",
            is_builtin=False,
        ))

        test_session.add(FFmpegProfile(name="TestProfile", config='{"test": true}'))
        test_session.commit()

        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        db = data["database"]

        assert len(db["tag_groups"]) >= 1
        assert db["tag_groups"][0]["name"] == "Countries"
        assert len(db["tag_groups"][0]["tags"]) == 1

        assert len(db["normalization_rule_groups"]) >= 1
        assert db["normalization_rule_groups"][0]["rules"][0]["name"] == "Rule1"

        assert len(db["ffmpeg_profiles"]) >= 1
        assert db["ffmpeg_profiles"][0]["name"] == "TestProfile"

    @pytest.mark.asyncio
    async def test_dispatcharr_not_connected(self, async_client, test_session):
        """Export handles Dispatcharr not connected gracefully."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": ""}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "_warning" in data["dispatcharr"]
        assert "not connected" in data["dispatcharr"]["_warning"]

    @pytest.mark.asyncio
    async def test_dispatcharr_error_graceful(self, async_client, test_session):
        """Export handles Dispatcharr API errors gracefully."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        mock_client = AsyncMock()
        mock_client.get_channels.side_effect = Exception("Connection refused")

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=mock_client):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "_warning" in data["dispatcharr"]
        assert "Connection refused" in data["dispatcharr"]["_warning"]

    @pytest.mark.asyncio
    async def test_export_metadata(self, async_client, test_session):
        """Export includes version and timestamp in metadata."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "version" in data["ecm_export"]
        assert "exported_at" in data["ecm_export"]
