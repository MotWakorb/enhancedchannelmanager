"""Legacy ZIP settings and journal admission regressions (9kwzp.2, 9kwzp.4)."""

import errno
import io
import json
import sqlite3
import stat
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from routers import backup as backup_mod


_BASELINE_SCHEMA = {
    "journal_entries": ("timestamp", "category", "action_type", "entity_name", "description"),
    "scheduled_tasks": ("task_id", "task_name", "enabled", "schedule_type"),
    "auto_creation_rules": ("name", "enabled", "priority", "conditions", "actions"),
}


def _sqlite_bytes(tmp_path: Path, tables: tuple[str, ...], *, baseline_columns: bool = False) -> bytes:
    path = tmp_path / ("-".join(tables) + ".db")
    connection = sqlite3.connect(path)
    try:
        for table in tables:
            columns = ["id INTEGER PRIMARY KEY"]
            if baseline_columns:
                columns.extend(f'"{name}" TEXT' for name in _BASELINE_SCHEMA.get(table, ()))
            connection.execute(f'CREATE TABLE "{table}" ({", ".join(columns)})')
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
        journal=_sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True),
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


def test_historical_nulls_and_legacy_api_key_use_loader_compatibility(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"mcp_api_key": "destination-key"}))
    archived = json.dumps(
        {
            "url": "http://restored:9191",
            "user_timezone": None,
            "stats_poll_interval": None,
            "api_key": "legacy-dispatcharr-key",
            "mcp_api_key": "artifact-key",
        }
    ).encode()

    with patch.object(backup_mod, "CONFIG_FILE", settings_path):
        restored = json.loads(backup_mod._merge_settings_preserving_redacted(archived))

    assert restored["user_timezone"] == ""
    assert restored["stats_poll_interval"] == 10
    assert restored["dispatcharr_api_key"] == "legacy-dispatcharr-key"
    assert restored["api_key"] == "legacy-dispatcharr-key"
    assert restored["mcp_api_key"] == "destination-key"


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
            journal=_sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True)
        )
    )

    with zipfile.ZipFile(archive) as zf:
        manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "0.15.0"


def test_table_names_without_historical_columns_are_rejected(tmp_path):
    archive = tmp_path / "unusable-schema.zip"
    archive.write_bytes(_legacy_zip(journal=_sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA))))

    with zipfile.ZipFile(archive) as zf, pytest.raises(backup_mod.HTTPException) as exc:
        backup_mod._validate_backup_zip(zf)

    assert exc.value.detail == "Backup contains incompatible journal.db"


def test_corrupt_sqlite_with_readable_schema_is_rejected(tmp_path):
    path = tmp_path / "corrupt-source.db"
    path.write_bytes(
        _sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True)
    )
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE payloads (data BLOB)")
        connection.executemany(
            "INSERT INTO payloads VALUES (?)", [(b"x" * 3000,)] * 20
        )
        connection.commit()
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    finally:
        connection.close()
    journal = bytearray(path.read_bytes())
    journal[-page_size:] = b"\0" * page_size
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(_legacy_zip(journal=bytes(journal)))

    with zipfile.ZipFile(archive) as zf, pytest.raises(backup_mod.HTTPException) as exc:
        backup_mod._validate_backup_zip(zf)

    assert exc.value.detail == "Backup contains invalid journal.db"


def test_journal_validation_streams_to_private_temp_and_opens_read_only(tmp_path):
    archive = tmp_path / "valid.zip"
    archive.write_bytes(
        _legacy_zip(
            journal=_sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True)
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
            plan = backup_mod._validate_backup_zip(zf)
            plan.close()

    assert observed_temp is not None
    assert not observed_temp.exists()


def test_restore_installs_validated_journal_inode_after_path_substitution(tmp_path):
    original = _sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True)
    replacement = _sqlite_bytes(tmp_path, ("users",))
    archive = tmp_path / "valid.zip"
    archive.write_bytes(_legacy_zip(journal=original))
    live = tmp_path / "live-journal.db"

    with zipfile.ZipFile(archive) as zf:
        plan = backup_mod._validate_backup_zip(zf)
        staged_path = plan.staged_paths["journal.db"]
        staged_path.unlink()
        staged_path.write_bytes(replacement)
        with (
            patch.object(backup_mod, "JOURNAL_DB_FILE", live),
            patch.object(backup_mod, "CONFIG_DIR", tmp_path),
            patch.object(backup_mod, "close_db"),
            patch.object(backup_mod, "init_db"),
            patch.object(backup_mod, "clear_settings_cache"),
            patch.object(backup_mod, "reset_client"),
            patch.object(backup_mod, "_capture_existing_alert_method_configs", return_value={}),
            patch.object(backup_mod, "_capture_existing_auth_rows", return_value={}),
            patch.object(backup_mod, "_count_reestablish_rows", return_value={}),
        ):
            backup_mod._restore_from_zip(zf, plan)
        plan.close()

    assert live.read_bytes() == original


def test_current_standard_zip_producer_is_admitted_and_restored(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    source_settings = source / "settings.json"
    source_settings.write_text(json.dumps({"url": "http://source:9191"}))
    source_journal = source / "journal.db"
    source_journal.write_bytes(
        _sqlite_bytes(tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True)
    )
    with sqlite3.connect(source_journal) as connection:
        connection.execute(
            "INSERT INTO scheduled_tasks "
            "(task_id, task_name, enabled, schedule_type) VALUES (?, ?, ?, ?)",
            ("backup", "Backup", "1", "manual"),
        )
        connection.commit()

    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("checkpoint unavailable in fixture")
    with (
        patch.object(backup_mod, "CONFIG_DIR", source),
        patch.object(backup_mod, "CONFIG_FILE", source_settings),
        patch.object(backup_mod, "JOURNAL_DB_FILE", source_journal),
        patch.object(backup_mod, "get_engine", return_value=engine),
    ):
        artifact = backup_mod._create_backup_zip().getvalue()

    archive = tmp_path / "current-standard.zip"
    archive.write_bytes(artifact)
    extracted = tmp_path / "produced-journal.db"
    with zipfile.ZipFile(archive) as zf:
        extracted.write_bytes(zf.read("journal.db"))
    with sqlite3.connect(extracted) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "journal_entries" not in tables
    assert {"scheduled_tasks", "auto_creation_rules"}.issubset(tables)

    destination = tmp_path / "destination"
    destination.mkdir()
    destination_settings = destination / "settings.json"
    destination_journal = destination / "journal.db"
    with zipfile.ZipFile(archive) as zf:
        with patch.object(backup_mod, "CONFIG_DIR", destination):
            plan = backup_mod._validate_backup_zip(zf)
        try:
            with (
                patch.object(backup_mod, "CONFIG_DIR", destination),
                patch.object(backup_mod, "CONFIG_FILE", destination_settings),
                patch.object(backup_mod, "JOURNAL_DB_FILE", destination_journal),
                patch.object(backup_mod, "close_db"),
                patch.object(backup_mod, "init_db"),
                patch.object(backup_mod, "clear_settings_cache"),
                patch.object(backup_mod, "reset_client"),
            ):
                restored = backup_mod._restore_from_zip(zf, plan)
        finally:
            plan.close()

    assert {"settings.json", "journal.db"}.issubset(restored)
    with sqlite3.connect(destination_journal) as connection:
        assert connection.execute(
            "SELECT task_name FROM scheduled_tasks WHERE task_id='backup'"
        ).fetchone() == ("Backup",)


def _transactional_restore_fixture(tmp_path: Path):
    live = tmp_path / "live"
    live.mkdir()
    settings = live / "settings.json"
    settings.write_text(json.dumps({"url": "http://live:9191"}))
    journal = live / "journal.db"
    journal_bytes = _sqlite_bytes(
        tmp_path, tuple(_BASELINE_SCHEMA), baseline_columns=True
    )
    journal.write_bytes(journal_bytes)
    members = {
        "uploads/logos/logo.png": b"new logo",
        "tls/key.pem": b"new key",
        "m3u_uploads/list.m3u": b"new playlist",
    }
    old = {}
    for name, content in {
        "uploads/logos/old.png": b"old logo",
        "tls/old.pem": b"old key",
        "m3u_uploads/old.m3u": b"old playlist",
    }.items():
        path = live / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        old[name] = content

    archive = tmp_path / "transaction.zip"
    archive.write_bytes(
        _legacy_zip(
            settings={"url": "http://restored:9191"},
            journal=journal_bytes,
        )
    )
    with zipfile.ZipFile(archive, "a") as zf:
        for name, content in members.items():
            zf.writestr(name, content)

    return live, settings, journal, old, archive


def _assert_prior_live_state(live, settings, journal, old, prior_journal):
    assert json.loads(settings.read_text()) == {"url": "http://live:9191"}
    assert journal.read_bytes() == prior_journal
    for name, content in old.items():
        assert (live / name).read_bytes() == content
    assert not (live / "uploads/logos/logo.png").exists()
    assert not (live / "tls/key.pem").exists()
    assert not (live / "m3u_uploads/list.m3u").exists()


@pytest.mark.parametrize(
    "failed_target",
    ["settings.json", "journal.db", "uploads/logos", "tls", "m3u_uploads"],
)
def test_restore_replace_failure_rolls_back_every_live_artifact(
    tmp_path, failed_target
):
    live, settings, journal, old, archive = _transactional_restore_fixture(tmp_path)
    prior_journal = journal.read_bytes()
    real_replace = backup_mod.os.replace
    failed = False

    def fail_one_install(source, destination):
        nonlocal failed
        destination = Path(destination)
        if not failed and destination == live / failed_target:
            failed = True
            raise PermissionError("injected replace failure")
        return real_replace(source, destination)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(backup_mod, "CONFIG_DIR", live):
            plan = backup_mod._validate_backup_zip(zf)
        try:
            with (
                patch.object(backup_mod, "CONFIG_DIR", live),
                patch.object(backup_mod, "CONFIG_FILE", settings),
                patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
                patch.object(backup_mod.os, "replace", side_effect=fail_one_install),
                patch.object(backup_mod, "close_db") as close_db,
                patch.object(backup_mod, "init_db") as init_db,
                patch.object(backup_mod, "clear_settings_cache"),
                patch.object(backup_mod, "reset_client"),
                pytest.raises(PermissionError, match="injected replace failure"),
            ):
                backup_mod._restore_from_zip(zf, plan)
        finally:
            plan.close()

    assert failed
    expected_db_cycles = 2 if failed_target == "settings.json" else 1
    assert close_db.call_count == expected_db_cycles
    assert init_db.call_count == expected_db_cycles
    _assert_prior_live_state(live, settings, journal, old, prior_journal)


@pytest.mark.parametrize("failed_member", ["journal.db", "tls/key.pem"])
def test_restore_staging_copy_failure_never_touches_live_state(tmp_path, failed_member):
    live, settings, journal, old, archive = _transactional_restore_fixture(tmp_path)
    prior_journal = journal.read_bytes()
    real_copy = backup_mod.shutil.copyfileobj

    def fail_selected_copy(source, destination, length=0):
        staged_name = next(
            name for name, handle in plan._files.items() if handle.fileno() == source.fileno()
        )
        if staged_name == failed_member:
            raise OSError("injected copy failure")
        return real_copy(source, destination, length)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(backup_mod, "CONFIG_DIR", live):
            plan = backup_mod._validate_backup_zip(zf)
        try:
            with (
                patch.object(backup_mod, "CONFIG_DIR", live),
                patch.object(backup_mod, "CONFIG_FILE", settings),
                patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
                patch.object(backup_mod.shutil, "copyfileobj", side_effect=fail_selected_copy),
                patch.object(backup_mod, "close_db") as close_db,
                patch.object(backup_mod, "init_db") as init_db,
                pytest.raises(OSError, match="injected copy failure"),
            ):
                backup_mod._restore_from_zip(zf, plan)
        finally:
            plan.close()

    close_db.assert_not_called()
    init_db.assert_not_called()
    _assert_prior_live_state(live, settings, journal, old, prior_journal)


def test_restore_init_failure_rolls_back_then_reinitializes_prior_database(tmp_path):
    live, settings, journal, old, archive = _transactional_restore_fixture(tmp_path)
    prior_journal = journal.read_bytes()

    with zipfile.ZipFile(archive) as zf:
        with patch.object(backup_mod, "CONFIG_DIR", live):
            plan = backup_mod._validate_backup_zip(zf)
        try:
            with (
                patch.object(backup_mod, "CONFIG_DIR", live),
                patch.object(backup_mod, "CONFIG_FILE", settings),
                patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
                patch.object(backup_mod, "close_db") as close_db,
                patch.object(
                    backup_mod,
                    "init_db",
                    side_effect=[RuntimeError("injected init failure"), None],
                ) as init_db,
                patch.object(backup_mod, "clear_settings_cache"),
                patch.object(backup_mod, "reset_client"),
                pytest.raises(RuntimeError, match="injected init failure"),
            ):
                backup_mod._restore_from_zip(zf, plan)
        finally:
            plan.close()

    assert close_db.call_count == 2
    assert init_db.call_count == 2
    _assert_prior_live_state(live, settings, journal, old, prior_journal)


@pytest.mark.parametrize(
    "failure",
    [OSError(errno.ENOSPC, "injected disk full"), KeyboardInterrupt()],
)
def test_restore_disk_full_or_interruption_compensates_live_state(tmp_path, failure):
    live, settings, journal, old, archive = _transactional_restore_fixture(tmp_path)
    prior_journal = journal.read_bytes()
    real_replace = backup_mod.os.replace
    failed = False

    def interrupt_settings_install(source, destination):
        nonlocal failed
        if not failed and Path(destination) == settings:
            failed = True
            raise failure
        return real_replace(source, destination)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(backup_mod, "CONFIG_DIR", live):
            plan = backup_mod._validate_backup_zip(zf)
        try:
            with (
                patch.object(backup_mod, "CONFIG_DIR", live),
                patch.object(backup_mod, "CONFIG_FILE", settings),
                patch.object(backup_mod, "JOURNAL_DB_FILE", journal),
                patch.object(
                    backup_mod.os, "replace", side_effect=interrupt_settings_install
                ),
                patch.object(backup_mod, "close_db"),
                patch.object(backup_mod, "init_db"),
                pytest.raises(type(failure)),
            ):
                backup_mod._restore_from_zip(zf, plan)
        finally:
            plan.close()

    assert failed
    _assert_prior_live_state(live, settings, journal, old, prior_journal)
