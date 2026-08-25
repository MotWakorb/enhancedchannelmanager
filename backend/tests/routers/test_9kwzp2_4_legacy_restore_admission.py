"""Legacy ZIP settings and journal admission regressions (9kwzp.2, 9kwzp.4)."""

import io
import json
import sqlite3
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from routers import backup as backup_mod


def _sqlite_bytes(tmp_path: Path, tables: tuple[str, ...]) -> bytes:
    path = tmp_path / ("-".join(tables) + ".db")
    connection = sqlite3.connect(path)
    try:
        for table in tables:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()
    return path.read_bytes()


def _legacy_zip(*, settings: object | None = None, journal: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        files = []
        if settings is not None:
            zf.writestr("settings.json", json.dumps(settings))
            files.append("settings.json")
        if journal is not None:
            zf.writestr("journal.db", journal)
            files.append("journal.db")
        zf.writestr(
            "ecm_backup.json",
            json.dumps({"version": "0.15.0", "files": files}),
        )
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_invalid_known_setting_is_rejected_before_close_or_live_write(
    async_client, tmp_path
):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"url": "http://live:9191"}))
    journal_path = tmp_path / "journal.db"
    journal_path.write_bytes(b"live journal sentinel")
    artifact = _legacy_zip(
        settings={"linked_m3u_accounts": "not-a-list"},
        journal=_sqlite_bytes(tmp_path, ("journal_entries", "scheduled_tasks")),
    )

    with (
        patch.object(backup_mod, "CONFIG_DIR", tmp_path),
        patch.object(backup_mod, "CONFIG_FILE", settings_path),
        patch.object(backup_mod, "JOURNAL_DB_FILE", journal_path),
        patch.object(backup_mod, "close_db") as close_db,
    ):
        response = await async_client.post(
            "/api/backup/restore",
            files={"file": ("backup.zip", artifact, "application/zip")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Backup contains invalid settings.json"
    close_db.assert_not_called()
    assert json.loads(settings_path.read_text()) == {"url": "http://live:9191"}
    assert journal_path.read_bytes() == b"live journal sentinel"


def test_settings_merge_preserves_destination_mcp_key_and_drops_unknown_keys(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "url": "http://live:9191",
                "mcp_api_key": "destination-key",
                "obsolete_destination_key": "drop-me",
            }
        )
    )
    archived = json.dumps(
        {
            "url": "http://restored:9191",
            "mcp_api_key": "attacker-key",
            "unknown_archive_key": "drop-me-too",
            "max_auto_created_channels_per_run": -7,
        }
    ).encode()

    with patch.object(backup_mod, "CONFIG_FILE", settings_path):
        restored = json.loads(backup_mod._merge_settings_preserving_redacted(archived))

    assert restored["url"] == "http://restored:9191"
    assert restored["mcp_api_key"] == "destination-key"
    assert restored["max_auto_created_channels_per_run"] == 0
    assert "unknown_archive_key" not in restored
    assert "obsolete_destination_key" not in restored


def test_schema_mismatched_sqlite_is_rejected_before_close_db(tmp_path):
    archive = tmp_path / "wrong-schema.zip"
    archive.write_bytes(
        _legacy_zip(journal=_sqlite_bytes(tmp_path, ("users",)))
    )

    with zipfile.ZipFile(archive) as zf, patch.object(backup_mod, "close_db") as close_db:
        with pytest.raises(backup_mod.HTTPException) as exc:
            backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup contains incompatible journal.db"
    close_db.assert_not_called()


def test_legacy_baseline_schema_is_accepted_without_current_full_schema(tmp_path):
    archive = tmp_path / "old-valid.zip"
    archive.write_bytes(
        _legacy_zip(
            journal=_sqlite_bytes(tmp_path, ("journal_entries", "scheduled_tasks"))
        )
    )

    with zipfile.ZipFile(archive) as zf:
        manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "0.15.0"


def test_journal_validation_streams_to_private_temp_and_opens_read_only(tmp_path):
    archive = tmp_path / "valid.zip"
    archive.write_bytes(
        _legacy_zip(
            journal=_sqlite_bytes(tmp_path, ("journal_entries", "scheduled_tasks"))
        )
    )
    real_connect = sqlite3.connect
    observed_temp: Path | None = None

    def checked_connect(database, *args, **kwargs):
        nonlocal observed_temp
        assert kwargs.get("uri") is True
        assert str(database).endswith("?mode=ro")
        observed_temp = Path(str(database).removeprefix("file:").removesuffix("?mode=ro"))
        assert stat.S_IMODE(observed_temp.stat().st_mode) == 0o600
        return real_connect(database, *args, **kwargs)

    with zipfile.ZipFile(archive) as zf:
        original_read = zf.read

        def reject_whole_journal_read(name, *args, **kwargs):
            if name == "journal.db":
                raise AssertionError("journal.db was read wholesale")
            return original_read(name, *args, **kwargs)

        with (
            patch.object(zf, "read", side_effect=reject_whole_journal_read),
            patch.object(backup_mod.sqlite3, "connect", side_effect=checked_connect),
        ):
            backup_mod._validate_backup_zip(zf)

    assert observed_temp is not None
    assert not observed_temp.exists()
