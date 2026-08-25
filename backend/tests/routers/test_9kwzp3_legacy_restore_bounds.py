"""Regression coverage for bounded legacy ZIP restore ingestion (9kwzp.3)."""

import io
import json
import sqlite3
import stat
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from starlette.datastructures import UploadFile

from routers import backup as backup_mod

from .test_backup import _minimal_journal_db_bytes


def _legacy_zip(*, journal: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ecm_backup.json",
            json.dumps({"version": "1.0", "files": ["journal.db"]}),
        )
        zf.writestr(
            "journal.db",
            journal if journal is not None else _minimal_journal_db_bytes(),
        )
    return buffer.getvalue()


def _compressible_journal_bytes(tmp_path) -> bytes:
    path = tmp_path / "compressible-journal.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "category TEXT, action_type TEXT, entity_name TEXT, description TEXT)"
        )
        connection.execute(
            "CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, task_id TEXT, "
            "task_name TEXT, enabled INTEGER, schedule_type TEXT)"
        )
        connection.execute(
            "CREATE TABLE auto_creation_rules (id INTEGER PRIMARY KEY, name TEXT, "
            "enabled INTEGER, priority INTEGER, conditions TEXT, actions TEXT)"
        )
        connection.execute("CREATE TABLE payloads (data BLOB)")
        connection.execute("INSERT INTO payloads VALUES (zeroblob(?))", (1024 * 1024,))
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_legacy_restore_streams_through_upload_cap_and_cleans_partial_file(
    tmp_path, initial
):
    payload = _legacy_zip()
    settings = type("Settings", (), {"is_configured": lambda self: False})()

    with (
        patch.object(backup_mod, "_RESTORE_MAX_UPLOAD_BYTES", 32),
        patch.object(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path),
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "_guard_initial_restore", new=AsyncMock()),
        patch.object(backup_mod, "_restore_from_zip", return_value=[]),
    ):
        upload = UploadFile(io.BytesIO(payload), filename="backup.zip")
        with pytest.raises(backup_mod.HTTPException) as exc:
            if initial:
                await backup_mod.restore_backup_initial(
                    request=object(), file=upload, session=object()
                )
            else:
                await backup_mod.restore_backup(file=upload, _admin=None)

    assert exc.value.status_code == 413
    assert exc.value.detail == "Uploaded artifact is too large"
    assert list(tmp_path.iterdir()) == []


def test_legacy_validator_rejects_high_ratio_member_before_any_read(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("journal.db", b"0" * (2 * 1024 * 1024))

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


@pytest.mark.parametrize("member_name", ["journal.db", "m3u_uploads/playlist.m3u"])
def test_legacy_validator_accepts_ecm_shaped_high_ratio_member(tmp_path, member_name):
    """Only legacy SQLite/M3U data may exceed the DBAS 100x ratio cap."""
    archive = tmp_path / "legacy-high-ratio.zip"
    content = (
        _compressible_journal_bytes(tmp_path)
        if member_name == "journal.db"
        else b"playlist-line-title-channel-name-operator-visible-metadata\n" * (1024 * 1024 // 57)
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr(member_name, content)

    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo(member_name)
        assert 292 < info.file_size / info.compress_size < 650
        manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "1.0"


def test_legacy_validator_keeps_100x_ratio_for_other_members_before_any_read(tmp_path):
    """The legacy allowance is not a blanket relaxation for arbitrary members."""
    archive = tmp_path / "non-legacy-data-bomb.zip"
    content = b"tls-certificate-fixture-metadata-not-a-secret\n" * (1024 * 1024 // 46)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tls/cert.pem", content)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


@pytest.mark.parametrize(
    ("members", "limit_name", "limit"),
    [
        ([("one", b"x"), ("two", b"x")], "_ARTIFACT_MAX_ENTRIES", 1),
        ([("oversized", b"x" * 65)], "_ARTIFACT_MAX_MEMBER_UNCOMPRESSED", 64),
        (
            [("first", b"x" * 48), ("second", b"x" * 48)],
            "_ARTIFACT_MAX_TOTAL_UNCOMPRESSED",
            64,
        ),
    ],
)
def test_legacy_validator_enforces_declared_bounds_before_any_read(
    tmp_path, monkeypatch, members, limit_name, limit
):
    """Legacy compatibility does not relax entry or declared-size controls."""
    archive = tmp_path / "bounded.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)
    monkeypatch.setattr(backup_mod, limit_name, limit)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


def test_legacy_validator_streams_journal_in_bounded_chunks(tmp_path):
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("journal.db", _minimal_journal_db_bytes())

    with zipfile.ZipFile(archive) as zf:
        original_open = zf.open
        journal_reads: list[int] = []

        def tracked_open(name, *args, **kwargs):
            member = original_open(name, *args, **kwargs)
            if name != "journal.db":
                return member

            class TrackedMember:
                def __enter__(self):
                    member.__enter__()
                    return self

                def __exit__(self, *exc):
                    return member.__exit__(*exc)

                def read(self, size=-1):
                    journal_reads.append(size)
                    return member.read(size)

            return TrackedMember()

        with patch.object(zf, "open", side_effect=tracked_open):
            manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "1.0"
    assert journal_reads == [backup_mod._RESTORE_UPLOAD_CHUNK] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["restore", "restore-initial", "restore-saved"])
async def test_legacy_restore_endpoints_never_whole_read_archive_or_payload_members(
    async_client, tmp_path, endpoint
):
    artifact = _legacy_zip()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    filename = "ecm-backup-2026-01-01_000000.zip"
    (backups_dir / filename).write_bytes(artifact)

    def guarded_read(self, name, *args, **kwargs):
        raise AssertionError(f"whole-member read: {name}")

    saved_path = backups_dir / filename
    real_path_read_bytes = type(saved_path).read_bytes

    def guarded_path_read_bytes(self):
        if self == saved_path:
            raise AssertionError("whole saved archive read")
        return real_path_read_bytes(self)

    settings = type("Settings", (), {"is_configured": lambda self: False})()
    with (
        patch.object(zipfile.ZipFile, "read", guarded_read),
        patch.object(type(saved_path), "read_bytes", guarded_path_read_bytes),
        patch.object(backup_mod, "CONFIG_DIR", config_dir),
        patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"),
        patch.object(backup_mod, "JOURNAL_DB_FILE", config_dir / "journal.db"),
        patch.object(backup_mod, "BACKUPS_DIR", backups_dir),
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "_guard_initial_restore", new=AsyncMock()),
        patch.object(backup_mod, "close_db"),
        patch.object(backup_mod, "init_db"),
        patch.object(backup_mod, "clear_settings_cache"),
        patch.object(backup_mod, "reset_client"),
    ):
        if endpoint == "restore-saved":
            response = await async_client.post(
                "/api/backup/restore-saved", json={"filename": filename}
            )
        else:
            response = await async_client.post(
                f"/api/backup/{endpoint}",
                files={"file": ("backup.zip", artifact, "application/zip")},
            )

    assert response.status_code == 200, response.text


def test_validation_workspace_is_private_and_retains_staged_inode(tmp_path):
    archive = tmp_path / "backup.zip"
    archive.write_bytes(_legacy_zip())

    with zipfile.ZipFile(archive) as zf:
        plan = backup_mod._validate_backup_zip(zf)
        staged = plan.staged_paths["journal.db"]
        assert stat.S_IMODE(staged.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600
        assert plan.staged_inodes["journal.db"] == staged.stat().st_ino
        plan.close()


def test_dbas_manifest_cap_is_checked_from_zipinfo_before_open(tmp_path, monkeypatch):
    archive = tmp_path / "oversized-manifest.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": 1, "padding": "x" * 128, "files": []}),
        )
    monkeypatch.setattr(backup_mod, "_MAX_DBAS_MANIFEST_BYTES", 64)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "open", side_effect=AssertionError("member opened")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod.validate_artifact_manifest(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid backup manifest"


def test_dbas_manifest_parser_never_uses_unbounded_member_read(tmp_path):
    archive = tmp_path / "manifest.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 1, "files": []}))

    with zipfile.ZipFile(archive) as zf:
        original_open = zf.open
        reads = []

        def tracked_open(name, *args, **kwargs):
            member = original_open(name, *args, **kwargs)
            if getattr(name, "filename", name) != "manifest.json":
                return member

            class TrackedMember:
                def __enter__(self):
                    member.__enter__()
                    return self

                def __exit__(self, *exc):
                    return member.__exit__(*exc)

                def read(self, size=-1):
                    reads.append(size)
                    assert size >= 0
                    return member.read(size)

            return TrackedMember()

        with patch.object(zf, "open", side_effect=tracked_open):
            backup_mod.validate_artifact_manifest(zf)

    assert reads


@pytest.mark.asyncio
async def test_initial_restore_rejects_oversized_settings_before_open_or_shutdown(
    async_client, tmp_path, monkeypatch
):
    artifact = io.BytesIO()
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("settings.json", json.dumps({"padding": "x" * 128}))
    monkeypatch.setattr(backup_mod, "_MAX_LEGACY_SETTINGS_BYTES", 64)
    settings = type("Settings", (), {"is_configured": lambda self: False})()
    real_open = zipfile.ZipFile.open

    def reject_settings_open(self, name, *args, **kwargs):
        if name == "settings.json":
            raise AssertionError("oversized settings member opened")
        return real_open(self, name, *args, **kwargs)

    with (
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "_guard_initial_restore", new=AsyncMock()),
        patch.object(zipfile.ZipFile, "open", reject_settings_open),
        patch.object(backup_mod, "close_db") as close_db,
    ):
        response = await async_client.post(
            "/api/backup/restore-initial",
            files={"file": ("backup.zip", artifact.getvalue(), "application/zip")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Backup contains invalid settings.json"
    close_db.assert_not_called()


def test_settings_cap_retains_multi_megabyte_operator_configuration(tmp_path):
    archive = tmp_path / "large-settings.zip"
    settings = json.dumps({"url": "http://test:9191"}).encode() + b" " * (2 * 1024 * 1024)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("settings.json", settings)

    with zipfile.ZipFile(archive) as zf:
        plan = backup_mod._validate_backup_zip(zf)
        plan.close()


def test_staged_settings_parser_never_uses_unbounded_read(tmp_path):
    archive = tmp_path / "settings.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("settings.json", json.dumps({"url": "http://test:9191"}))

    with zipfile.ZipFile(archive) as zf:
        plan = backup_mod._validate_backup_zip(zf)
        original = plan._files["settings.json"]
        reads = []

        class TrackedStagedFile:
            def read(self, size=-1):
                reads.append(size)
                assert size >= 0
                return original.read(size)

            def seek(self, *args):
                return original.seek(*args)

            def close(self):
                return original.close()

        plan._files["settings.json"] = TrackedStagedFile()
        try:
            assert plan.load_json(
                "settings.json", backup_mod._MAX_LEGACY_SETTINGS_BYTES
            )["url"] == "http://test:9191"
        finally:
            plan.close()

    assert reads
