"""
Unit tests for backup endpoints.

Tests: GET /api/backup/create, POST /api/backup/restore, POST /api/backup/restore-initial,
       GET /api/backup/export, POST /api/backup/validate, POST /api/backup/restore-yaml
Mocks: get_settings(), get_engine(), close_db(), init_db(), clear_settings_cache(), reset_client()
"""
import io
import json
import zipfile

import pytest
import yaml
from unittest.mock import AsyncMock, MagicMock, patch

from models import (
    AutoCreationRule,
    DummyEPGProfile,
    DummyEPGChannelAssignment,
    FFmpegProfile,
    NormalizationRuleGroup,
    NormalizationRule,
    ScheduledTask,
    TaskSchedule,
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
        dispatcharr = data.get("dispatcharr", {})
        assert "_warning" in dispatcharr
        assert "not connected" in dispatcharr["_warning"]

    @pytest.mark.asyncio
    async def test_dispatcharr_error_graceful(self, async_client, test_session):
        """Export handles Dispatcharr API errors gracefully."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        mock_client = AsyncMock()
        mock_client.get_m3u_accounts.side_effect = Exception("Connection refused")

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=mock_client):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        dispatcharr = data.get("dispatcharr", {})
        assert "_warning" in dispatcharr
        assert "Connection refused" in dispatcharr["_warning"]

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


class TestExportSections:
    """Tests for GET /api/backup/export-sections."""

    @pytest.mark.asyncio
    async def test_returns_section_list(self, async_client):
        """Returns available section keys and labels."""
        response = await async_client.get("/api/backup/export-sections")
        assert response.status_code == 200
        sections = response.json()
        assert len(sections) == 13
        keys = {s["key"] for s in sections}
        assert "settings" in keys
        assert "tag_groups" in keys
        assert "ffmpeg_profiles" in keys
        assert all("label" in s for s in sections)


class TestSelectiveExport:
    """Tests for selective GET /api/backup/export?sections=..."""

    @pytest.mark.asyncio
    async def test_export_specific_sections(self, async_client, test_session):
        """Export with sections param only includes requested sections."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export?sections=settings,tag_groups")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "settings" in data
        assert "database" in data
        assert "tag_groups" in data["database"]
        # These should NOT be in the export
        assert "scheduled_tasks" not in data.get("database", {})
        assert "auto_creation_rules" not in data.get("database", {})
        # Metadata should list selected sections
        assert sorted(data["ecm_export"]["sections_included"]) == ["settings", "tag_groups"]

    @pytest.mark.asyncio
    async def test_export_no_sections_returns_all(self, async_client, test_session):
        """Export without sections param includes everything."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "settings" in data
        assert "database" in data
        assert len(data["ecm_export"]["sections_included"]) == 13

    @pytest.mark.asyncio
    async def test_export_invalid_section_returns_400(self, async_client):
        """Export with unknown section key returns 400."""
        response = await async_client.get("/api/backup/export?sections=bogus")
        assert response.status_code == 400
        assert "Unknown sections" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_export_db_only_sections(self, async_client, test_session):
        """Export with only DB sections omits settings."""
        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "http://test:9191"}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export?sections=ffmpeg_profiles")

        assert response.status_code == 200
        data = yaml.safe_load(response.text)
        assert "settings" not in data
        assert "ffmpeg_profiles" in data.get("database", {})


class TestSavedBackups:
    """Tests for saved backup endpoints (list/download/delete)."""

    @pytest.mark.asyncio
    async def test_list_empty(self, async_client, tmp_path):
        """Returns empty list when no backups directory."""
        with patch("routers.backup.BACKUPS_DIR", tmp_path / "nonexistent"):
            response = await async_client.get("/api/backup/saved")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_with_files(self, async_client, tmp_path):
        """Returns backup files sorted newest first."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        (backups_dir / "ecm-backup-2026-01-01_000000.yaml").write_text("yaml1")
        (backups_dir / "ecm-backup-2026-01-02_000000.yaml").write_text("yaml2longer")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get("/api/backup/saved")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Newest first
        assert data[0]["filename"] == "ecm-backup-2026-01-02_000000.yaml"
        assert data[0]["size_bytes"] == 11
        assert "created_at" in data[0]

    @pytest.mark.asyncio
    async def test_download(self, async_client, tmp_path):
        """Downloads a saved backup file."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        (backups_dir / "ecm-backup-2026-01-01_000000.yaml").write_text("yaml_content")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get("/api/backup/saved/ecm-backup-2026-01-01_000000.yaml")

        assert response.status_code == 200
        assert response.text == "yaml_content"
        assert "text/yaml" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_404(self, async_client, tmp_path):
        """Returns 404 for missing backup file."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get("/api/backup/saved/ecm-backup-2026-01-01_000000.yaml")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_invalid_filename_returns_400(self, async_client):
        """Returns 400 for invalid filenames."""
        response = await async_client.get("/api/backup/saved/evil-file.txt")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_delete(self, async_client, tmp_path):
        """Deletes a saved backup file."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        f = backups_dir / "ecm-backup-2026-01-01_000000.yaml"
        f.write_text("yaml_content")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete("/api/backup/saved/ecm-backup-2026-01-01_000000.yaml")

        assert response.status_code == 200
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_invalid_filename_returns_400(self, async_client):
        """Returns 400 for invalid filename on delete."""
        response = await async_client.delete("/api/backup/saved/evil-file.txt")
        assert response.status_code == 400


class TestSavedBackupsPathInjection:
    """Path-injection regression tests for download/delete saved backup endpoints.

    Remediates CodeQL HIGH alerts 1416-1419 (py/path-injection, CWE-22/23/36/73/99).
    Backstops the canonicalize-and-verify containment check in
    routers.backup.download_saved_backup / delete_saved_backup.
    """

    # Traversal payloads that must be rejected at the 400 boundary before any
    # filesystem call reaches Path.exists / Path.read_text / Path.unlink.
    # Each is URL-encoded where needed to survive the FastAPI path parameter.
    TRAVERSAL_PAYLOADS = [
        # URL-encoded '../' traversal
        "..%2Fecm-backup-2026-01-01_000000.yaml",
        "..%2F..%2Fetc%2Fpasswd",
        # URL-encoded absolute paths
        "%2Fetc%2Fpasswd",
        "%2Ftmp%2Fecm-backup-2026-01-01_000000.yaml",
        # Null-byte splice (URL-encoded) — legacy-Python filename smuggling
        "ecm-backup-2026-01-01_000000.yaml%00.evil",
        # Backslash (Windows-style traversal)
        "..%5Cecm-backup-2026-01-01_000000.yaml",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    async def test_download_rejects_traversal_payloads(self, async_client, tmp_path, payload):
        """download_saved_backup must reject traversal / absolute / null-byte payloads with 400."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get(f"/api/backup/saved/{payload}")
        # Any non-2xx rejection is acceptable; the critical guarantee is that
        # we never reach Path.read_text on an attacker-controlled path.
        assert response.status_code in (400, 404, 422), (
            f"payload {payload!r} was not rejected: status={response.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", TRAVERSAL_PAYLOADS)
    async def test_delete_rejects_traversal_payloads(self, async_client, tmp_path, payload):
        """delete_saved_backup must reject traversal / absolute / null-byte payloads with 400."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete(f"/api/backup/saved/{payload}")
        assert response.status_code in (400, 404, 422), (
            f"payload {payload!r} was not rejected: status={response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_download_rejects_symlink_escape(self, async_client, tmp_path):
        """A symlink inside BACKUPS_DIR pointing outside must not leak contents.

        Creates a symlink whose *name* passes the regex but whose resolved
        target is outside BACKUPS_DIR. After canonicalization, .relative_to()
        must raise and produce a 400.
        """
        import os

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.yaml"
        secret.write_text("top-secret-contents")

        # Symlink name satisfies _BACKUP_FILENAME_RE but points outside the dir.
        link_name = "ecm-backup-2099-12-31_235959.yaml"
        link_path = backups_dir / link_name
        try:
            os.symlink(secret, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported in this environment")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get(f"/api/backup/saved/{link_name}")

        # The canonicalized path resolves outside BACKUPS_DIR, so the
        # containment check must reject with 400. If the check regressed, the
        # response body would contain "top-secret-contents".
        assert response.status_code == 400
        assert "top-secret-contents" not in response.text

    @pytest.mark.asyncio
    async def test_delete_rejects_symlink_escape(self, async_client, tmp_path):
        """A symlink inside BACKUPS_DIR pointing outside must not be deleted via resolve()."""
        import os

        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.yaml"
        secret.write_text("do-not-delete-me")

        link_name = "ecm-backup-2099-12-31_235959.yaml"
        link_path = backups_dir / link_name
        try:
            os.symlink(secret, link_path)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported in this environment")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete(f"/api/backup/saved/{link_name}")

        assert response.status_code == 400
        # The real file outside BACKUPS_DIR must still exist — the symlink
        # resolution must not have caused us to unlink the target.
        assert secret.exists()
        assert secret.read_text() == "do-not-delete-me"

    @pytest.mark.asyncio
    async def test_download_valid_filename_still_works(self, async_client, tmp_path):
        """Regression: the containment check must not break legitimate downloads."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        (backups_dir / "ecm-backup-2026-01-15_123456.yaml").write_text("legitimate")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.get(
                "/api/backup/saved/ecm-backup-2026-01-15_123456.yaml"
            )

        assert response.status_code == 200
        assert response.text == "legitimate"

    @pytest.mark.asyncio
    async def test_delete_valid_filename_still_works(self, async_client, tmp_path):
        """Regression: the containment check must not break legitimate deletes."""
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir()
        target = backups_dir / "ecm-backup-2026-01-15_123456.yaml"
        target.write_text("legitimate")

        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            response = await async_client.delete(
                "/api/backup/saved/ecm-backup-2026-01-15_123456.yaml"
            )

        assert response.status_code == 200
        assert not target.exists()


def _make_yaml_export(**overrides):
    """Create a YAML export string for testing.

    Returns bytes suitable for upload via async_client.
    """
    data = {
        "ecm_export": {"version": "0.16.0", "exported_at": "2026-01-01T00:00:00+00:00"},
        "settings": {"url": "http://test:9191", "username": "admin", "password": "***REDACTED***"},
        "database": {
            "scheduled_tasks": [
                {
                    "task_id": "stream_probe",
                    "task_name": "Stream Prober",
                    "description": "Probes streams",
                    "enabled": True,
                    "schedule_type": "manual",
                    "config": {"timeout": 30},
                },
            ],
            "task_schedules": [
                {
                    "task_id": "stream_probe",
                    "name": "Daily probe",
                    "enabled": True,
                    "schedule_type": "daily",
                    "schedule_time": "03:00",
                    "timezone": "US/Eastern",
                    "parameters": {"max_concurrent": 8},
                },
            ],
            "normalization_rule_groups": [
                {
                    "name": "Quality Tags",
                    "description": "Remove quality tags",
                    "enabled": True,
                    "priority": 1,
                    "is_builtin": False,
                    "rules": [
                        {
                            "name": "Strip HD",
                            "enabled": True,
                            "priority": 1,
                            "condition_type": "ends_with",
                            "condition_value": "HD",
                            "action_type": "remove",
                            "is_builtin": False,
                        },
                    ],
                },
            ],
            "tag_groups": [
                {
                    "name": "Countries",
                    "description": "Country tags",
                    "is_builtin": False,
                    "tags": [
                        {"value": "US", "case_sensitive": False, "enabled": True, "is_builtin": False},
                        {"value": "UK", "case_sensitive": False, "enabled": True, "is_builtin": False},
                    ],
                },
            ],
            "auto_creation_rules": [
                {
                    "name": "Sports Rule",
                    "enabled": True,
                    "priority": 1,
                    "conditions": [{"type": "group_name", "operator": "contains", "value": "Sports"}],
                    "actions": [{"type": "create_channel"}],
                    "run_on_refresh": False,
                    "stop_on_first_match": True,
                },
            ],
            "ffmpeg_profiles": [
                {"name": "Test Profile", "config": {"codec": "libx264", "bitrate": "5000k"}},
            ],
            "dummy_epg_profiles": [
                {
                    "name": "Sports EPG",
                    "enabled": True,
                    "name_source": "channel",
                    "stream_index": 1,
                    "title_pattern": "(?P<title>.+)",
                    "event_timezone": "US/Eastern",
                    "program_duration": 180,
                    "tvg_id_template": "ecm-{channel_number}",
                    "channel_assignments": [
                        {"channel_id": 1, "channel_name": "ESPN", "tvg_id_override": None},
                    ],
                },
            ],
        },
        "dispatcharr": {"_warning": "Dispatcharr not connected — section empty"},
    }
    data.update(overrides)
    return yaml.dump(data, default_flow_style=False).encode()


class TestValidateYaml:
    """Tests for POST /api/backup/validate."""

    @pytest.mark.asyncio
    async def test_validates_valid_yaml(self, async_client):
        """Returns section metadata with item counts for valid export."""
        content = _make_yaml_export()
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["version"] == "0.16.0"
        assert len(data["sections"]) == 13

        # Check a few sections
        by_key = {s["key"]: s for s in data["sections"]}
        assert by_key["settings"]["item_count"] == 3
        assert by_key["settings"]["available"] is True
        assert by_key["tag_groups"]["item_count"] == 1
        assert by_key["ffmpeg_profiles"]["item_count"] == 1
        assert by_key["scheduled_tasks"]["item_count"] == 1

    @pytest.mark.asyncio
    async def test_rejects_invalid_yaml(self, async_client):
        """Returns 400 for invalid YAML content."""
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("bad.yaml", b": : : {{{invalid", "text/yaml")},
        )
        assert response.status_code == 400
        assert "Invalid YAML" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_non_ecm_yaml(self, async_client):
        """Returns 400 for YAML without ecm_export header."""
        content = yaml.dump({"some_other": "data"}).encode()
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("other.yaml", content, "text/yaml")},
        )
        assert response.status_code == 400
        assert "missing ecm_export" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_sections_marked_unavailable(self, async_client):
        """Sections with no items are marked as unavailable."""
        content = _make_yaml_export(database={
            "scheduled_tasks": [],
            "task_schedules": [],
            "normalization_rule_groups": [],
            "tag_groups": [],
            "auto_creation_rules": [],
            "ffmpeg_profiles": [],
            "dummy_epg_profiles": [],
        })
        response = await async_client.post(
            "/api/backup/validate",
            files={"file": ("export.yaml", content, "text/yaml")},
        )
        assert response.status_code == 200
        data = response.json()
        by_key = {s["key"]: s for s in data["sections"]}
        for key in ("scheduled_tasks", "tag_groups", "ffmpeg_profiles"):
            assert by_key[key]["item_count"] == 0
            assert by_key[key]["available"] is False


class TestRestoreYaml:
    """Tests for POST /api/backup/restore-yaml."""

    @pytest.mark.asyncio
    async def test_restores_all_sections(self, async_client, test_session):
        """Restores all sections from a valid YAML export."""
        content = _make_yaml_export()
        all_sections = json.dumps([
            "settings", "scheduled_tasks", "task_schedules",
            "normalization_rule_groups", "tag_groups",
            "auto_creation_rules", "ffmpeg_profiles", "dummy_epg_profiles",
        ])

        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {"url": "", "username": "", "password": "existing_pass", "smtp_password": "existing_smtp"}

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.save_settings") as mock_save, \
             patch("routers.backup.clear_settings_cache"):
            response = await async_client.post(
                "/api/backup/restore-yaml",
                data={"sections": all_sections},
                files={"file": ("export.yaml", content, "text/yaml")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["sections_restored"]) == 8
        assert data["sections_failed"] == []

        # Verify DB records were created
        assert test_session.query(ScheduledTask).count() == 1
        assert test_session.query(TaskSchedule).count() == 1
        assert test_session.query(TagGroup).count() == 1
        assert test_session.query(Tag).count() == 2
        assert test_session.query(NormalizationRuleGroup).count() == 1
        assert test_session.query(NormalizationRule).count() == 1
        assert test_session.query(AutoCreationRule).count() == 1
        assert test_session.query(FFmpegProfile).count() == 1
        assert test_session.query(DummyEPGProfile).count() == 1
        assert test_session.query(DummyEPGChannelAssignment).count() == 1

    @pytest.mark.asyncio
    async def test_selective_restore(self, async_client, test_session):
        """Only restores selected sections."""
        content = _make_yaml_export()
        sections = json.dumps(["tag_groups", "ffmpeg_profiles"])

        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": sections},
            files={"file": ("export.yaml", content, "text/yaml")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert set(data["sections_restored"]) == {"tag_groups", "ffmpeg_profiles"}

        # Only selected sections should have data
        assert test_session.query(TagGroup).count() == 1
        assert test_session.query(FFmpegProfile).count() == 1
        # Unselected sections should be empty
        assert test_session.query(ScheduledTask).count() == 0
        assert test_session.query(AutoCreationRule).count() == 0

    @pytest.mark.asyncio
    async def test_settings_restore_preserves_redacted(self, async_client, test_session):
        """Redacted password fields are kept from existing settings."""
        content = _make_yaml_export()

        mock_settings = MagicMock()
        mock_settings.model_dump.return_value = {
            "url": "http://old:9191",
            "username": "old_user",
            "password": "real_password",
            "smtp_password": "real_smtp",
        }

        with patch("routers.backup.get_settings", return_value=mock_settings), \
             patch("routers.backup.save_settings") as mock_save, \
             patch("routers.backup.clear_settings_cache"):
            response = await async_client.post(
                "/api/backup/restore-yaml",
                data={"sections": json.dumps(["settings"])},
                files={"file": ("export.yaml", content, "text/yaml")},
            )

        assert response.status_code == 200
        data = response.json()
        assert "Skipped redacted field: password" in data["warnings"][0]

        # Verify save_settings was called with merged data
        saved = mock_save.call_args[0][0]
        assert saved.password == "real_password"
        assert saved.smtp_password == "real_smtp"
        assert saved.url == "http://test:9191"
        assert saved.username == "admin"

    @pytest.mark.asyncio
    async def test_partial_failure_handling(self, async_client, test_session):
        """Reports partial failures without aborting other sections."""
        content = _make_yaml_export()
        sections = json.dumps(["tag_groups", "auto_creation_rules"])

        # Make auto_creation_rules restore fail by patching the registry
        with patch.dict("routers.backup._SECTION_RESTORERS", {"auto_creation_rules": MagicMock(side_effect=Exception("DB error"))}):
            response = await async_client.post(
                "/api/backup/restore-yaml",
                data={"sections": sections},
                files={"file": ("export.yaml", content, "text/yaml")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "tag_groups" in data["sections_restored"]
        assert "auto_creation_rules" in data["sections_failed"]
        assert any("auto_creation_rules: DB error" in e for e in data["errors"])

        # tag_groups should still have been restored
        assert test_session.query(TagGroup).count() == 1

    @pytest.mark.asyncio
    async def test_channel_groups_restore_upserts_by_name(self):
        """Channel groups restore must NOT delete existing groups — channels and
        streams reference them by ID, so deleting would orphan those FKs. Only
        groups missing by name should be created."""
        from routers.backup import _restore_channel_groups

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "My Teams"},
            {"id": 2, "name": "Local"},
        ]

        items = [
            {"id": 99, "name": "My Teams"},
            {"id": 100, "name": "Local"},
            {"id": 101, "name": "Sports"},
        ]

        with patch("routers.backup.get_client", return_value=mock_client):
            result = await _restore_channel_groups(items)

        mock_client.delete_channel_group.assert_not_called()
        mock_client.create_channel_group.assert_called_once_with("Sports")
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_yaml(self, async_client):
        """Returns 400 for invalid YAML content."""
        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": json.dumps(["settings"])},
            files={"file": ("bad.yaml", b":: invalid yaml {{", "text/yaml")},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_unknown_sections(self, async_client):
        """Returns 400 for unknown section keys."""
        content = _make_yaml_export()
        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": json.dumps(["nonexistent_section"])},
            files={"file": ("export.yaml", content, "text/yaml")},
        )
        assert response.status_code == 400
        assert "Unknown sections" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_empty_sections(self, async_client):
        """Returns 400 when no sections selected."""
        content = _make_yaml_export()
        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": json.dumps([])},
            files={"file": ("export.yaml", content, "text/yaml")},
        )
        assert response.status_code == 400
        assert "at least one section" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_restore_replaces_existing_data(self, async_client, test_session):
        """Restore deletes existing records before recreating."""
        # Seed existing data
        test_session.add(TagGroup(name="OldGroup", is_builtin=False))
        test_session.add(FFmpegProfile(name="OldProfile", config='{}'))
        test_session.commit()

        assert test_session.query(TagGroup).count() == 1
        assert test_session.query(FFmpegProfile).count() == 1

        content = _make_yaml_export()
        response = await async_client.post(
            "/api/backup/restore-yaml",
            data={"sections": json.dumps(["tag_groups", "ffmpeg_profiles"])},
            files={"file": ("export.yaml", content, "text/yaml")},
        )

        assert response.status_code == 200
        # Old records replaced with new ones
        assert test_session.query(TagGroup).count() == 1
        assert test_session.query(TagGroup).first().name == "Countries"
        assert test_session.query(FFmpegProfile).count() == 1
        assert test_session.query(FFmpegProfile).first().name == "Test Profile"
