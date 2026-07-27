"""Tests for the DBAS scheduled/manual backup task (bead 0i2vt.6).

Covers ``tasks.dbas_backup.DbasBackupTask`` — the scheduled + manually
triggerable producer of the new-format DBAS artifact (the ``.7`` builder,
``routers.backup.build_backup_artifact``).

Two behavioral surfaces under test:

1. **Build path** — ``execute()`` invokes ``build_backup_artifact`` with
   ``dest_dir`` pointing at ``CONFIG_DIR / "backups"`` so the sealed ZIP +
   ``.sha256`` sidecar land in the backups dir (not a temp dir). On success
   the ``TaskResult`` carries filename / schema_version / sha256 / file_count
   and increments ``ecm_backup_runs_total{result="success"}``. A build raise
   maps to ``result="failed"`` + ``success=False``.

2. **Fire-time credential-freshness gate** (the security-critical part) —
   when the task config carries a ``cloud_target_id``, the task re-reads the
   ``CloudStorageTarget`` FRESH from the DB at fire time and ABORTS (no
   artifact built) if the target is missing, disabled, has a non-NULL
   ``token_revoked_at``, or its ``credential_version`` no longer matches the
   captured ``cloud_credential_version``. Every abort is NON-SILENT: WARN log,
   journal entry, NotificationCenter notification, and
   ``ecm_backup_runs_total{result="skipped"}``. ``TaskResult.success`` is
   False on a skip — distinct from a build failure.

All DB access goes through an in-memory SQLite engine wired into the
``database`` module so the task's ``get_session()`` / ``journal.log_entry`` /
``create_notification_internal`` calls reach the test DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from export_models import CloudStorageTarget
from models import JournalEntry, Notification
from routers.backup import BackupArtifact
from task_scheduler import ScheduleConfig, ScheduleType, TaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    """Point database._SessionLocal at the in-memory test engine so the
    task's direct get_session() / journal / notification calls hit it."""
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def _reset_metrics():
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


def _counter_value(result_label: str) -> float:
    """Read the current ecm_backup_runs_total value for a result label."""
    counter = observability.get_metric("backup_runs_total")
    return counter.labels(result=result_label)._value.get()


def _make_target(session, **overrides) -> CloudStorageTarget:
    fields = dict(
        name="primary-s3",
        provider_type="s3",
        credentials="{}",
        upload_path="/backups",
        enabled=True,
        credential_version=1,
        token_revoked_at=None,
    )
    fields.update(overrides)
    target = CloudStorageTarget(**fields)
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _fake_artifact(dest_dir: Path) -> BackupArtifact:
    """Materialize a fake sealed artifact + sidecar in dest_dir so the
    happy-path assertions can confirm files actually land there."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "ecm-artifact-abc123.zip"
    sidecar_path = Path(str(zip_path) + ".sha256")
    zip_path.write_bytes(b"PK\x03\x04fake-sealed-zip")
    sidecar_path.write_text("deadbeef  %s\n" % zip_path.name)
    return BackupArtifact(
        zip_path=zip_path,
        sidecar_path=sidecar_path,
        schema_version=1,
        sha256="deadbeef",
        file_count=7,
    )


# ---------------------------------------------------------------------------
# Registration / defaults
# ---------------------------------------------------------------------------


def test_task_is_registered():
    """The task must be importable via the registry under its task_id."""
    import tasks  # noqa: F401 — triggers @register_task side effects
    from task_registry import get_registry

    registry = get_registry()
    assert registry.is_registered("dbas_backup")
    assert registry.get_task_class("dbas_backup").default_enabled is False


def test_default_enabled_is_false():
    """PO decision: ships OFF by default."""
    from tasks.dbas_backup import DbasBackupTask

    assert DbasBackupTask.default_enabled is False


def test_task_id_is_dbas_backup():
    from tasks.dbas_backup import DbasBackupTask

    assert DbasBackupTask.task_id == "dbas_backup"


def test_supports_cron_and_interval_schedules():
    """The task must accept CRON and INTERVAL schedules like its siblings."""
    from tasks.dbas_backup import DbasBackupTask

    cron = DbasBackupTask(
        ScheduleConfig(schedule_type=ScheduleType.CRON, cron_expression="0 3 * * *")
    )
    assert cron.schedule_config.schedule_type == ScheduleType.CRON

    interval = DbasBackupTask(
        ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=86400)
    )
    assert interval.schedule_config.schedule_type == ScheduleType.INTERVAL


# ---------------------------------------------------------------------------
# Happy path — artifact build, no cloud target configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_builds_artifact_in_backups_dir(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    captured = {}

    async def _fake_build(dest_dir=None, **_kwargs):
        captured["dest_dir"] = dest_dir
        return _fake_artifact(dest_dir)

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        task = DbasBackupTask()
        result = await task.execute()

    assert result.success is True
    # dest_dir routed to the backups dir, not a temp dir
    assert Path(captured["dest_dir"]) == backups_dir
    # artifact + sidecar actually present in the backups dir
    zips = list(backups_dir.glob("*.zip"))
    assert len(zips) == 1
    assert (backups_dir / (zips[0].name + ".sha256")).exists()

    # details carry the artifact metadata
    assert result.details["schema_version"] == 1
    assert result.details["sha256"] == "deadbeef"
    assert result.details["file_count"] == 7
    assert result.details["filename"] == zips[0].name

    assert _counter_value("success") == 1.0
    assert _counter_value("skipped") == 0.0
    assert _counter_value("failed") == 0.0


@pytest.mark.asyncio
async def test_no_cloud_target_config_runs_normally(
    _wire_db, _reset_metrics, tmp_path
):
    """When cloud_target_id is None the freshness gate is a no-op."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir)

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        task = DbasBackupTask()
        task.update_config({"cloud_target_id": None})
        result = await task.execute()

    assert result.success is True
    assert _counter_value("success") == 1.0


# ---------------------------------------------------------------------------
# Build failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_failure_maps_to_failed(_wire_db, _reset_metrics, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _boom(dest_dir=None):
        raise OSError("no space left on device")

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_boom
    ):
        task = DbasBackupTask()
        result = await task.execute()

    assert result.success is False
    assert result.error
    assert _counter_value("failed") == 1.0
    assert _counter_value("success") == 0.0


# ---------------------------------------------------------------------------
# Fire-time credential-freshness gate — each abort condition
# ---------------------------------------------------------------------------


async def _run_with_gate(
    dbas_backup_mod, task_cls, test_session, backups_dir, config
):
    """Run execute() with the build mocked, asserting it never builds when
    the gate aborts. Returns (result, build_called)."""
    build_called = {"v": False}

    async def _fake_build(dest_dir=None, **_kwargs):
        build_called["v"] = True
        return _fake_artifact(dest_dir)

    notify = AsyncMock(return_value={"id": 1})
    with patch.object(dbas_backup_mod, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup_mod, "build_backup_artifact", side_effect=_fake_build
    ), patch.object(dbas_backup_mod, "create_notification_internal", notify):
        task = task_cls()
        task.update_config(config)
        result = await task.execute()
    return result, build_called["v"], notify


def _assert_non_silent_skip(test_session, result, build_called, notify):
    """A freshness-gate abort must: not build, return success=False, WARN
    (caller checks caplog), write a journal entry, post a notification,
    and increment result='skipped'."""
    assert build_called is False, "gate must abort BEFORE building an artifact"
    assert result.success is False
    # journal entry written
    entries = (
        test_session.query(JournalEntry)
        .filter(JournalEntry.category == "backup")
        .all()
    )
    assert len(entries) >= 1
    # notification posted (warning)
    assert notify.await_count >= 1
    kwargs = notify.await_args.kwargs
    assert kwargs.get("notification_type") == "warning"
    # metric
    assert _counter_value("skipped") >= 1.0
    assert _counter_value("success") == 0.0


@pytest.mark.asyncio
async def test_gate_aborts_when_target_missing(
    _wire_db, _reset_metrics, test_session, tmp_path, caplog
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    # No target with id=999 exists.
    with caplog.at_level("WARNING"):
        result, build_called, notify = await _run_with_gate(
            dbas_backup,
            DbasBackupTask,
            test_session,
            backups_dir,
            {"cloud_target_id": 999, "cloud_credential_version": 1},
        )

    _assert_non_silent_skip(test_session, result, build_called, notify)
    assert any("[DBAS_BACKUP]" in r.message for r in caplog.records)
    assert not list(backups_dir.glob("*.zip"))


@pytest.mark.asyncio
async def test_gate_aborts_when_target_disabled(
    _wire_db, _reset_metrics, test_session, tmp_path, caplog
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    target = _make_target(test_session, enabled=False, credential_version=1)

    with caplog.at_level("WARNING"):
        result, build_called, notify = await _run_with_gate(
            dbas_backup,
            DbasBackupTask,
            test_session,
            backups_dir,
            {"cloud_target_id": target.id, "cloud_credential_version": 1},
        )

    _assert_non_silent_skip(test_session, result, build_called, notify)
    assert any("[DBAS_BACKUP]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_gate_aborts_when_token_revoked(
    _wire_db, _reset_metrics, test_session, tmp_path, caplog
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    target = _make_target(
        test_session,
        token_revoked_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        credential_version=1,
    )

    with caplog.at_level("WARNING"):
        result, build_called, notify = await _run_with_gate(
            dbas_backup,
            DbasBackupTask,
            test_session,
            backups_dir,
            {"cloud_target_id": target.id, "cloud_credential_version": 1},
        )

    _assert_non_silent_skip(test_session, result, build_called, notify)


@pytest.mark.asyncio
async def test_gate_aborts_when_credential_version_mismatch(
    _wire_db, _reset_metrics, test_session, tmp_path, caplog
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    # Target rotated to v2; schedule captured v1.
    target = _make_target(test_session, credential_version=2)

    with caplog.at_level("WARNING"):
        result, build_called, notify = await _run_with_gate(
            dbas_backup,
            DbasBackupTask,
            test_session,
            backups_dir,
            {"cloud_target_id": target.id, "cloud_credential_version": 1},
        )

    _assert_non_silent_skip(test_session, result, build_called, notify)


@pytest.mark.asyncio
async def test_gate_passes_when_target_fresh(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    """A healthy, enabled, non-revoked, version-matching target builds."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    target = _make_target(test_session, enabled=True, credential_version=3)

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir)

    notify = AsyncMock(return_value={"id": 1})
    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ), patch.object(dbas_backup, "create_notification_internal", notify):
        task = DbasBackupTask()
        task.update_config(
            {"cloud_target_id": target.id, "cloud_credential_version": 3}
        )
        result = await task.execute()

    assert result.success is True
    assert _counter_value("success") == 1.0
    assert _counter_value("skipped") == 0.0


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------


def test_config_round_trip():
    from tasks.dbas_backup import DbasBackupTask

    task = DbasBackupTask()
    task.update_config({"cloud_target_id": 5, "cloud_credential_version": 9})
    cfg = task.get_config()
    assert cfg["cloud_target_id"] == 5
    assert cfg["cloud_credential_version"] == 9
