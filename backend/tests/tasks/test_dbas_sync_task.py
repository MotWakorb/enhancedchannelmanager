"""Tests for the scheduled/manual cross-instance SyncTask (bead 5gzg5, epic i39wu).

Covers ``tasks.dbas_sync.DbasSyncTask`` — the ``TaskScheduler`` wrapper that makes
the cross-instance sync ENGINE (``tasks.dbas_sync_engine.run_sync``) OPERATOR-
TRIGGERABLE: schedulable on an interval AND manually force-triggerable via the
generic ``POST /api/tasks/dbas_sync/run`` endpoint.

Two behavioral surfaces under test:

1. **Run path** — ``execute()`` resolves the configured ``sync_target_id``, fires
   the fire-time credential-freshness gate, and (on a fresh target) calls
   ``run_sync(target, confirm_apply=...)`` once. The returned
   :class:`RestoreReport` is mapped to a TRI-STATE ``TaskResult``:
     * clean ``SUCCESS`` (or a dry-run plan)  -> ``success=True``;
     * a mixed/rolled-back outcome             -> ``success=False`` (partial);
     * an exception in ``run_sync``            -> ``success=False`` (failed).

2. **Fire-time credential-freshness gate** (the security-critical part) —
   when the bound ``SyncTarget`` is missing / disabled / has a non-NULL
   ``token_revoked_at`` / has a rotated ``credential_version``, the task ABORTS
   the run WITHOUT ever calling ``run_sync`` (no remote client, no writes). The
   abort is NON-SILENT: WARN log + journal entry + NotificationCenter
   notification, and ``TaskResult.success`` is False (a skip, distinct from a
   run failure).

All DB access goes through an in-memory SQLite engine wired into the
``database`` module so the task's ``get_session()`` / ``journal.log_entry`` /
``create_notification_internal`` calls reach the test DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from dbas.restore_contracts import RestoreOutcome, RestoreReport
from export_models import SyncTarget
from models import JournalEntry, Notification
from task_scheduler import ScheduleConfig, ScheduleType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    """Point database._SessionLocal at the in-memory test engine so the task's
    direct get_session() / journal / notification calls hit it."""
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def _reset_metrics():
    """Fresh metric registry around each metric-asserting test (mirror the
    DbasBackupTask metric tests)."""
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


def _sync_counter_value(result_label: str) -> float:
    """Read the current ecm_sync_runs_total value for a result label."""
    counter = observability.get_metric("sync_runs_total")
    return counter.labels(result=result_label)._value.get()


def _make_target(session, **overrides) -> SyncTarget:
    fields = dict(
        name="dispatcharr-b",
        base_url="https://dispatcharr-b.example.com",
        credentials="{}",
        enabled=True,
        credential_version=1,
        token_revoked_at=None,
        insecure=False,
    )
    fields.update(overrides)
    target = SyncTarget(**fields)
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _success_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=False)
    report.outcome = RestoreOutcome.SUCCESS
    return report


def _partial_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=False)
    report.outcome = RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    return report


def _dry_run_report() -> RestoreReport:
    # A dry-run plan has no realized outcome (outcome stays None).
    return RestoreReport(is_dry_run=True)


# ---------------------------------------------------------------------------
# Registration / defaults
# ---------------------------------------------------------------------------


def test_task_is_registered():
    """The task must be importable via the registry under its task_id."""
    import tasks  # noqa: F401 — triggers @register_task side effects
    from task_registry import get_registry

    registry = get_registry()
    assert registry.is_registered("dbas_sync")
    assert registry.get_task_class("dbas_sync").default_enabled is False


def test_default_enabled_is_false():
    """Ships OFF by default — an operator opts into an interval schedule."""
    from tasks.dbas_sync import DbasSyncTask

    assert DbasSyncTask.default_enabled is False


def test_task_id_is_dbas_sync():
    from tasks.dbas_sync import DbasSyncTask

    assert DbasSyncTask.task_id == "dbas_sync"


def test_default_schedule_is_manual():
    """Default schedule is MANUAL — the operator opts into an interval."""
    from tasks.dbas_sync import DbasSyncTask

    task = DbasSyncTask()
    assert task.schedule_config.schedule_type == ScheduleType.MANUAL


def test_supports_interval_schedule():
    """The operator can opt into an INTERVAL schedule (ADR-013 S6)."""
    from tasks.dbas_sync import DbasSyncTask

    interval = DbasSyncTask(
        ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=3600)
    )
    assert interval.schedule_config.schedule_type == ScheduleType.INTERVAL


def test_dbas_sync_is_privileged_task():
    """Sync is an outbound-write op — it must be in PRIVILEGED_TASK_IDS so the
    generic /run endpoint is admin-gated (mirrors dbas_backup/dbas_restore)."""
    from routers.tasks import PRIVILEGED_TASK_IDS

    assert "dbas_sync" in PRIVILEGED_TASK_IDS


# ---------------------------------------------------------------------------
# Config / params
# ---------------------------------------------------------------------------


def test_confirm_apply_defaults_to_false():
    """Dry-run preview is the default; apply (source-wins) is opt-in."""
    from tasks.dbas_sync import DbasSyncTask

    task = DbasSyncTask()
    assert task.confirm_apply is False


def test_update_config_sets_target_and_apply():
    from tasks.dbas_sync import DbasSyncTask

    task = DbasSyncTask()
    task.update_config(
        {"sync_target_id": 42, "confirm_apply": True, "cloud_credential_version": 3}
    )
    assert task.sync_target_id == 42
    assert task.confirm_apply is True
    assert task.cloud_credential_version == 3

    cfg = task.get_config()
    assert cfg["sync_target_id"] == 42
    assert cfg["confirm_apply"] is True
    assert cfg["cloud_credential_version"] == 3


# ---------------------------------------------------------------------------
# Run path — report -> tri-state TaskResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_calls_run_sync_with_target_and_confirm_apply(_wire_db):
    """execute() resolves the configured target and calls run_sync once with the
    configured confirm_apply; a SUCCESS report maps to success=True."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    captured = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        captured["target_id"] = sync_target.id
        captured["confirm_apply"] = confirm_apply
        captured["session_passed"] = session is not None
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync) as mock_run:
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id, "confirm_apply": True})
        result = await task.execute()

    assert mock_run.await_count == 1
    assert captured["target_id"] == target_id
    assert captured["confirm_apply"] is True
    assert captured["session_passed"] is True
    assert result.success is True
    assert result.details["outcome"] == "success"


@pytest.mark.asyncio
async def test_execute_dry_run_default_maps_to_success(_wire_db):
    """confirm_apply defaults False -> run_sync called with confirm_apply=False;
    a dry-run report (no outcome) maps to a successful TaskResult."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    captured = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        captured["confirm_apply"] = confirm_apply
        return _dry_run_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        result = await task.execute()

    assert captured["confirm_apply"] is False
    assert result.success is True
    assert result.details["is_dry_run"] is True


@pytest.mark.asyncio
async def test_execute_mixed_report_maps_to_partial(_wire_db):
    """A mixed/rolled-back apply outcome -> success=False (partial), NOT a clean
    success (tri-state discipline: never SUCCESS on mixed state)."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        return _partial_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id, "confirm_apply": True})
        result = await task.execute()

    assert result.success is False
    assert result.details["outcome"] == "partial_failed_rolled_back"


@pytest.mark.asyncio
async def test_execute_exception_maps_to_failed(_wire_db):
    """An exception/abort inside run_sync -> success=False (failed), sanitized."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _boom(sync_target, *, confirm_apply=False, session=None, **_kw):
        raise RuntimeError("remote-b unreachable")

    with patch.object(dbas_sync, "run_sync", side_effect=_boom):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        result = await task.execute()

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_no_target_configured_fails_without_run_sync(_wire_db):
    """No sync_target_id configured -> a clean failure, run_sync never called."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        task = DbasSyncTask()
        result = await task.execute()

    assert mock_run.await_count == 0
    assert result.success is False


# ---------------------------------------------------------------------------
# Fire-time credential-freshness gate — abort WITHOUT calling run_sync
# ---------------------------------------------------------------------------


async def _run_with_target_overrides(_wire_db, **target_overrides):
    """Seed a SyncTarget (with overrides), run the task with a captured version
    of 1, and assert the gate aborts BEFORE run_sync. Returns (result, called)."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session, **target_overrides)
    target_id = target.id
    session.close()

    mock_run = AsyncMock()
    with patch.object(dbas_sync, "run_sync", new=mock_run):
        task = DbasSyncTask()
        # Captured credential_version is 1 (the freshly-created target's value);
        # a rotation override (credential_version=2) trips the gate.
        task.update_config(
            {"sync_target_id": target_id, "cloud_credential_version": 1}
        )
        result = await task.execute()

    return result, mock_run.await_count


@pytest.mark.asyncio
async def test_gate_aborts_when_target_disabled(_wire_db):
    result, called = await _run_with_target_overrides(_wire_db, enabled=False)
    assert called == 0, "gate must abort BEFORE calling run_sync"
    assert result.success is False
    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"


@pytest.mark.asyncio
async def test_gate_aborts_when_token_revoked(_wire_db):
    from datetime import datetime, timezone

    result, called = await _run_with_target_overrides(
        _wire_db, token_revoked_at=datetime.now(timezone.utc)
    )
    assert called == 0
    assert result.success is False
    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"


@pytest.mark.asyncio
async def test_gate_aborts_when_credential_version_mismatch(_wire_db):
    # Target now at v2; the task captured v1 -> rotation -> abort.
    result, called = await _run_with_target_overrides(_wire_db, credential_version=2)
    assert called == 0
    assert result.success is False
    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"


@pytest.mark.asyncio
async def test_gate_aborts_when_target_missing(_wire_db):
    """A configured target id that no longer exists -> abort, no run_sync."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    mock_run = AsyncMock()
    with patch.object(dbas_sync, "run_sync", new=mock_run):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": 999999, "cloud_credential_version": 1})
        result = await task.execute()

    assert mock_run.await_count == 0
    assert result.success is False
    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"


@pytest.mark.asyncio
async def test_gate_abort_is_non_silent(_wire_db):
    """A freshness-gate abort must journal AND notify (not a silent stop)."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session, enabled=False)
    target_id = target.id
    session.close()

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()):
        task = DbasSyncTask()
        task.update_config(
            {"sync_target_id": target_id, "cloud_credential_version": 1}
        )
        await task.execute()

    session = _wire_db()
    try:
        journals = session.query(JournalEntry).all()
        notifications = session.query(Notification).all()
    finally:
        session.close()

    assert any(j.category == "sync_outbound" for j in journals), (
        "abort must leave a sync_outbound journal row"
    )
    assert len(notifications) >= 1, "abort must emit an operator notification"


@pytest.mark.asyncio
async def test_fresh_target_passes_gate_and_runs(_wire_db):
    """A fresh target whose captured version matches proceeds to run_sync."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session, credential_version=5)
    target_id = target.id
    session.close()

    mock_run = AsyncMock(return_value=_success_report())
    with patch.object(dbas_sync, "run_sync", new=mock_run):
        task = DbasSyncTask()
        task.update_config(
            {"sync_target_id": target_id, "cloud_credential_version": 5}
        )
        result = await task.execute()

    assert mock_run.await_count == 1
    assert result.success is True


# ---------------------------------------------------------------------------
# Metric: ecm_sync_runs_total{result} — tri-state {success, partial, failed}
# ---------------------------------------------------------------------------


def test_sync_runs_total_is_registered(_reset_metrics):
    """The tri-state run-outcome counter must be registered (mirrors
    ecm_backup_runs_total)."""
    counter = observability.get_metric("sync_runs_total")
    # Bounded label set — each result label materializes its own series.
    for result in ("success", "partial", "failed"):
        assert counter.labels(result=result)._value.get() == 0.0


@pytest.mark.asyncio
async def test_success_run_bumps_success_metric(_wire_db, _reset_metrics):
    """A clean SUCCESS apply increments ecm_sync_runs_total{result="success"}
    exactly once (and nothing else)."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id, "confirm_apply": True})
        result = await task.execute()

    assert result.success is True
    assert _sync_counter_value("success") == 1.0
    assert _sync_counter_value("partial") == 0.0
    assert _sync_counter_value("failed") == 0.0


@pytest.mark.asyncio
async def test_dry_run_bumps_success_metric(_wire_db, _reset_metrics):
    """A dry-run preview (no realized outcome) is a clean success for metric
    purposes — it produced a plan."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        return _dry_run_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        await task.execute()

    assert _sync_counter_value("success") == 1.0
    assert _sync_counter_value("partial") == 0.0
    assert _sync_counter_value("failed") == 0.0


@pytest.mark.asyncio
async def test_partial_run_bumps_partial_metric(_wire_db, _reset_metrics):
    """A mixed/rolled-back apply increments {result="partial"} — NOT success
    (tri-state discipline). A sustained partial loop is what also keeps the
    last-success gauge stale and trips ECMSyncStalledTargetDrift."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, *, confirm_apply=False, session=None, **_kw):
        return _partial_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id, "confirm_apply": True})
        result = await task.execute()

    assert result.success is False
    assert _sync_counter_value("partial") == 1.0
    assert _sync_counter_value("success") == 0.0
    assert _sync_counter_value("failed") == 0.0


@pytest.mark.asyncio
async def test_exception_run_bumps_failed_metric(_wire_db, _reset_metrics):
    """An exception inside run_sync increments {result="failed"}."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _boom(sync_target, *, confirm_apply=False, session=None, **_kw):
        raise RuntimeError("remote-b unreachable")

    with patch.object(dbas_sync, "run_sync", side_effect=_boom):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        result = await task.execute()

    assert result.success is False
    assert _sync_counter_value("failed") == 1.0
    assert _sync_counter_value("success") == 0.0
    assert _sync_counter_value("partial") == 0.0


@pytest.mark.asyncio
async def test_no_target_bumps_failed_metric(_wire_db, _reset_metrics):
    """No sync_target_id configured -> {result="failed"}, run_sync never called."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        task = DbasSyncTask()
        result = await task.execute()

    assert mock_run.await_count == 0
    assert result.success is False
    assert _sync_counter_value("failed") == 1.0


@pytest.mark.asyncio
async def test_freshness_abort_bumps_failed_metric(_wire_db, _reset_metrics):
    """A credential-freshness abort increments {result="failed"} — a target
    that drifted because no sync was applied."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask

    session = _wire_db()
    target = _make_target(session, enabled=False)
    target_id = target.id
    session.close()

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        task = DbasSyncTask()
        task.update_config(
            {"sync_target_id": target_id, "cloud_credential_version": 1}
        )
        result = await task.execute()

    assert mock_run.await_count == 0
    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"
    assert _sync_counter_value("failed") == 1.0
    assert _sync_counter_value("success") == 0.0
    assert _sync_counter_value("partial") == 0.0
