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

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


# How many categories the stubbed builder claims to have gathered. A fixed
# stand-in for len(RESTORABLE_SECTIONS) so these tests pin the counts ARITHMETIC
# (bead …-fexq1) without churning every time a category is added.
_GATHERED_CATEGORIES = 16


def _fake_artifact(
    dest_dir: Path,
    *,
    degraded_categories=None,
    unresolved_epg_links=0,
    epg_index_truncated=False,
    gathered_categories=_GATHERED_CATEGORIES,
) -> BackupArtifact:
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
        gathered_categories=gathered_categories,
        degraded_categories=degraded_categories,
        unresolved_epg_links=unresolved_epg_links,
        epg_index_truncated=epg_index_truncated,
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
    # zt3kf — a clean gather (no degraded categories) reports a coherent
    # clean-success count, and details carries no degraded_categories key.
    # …-fexq1: the counts are CATEGORIES now, so a clean run reports every one
    # of them as archived rather than the placeholder "1 item".
    assert result.total_items == _GATHERED_CATEGORIES
    assert result.success_count == _GATHERED_CATEGORIES
    assert result.failed_count == 0
    assert "degraded_categories" not in result.details

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
# Degraded gather severity (enhancedchannelmanager-zt3kf)
#
# When build_backup_artifact returns an artifact whose Dispatcharr gather
# stubbed one or more categories, the task must report a WARNING-level result
# (success=True, failed_count>0) naming the degraded categories in
# details["degraded_categories"] — never a clean SUCCESS. This is what makes
# task_engine.py's existing "Completed with Warnings" branch reachable for a
# degraded DBAS backup (mirrors the tyei5/PR #766 dbas_restore counts-wiring
# pattern; see test_task_notification_formatting.py for the message-naming
# proof at the notification layer).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_gather_is_warning_not_clean_success(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir, degraded_categories=["dvr_rules"])

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        task = DbasBackupTask()
        result = await task.execute()

    # WARNING-level: still an overall success (a real artifact WAS built and
    # verified), but failed_count>0 makes task_engine.py's existing
    # "Completed with Warnings" branch reachable — never a hard failure.
    assert result.success is True
    assert result.failed_count == 1
    # …-fexq1: the fifteen categories that DID archive are counted. This read
    # "0 ok, 1 failed" for a real, restorable artifact.
    assert result.success_count == _GATHERED_CATEGORIES - 1
    assert result.total_items == _GATHERED_CATEGORIES
    assert result.details["degraded_categories"] == ["dvr_rules"]
    assert "dvr_rules" in result.message

    # The build itself still counts as a clean local success for the metric —
    # this is about GATHER completeness, not local-artifact/upload health.
    assert _counter_value("success") == 1.0
    assert _counter_value("failed") == 0.0


@pytest.mark.asyncio
async def test_degraded_gather_names_multiple_categories(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(
            dest_dir, degraded_categories=["core_settings", "dvr_rules"]
        )

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        task = DbasBackupTask()
        result = await task.execute()

    assert result.success is True
    # …-fexq1: two degraded categories are two failed items, not one degraded
    # run rounded to a boolean.
    assert result.failed_count == 2
    assert result.success_count == _GATHERED_CATEGORIES - 2
    assert result.details["degraded_categories"] == ["core_settings", "dvr_rules"]
    assert "core_settings" in result.message
    assert "dvr_rules" in result.message


@pytest.mark.asyncio
async def test_clean_gather_is_unaffected_by_degraded_wiring(
    _wire_db, _reset_metrics, tmp_path
):
    """Regression guard: a clean (empty) degraded_categories list must not
    perturb the existing clean-success envelope."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir, degraded_categories=[])

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        task = DbasBackupTask()
        result = await task.execute()

    assert result.success is True
    assert result.failed_count == 0
    assert result.success_count == _GATHERED_CATEGORIES
    assert "degraded_categories" not in result.details


@pytest.mark.asyncio
async def test_total_client_unavailability_reaches_warning_level_end_to_end(
    _wire_db, _reset_metrics, tmp_path
):
    """PR #770 review BLOCK, reviewer's exact repro: get_client() returning
    None (total Dispatcharr unavailability, BEFORE any per-category fetch)
    must not silently produce a clean success. Runs the REAL
    build_backup_artifact (not a fake artifact, unlike every other test in
    this file) so the detection bug in _gather_redacted_categories is
    exercised for real end to end, then the REAL artifact flows through the
    REAL DbasBackupTask.execute()."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask
    from routers import backup as backup_mod

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    journal = config_dir / "journal.db"
    # A REAL, empty SQLite database. The magic-byte stub this used to write is
    # not a database: sqlite3 opens it and then fails on the first query. That
    # was invisible while the journal.db scrub failed OPEN and shipped the raw
    # copy; since bead …-gi4zn the scrub fails CLOSED and an unreadable source
    # fails the whole backup, which is not what this test is about. A live
    # instance always has a real database here, so the faithful fixture is the
    # one that matches production. Same change, same reason, as
    # ``tests/routers/test_backup.py::_write_empty_journal_db``.
    sqlite3.connect(str(journal)).close()
    settings_file = config_dir / "settings.json"
    settings_file.write_text("{}")

    mock_settings = MagicMock()
    mock_settings.model_dump.return_value = {"url": "http://test:9191"}

    mock_engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (0, 0, 0)
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    backups_dir = tmp_path / "backups"

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", settings_file), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=mock_engine), \
         patch.object(backup_mod, "get_settings", return_value=mock_settings), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=None), \
         patch.object(dbas_backup, "BACKUPS_DIR", backups_dir):
        task = DbasBackupTask()
        result = await task.execute()

    all_dispatcharr_keys = sorted(
        k for k, v in backup_mod.RESTORABLE_SECTIONS.items() if v.get("dispatcharr")
    )
    # Every requested Dispatcharr category is degraded, NOT a silent
    # empty list — this is the reviewer's "worst possible input" scenario.
    assert result.details["degraded_categories"] == all_dispatcharr_keys
    # WARNING-level, never a clean silent success.
    assert result.success is True
    # …-fexq1, measured against the REAL section list rather than a stub: every
    # Dispatcharr-backed category is a failed item, and the LOCAL categories
    # that archived fine from the DB are counted as the successes they are.
    # Reporting "0 ok" here said the whole artifact was worthless when the
    # settings, rules and profiles inside it had all come through.
    assert result.failed_count == len(all_dispatcharr_keys)
    assert result.total_items == len(backup_mod.RESTORABLE_SECTIONS)
    assert result.success_count == (
        len(backup_mod.RESTORABLE_SECTIONS) - len(all_dispatcharr_keys)
    )
    assert result.success_count > 0


@pytest.mark.asyncio
async def test_degraded_gather_reaches_completed_with_warnings_notification(
    _wire_db, _reset_metrics, tmp_path, test_session
):
    """Engine-level seam proof (zt3kf): a degraded-gather TaskResult, run
    through the REAL TaskEngine (not a synthetic task), produces exactly ONE
    completion notification — a WARNING naming the degraded category — not a
    green success and not a hard failure. Mirrors the engine-level class in
    test_channel_pipeline_task_failed_action_notify.py."""
    from tasks import dbas_backup
    from task_engine import TaskEngine
    from models import ScheduledTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir, degraded_categories=["dvr_rules"])

    test_session.query(ScheduledTask).filter(
        ScheduledTask.task_id == "dbas_backup"
    ).delete()
    test_session.add(ScheduledTask(
        task_id="dbas_backup",
        task_name="DBAS Backup",
        description="test",
        enabled=True,
        schedule_type="manual",
        send_alerts=True,
        alert_on_warning=True,
        show_notifications=True,
    ))
    test_session.commit()

    engine = TaskEngine()
    notify = AsyncMock(return_value={"id": 1})
    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ), patch("services.notification_service.create_notification_internal", new=notify):
        await engine._execute_task(task_id="dbas_backup", triggered_by="test")

    assert notify.await_count == 1
    kwargs = notify.await_args.kwargs
    assert kwargs["notification_type"] == "warning"
    assert kwargs["title"].startswith("Task Completed with Warnings")
    assert "dvr_rules" in kwargs["message"]
    assert kwargs["send_alerts"] is True


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


# ---------------------------------------------------------------------------
# Declared run parameters match what the task reads (bead …-sdpzy)
# ---------------------------------------------------------------------------


def test_declared_run_parameters_are_the_ones_update_config_reads():
    """The declaration is only worth publishing if it cannot drift.

    ``run_parameter_schema`` is what GET /api/tasks/dbas_backup/parameter-schema
    tells an operator to send. Each declared name must actually change task
    state when passed through ``update_config`` — the same path the run endpoint
    uses for ad-hoc parameters.
    """
    from tasks.dbas_backup import DbasBackupTask

    declared = [p["name"] for p in DbasBackupTask.run_parameter_schema["parameters"]]
    assert declared == [
        "passphrase",
        "include_credentials",
        "acknowledge_unrecoverable",
    ]

    task = DbasBackupTask()
    task.update_config({
        "passphrase": "correct horse battery",
        "include_credentials": True,
        "acknowledge_unrecoverable": True,
    })
    assert task.passphrase == "correct horse battery"
    assert task.include_credentials is True
    assert task.acknowledge_unrecoverable is True


def test_run_parameters_are_absent_from_the_persisted_config():
    """They are manual-run transients — get_config() is what reaches journal.db."""
    from tasks.dbas_backup import DbasBackupTask

    declared = {p["name"] for p in DbasBackupTask.run_parameter_schema["parameters"]}
    task = DbasBackupTask()
    task.update_config({"passphrase": "correct horse battery"})

    assert declared.isdisjoint(task.get_config().keys())


# ---------------------------------------------------------------------------
# Encryption transients are ONE-SHOT (bead …-cytzj)
# ---------------------------------------------------------------------------
#
# passphrase / include_credentials / acknowledge_unrecoverable are manual-run
# transients: get_config() deliberately omits them so nothing is persisted to
# journal.db. But the task is a LIVE SINGLETON, so without an explicit reset a
# single manual "Create Encrypted Backup" made every LATER run in the same
# process — including an unattended SCHEDULED one — produce an encrypted,
# credential-bearing artifact under that one-off passphrase. The task's own
# stated invariant is "A SCHEDULED run therefore always produces the default
# redact-by-default backup."


def _build_arg_recorder(backups_dir, calls):
    """A build_backup_artifact stand-in that records the encryption kwargs."""

    async def _fake_build(dest_dir=None, **kwargs):
        calls.append({
            "passphrase": kwargs.get("passphrase"),
            "include_credentials": kwargs.get("include_credentials"),
            "acknowledge_unrecoverable": kwargs.get("acknowledge_unrecoverable"),
        })
        return _fake_artifact(dest_dir)

    return _fake_build


@pytest.mark.asyncio
async def test_encrypted_manual_run_does_not_contaminate_the_next_run(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    calls: list[dict] = []

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact",
        side_effect=_build_arg_recorder(backups_dir, calls),
    ):
        task = DbasBackupTask()

        # Run 1 — plain scheduled run: redact-by-default.
        await task.execute()
        # Manual encrypted, credential-carrying export.
        task.update_config({
            "passphrase": "correct horse battery staple",
            "include_credentials": True,
            "acknowledge_unrecoverable": True,
        })
        await task.execute()
        # Run 3 — nothing else changed. MUST be back to the default.
        await task.execute()

    assert calls[0] == {
        "passphrase": None,
        "include_credentials": False,
        "acknowledge_unrecoverable": False,
    }
    assert calls[1] == {
        "passphrase": "correct horse battery staple",
        "include_credentials": True,
        "acknowledge_unrecoverable": True,
    }
    assert calls[2] == {
        "passphrase": None,
        "include_credentials": False,
        "acknowledge_unrecoverable": False,
    }


@pytest.mark.asyncio
async def test_transients_reset_even_when_the_run_raises(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _boom(dest_dir=None, **_kwargs):
        raise OSError("no space left on device")

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_boom
    ):
        task = DbasBackupTask()
        task.update_config({
            "passphrase": "one-shot",
            "include_credentials": True,
            "acknowledge_unrecoverable": True,
        })
        result = await task.execute()

    assert result.success is False
    assert task.passphrase is None
    assert task.include_credentials is False
    assert task.acknowledge_unrecoverable is False


@pytest.mark.asyncio
async def test_transients_reset_when_the_freshness_gate_aborts_the_run(
    _wire_db, _reset_metrics, tmp_path
):
    """The gate returns BEFORE the build, so its early return must reset too."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir):
        task = DbasBackupTask()
        # No CloudStorageTarget with this id exists -> gate aborts with a SKIP.
        task.update_config({
            "cloud_target_id": 4242,
            "cloud_credential_version": 1,
            "passphrase": "one-shot",
            "include_credentials": True,
            "acknowledge_unrecoverable": True,
        })
        result = await task.execute()

    assert result.success is False
    assert task.passphrase is None
    assert task.include_credentials is False
    assert task.acknowledge_unrecoverable is False


@pytest.mark.asyncio
async def test_get_config_never_carries_the_encryption_transients(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"
    calls: list[dict] = []

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact",
        side_effect=_build_arg_recorder(backups_dir, calls),
    ):
        task = DbasBackupTask()
        task.update_config({
            "passphrase": "never-persist-me",
            "include_credentials": True,
            "acknowledge_unrecoverable": True,
        })
        before = task.get_config()
        await task.execute()
        after = task.get_config()

    for cfg in (before, after):
        assert "passphrase" not in cfg
        assert "include_credentials" not in cfg
        assert "acknowledge_unrecoverable" not in cfg
    assert "never-persist-me" not in repr(before) + repr(after)


# ---------------------------------------------------------------------------
# Unresolved channel EPG links (bead dfkbn, PR review W2)
#
# A channel whose epg_data_id points at a guide row that no longer exists cannot
# have its link restored. The operator should be able to SEE that on the backup
# rather than discover it in a restore report. It is deliberately NOT a WARNING:
# a dangling FK is common and largely unactionable, and spending the
# "Completed with Warnings" badge on it would train the operator to ignore the
# badge that a failed category fetch depends on.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unresolved_epg_links_are_surfaced_without_crying_wolf(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir, unresolved_epg_links=3)

    with patch.object(dbas_backup, "BACKUPS_DIR", tmp_path / "backups"), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        result = await DbasBackupTask().execute()

    assert result.details["unresolved_epg_links"] == 3
    assert "3 channel EPG link(s)" in result.message
    assert "no longer exists" in result.message
    # Visible, but NOT a warning: no failed_count, no degraded category.
    assert result.success is True
    assert result.failed_count == 0
    assert "degraded_categories" not in result.details
    assert "epg_index_truncated" not in result.details


@pytest.mark.asyncio
async def test_a_truncated_guide_read_is_named_as_such_not_as_dangling(
    _wire_db, _reset_metrics, tmp_path
):
    """Truncation and a dangling reference are different diagnoses (W2)."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(
            dest_dir, unresolved_epg_links=5, epg_index_truncated=True
        )

    with patch.object(dbas_backup, "BACKUPS_DIR", tmp_path / "backups"), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        result = await DbasBackupTask().execute()

    assert result.details["unresolved_epg_links"] == 5
    assert result.details["epg_index_truncated"] is True
    assert "row ceiling" in result.message
    # It must NOT assert those links are dangling — it does not know that.
    assert "no longer exists" not in result.message


@pytest.mark.asyncio
async def test_a_clean_backup_says_nothing_about_epg_links(
    _wire_db, _reset_metrics, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact(dest_dir)

    with patch.object(dbas_backup, "BACKUPS_DIR", tmp_path / "backups"), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        result = await DbasBackupTask().execute()

    assert "unresolved_epg_links" not in result.details
    assert "EPG link" not in result.message
