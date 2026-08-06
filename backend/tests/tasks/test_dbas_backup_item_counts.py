"""A degraded backup's counts describe ITEMS, not a boolean.

Bead ``enhancedchannelmanager-fexq1``, option (b) — the half left open when
option (a) made the Journal line lead with its severity.

THE DEFECT
----------
``zt3kf`` wired a degraded gather into the existing "Completed with Warnings"
branch by setting ``total_items=1`` with ``success_count``/``failed_count`` in
``{0, 1}`` — a BOOLEAN wearing the shape of item counts. So a backup that
archived fifteen categories cleanly and stubbed one wrote::

    Journal:      Completed DBAS Backup: 0 ok, 1 failed
    History row:  1 total   0 ok   1 failed
    Progress:     Completed: 0 ok, 1 failed

``0 ok`` for a run that produced a real, checksum-verified, restorable
artifact. Option (a) fixed the row's severity wording; it did not fix the
numbers, and ``0 ok`` still reads as total failure at a glance — the same
"reads confusing at a glance" failure mode ``zt3kf`` exists to fix, one
surface over.

THE MODEL
---------
The item is a CATEGORY, which is what the gather actually iterates and what
``degraded_categories`` already counts. Parity with ``dbas_restore``'s
``RestoreCounts``, which derives its counts from the report's per-category
numbers rather than inventing a scalar:

    total_items   = categories the artifact gathered
    failed_count  = len(degraded_categories)
    success_count = total_items - failed_count

SEVERITY MUST NOT MOVE
----------------------
``task_scheduler``'s severity ladder keys on ``result.success`` and
``result.failed_count``, so changing what ``failed_count`` MEANS could move a
backup's severity as a side effect. It does not, and these tests pin it: the
predicate is still ``failed_count > 0``, and ``failed_count > 0`` still holds
exactly when ``degraded_categories`` is non-empty. A clean backup stays
``success``; a degraded one stays ``warning``; an upload failure stays
``error``.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from routers.backup import BackupArtifact
from task_scheduler import completion_notification_type

# The count the real builder reports for a full gather is len(RESTORABLE_SECTIONS);
# the tests use a fixed stand-in so they pin the ARITHMETIC, not the section list
# (which grows whenever a category is added and would otherwise churn this file).
_GATHERED = 16


@pytest.fixture
def _reset_metrics():
    """The task bumps ``ecm_backup_runs_total``; keep runs independent."""
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    """Point database._SessionLocal at the in-memory test engine."""
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _artifact(
    dest_dir: Path,
    *,
    degraded_categories=None,
    gathered_categories: int | None = _GATHERED,
) -> BackupArtifact:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "ecm-backup-2026-08-06_120000.zip"
    sidecar_path = Path(str(zip_path) + ".sha256")
    zip_path.write_bytes(b"PK\x03\x04fake-sealed-zip")
    sidecar_path.write_text("deadbeef  %s\n" % zip_path.name)
    kwargs = {}
    if gathered_categories is not None:
        kwargs["gathered_categories"] = gathered_categories
    return BackupArtifact(
        zip_path=zip_path,
        sidecar_path=sidecar_path,
        schema_version=1,
        sha256="deadbeef",
        file_count=41,
        degraded_categories=degraded_categories,
        **kwargs,
    )


async def _run(tmp_path, **artifact_kwargs):
    """Execute the task with a stubbed builder; return the TaskResult."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _artifact(dest_dir, **artifact_kwargs)

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ):
        return await DbasBackupTask().execute()


# ---------------------------------------------------------------------------
# 1. The numbers mean items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_degraded_backup_counts_the_categories_that_did_archive(
    _wire_db, _reset_metrics, tmp_path
):
    """THE regression: fifteen categories archived is not "0 ok"."""
    result = await _run(tmp_path, degraded_categories=["dvr_rules"])

    assert result.total_items == _GATHERED
    assert result.failed_count == 1
    assert result.success_count == _GATHERED - 1
    # The specific thing an operator saw and misread.
    assert result.success_count > 0


@pytest.mark.asyncio
async def test_every_degraded_category_is_counted_as_one_failed_item(
    _wire_db, _reset_metrics, tmp_path
):
    """Two stubbed categories are two failed items, not one degraded run."""
    result = await _run(
        tmp_path, degraded_categories=["dvr_rules", "epg_sources", "logos"]
    )

    assert result.total_items == _GATHERED
    assert result.failed_count == 3
    assert result.success_count == _GATHERED - 3


@pytest.mark.asyncio
async def test_a_clean_backup_counts_every_category_as_archived(
    _wire_db, _reset_metrics, tmp_path
):
    """A clean run reports the real denominator, not the placeholder 1."""
    result = await _run(tmp_path, degraded_categories=[])

    assert result.total_items == _GATHERED
    assert result.success_count == _GATHERED
    assert result.failed_count == 0


@pytest.mark.asyncio
async def test_counts_stay_coherent(_wire_db, _reset_metrics, tmp_path):
    """ok + failed == total, on both shapes. The invariant, stated once."""
    for degraded in ([], ["dvr_rules"], ["dvr_rules", "logos"]):
        result = await _run(tmp_path, degraded_categories=degraded)
        assert result.success_count + result.failed_count == result.total_items


# ---------------------------------------------------------------------------
# 2. Severity is UNCHANGED (the constraint on this change)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_backup_still_reports_success_severity(
    _wire_db, _reset_metrics, tmp_path
):
    result = await _run(tmp_path, degraded_categories=[])

    assert result.success is True
    assert result.failed_count == 0
    assert completion_notification_type(result) == "success"


@pytest.mark.asyncio
async def test_a_degraded_backup_still_reports_warning_severity(
    _wire_db, _reset_metrics, tmp_path
):
    """``failed_count > 0`` still holds exactly when a category degraded.

    A real artifact WAS produced, so ``success`` stays True and the run is a
    warning — the severity zt3kf established, reached through counts that now
    mean items.
    """
    result = await _run(tmp_path, degraded_categories=["dvr_rules"])

    assert result.success is True
    assert result.failed_count > 0
    assert completion_notification_type(result) == "warning"


@pytest.mark.asyncio
async def test_an_upload_failure_still_reports_error_severity(
    _wire_db, _reset_metrics, tmp_path
):
    """Cloud-upload health takes precedence and is unaffected by the counts.

    The gather counts describe the ARTIFACT; the upload failure is carried by
    ``success=False`` + ``error``, so it cannot be softened by a healthy
    gather's numbers.
    """
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _artifact(dest_dir, degraded_categories=[])

    async def _failed_upload(_artifact_arg):
        return {
            "attempted": True, "run_result": "failed",
            "succeeded": 0, "failed": 1, "results": [],
        }

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ), patch.object(
        DbasBackupTask, "_upload_to_targets", side_effect=_failed_upload, autospec=False
    ):
        result = await DbasBackupTask().execute()

    assert result.success is False
    assert result.error == "CLOUD_UPLOAD_FAILED"
    assert completion_notification_type(result) == "error"


# ---------------------------------------------------------------------------
# 3. The surface the bead is about — the Journal line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_journal_line_reports_true_item_counts(
    _wire_db, _reset_metrics, tmp_path, test_session
):
    """End-to-end through the REAL engine: the row an operator scans.

    Asserted on the COUNTS only, never the surrounding wording — the severity
    prefix is option (a)'s surface and is pinned by its own tests.
    """
    from tasks import dbas_backup
    from task_engine import TaskEngine
    from models import ScheduledTask

    backups_dir = tmp_path / "backups"

    async def _fake_build(dest_dir=None, **_kwargs):
        return _artifact(dest_dir, degraded_categories=["dvr_rules"])

    test_session.query(ScheduledTask).filter(
        ScheduledTask.task_id == "dbas_backup"
    ).delete()
    test_session.add(ScheduledTask(
        task_id="dbas_backup", task_name="DBAS Backup", description="test",
        enabled=True, schedule_type="manual", send_alerts=True,
        alert_on_warning=True, show_notifications=True,
    ))
    test_session.commit()

    journal_rows: list[dict] = []
    engine = TaskEngine()
    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), patch.object(
        dbas_backup, "build_backup_artifact", side_effect=_fake_build
    ), patch(
        "services.notification_service.create_notification_internal",
        new=AsyncMock(return_value={"id": 1}),
    ), patch("task_engine.log_entry", new=lambda **kw: journal_rows.append(kw)):
        await engine._execute_task(task_id="dbas_backup", triggered_by="test")

    terminal = [row for row in journal_rows if row["action_type"] != "start"][-1]
    assert "%d ok, 1 failed" % (_GATHERED - 1) in terminal["description"]
    assert "0 ok" not in terminal["description"]


# ---------------------------------------------------------------------------
# 4. The builder actually reports the number
# ---------------------------------------------------------------------------


def test_the_artifact_reports_how_many_categories_it_gathered():
    """Without this the task has nothing honest to divide by."""
    art = BackupArtifact(
        zip_path=Path("/tmp/x.zip"), sidecar_path=Path("/tmp/x.zip.sha256"),
        schema_version=1, sha256="d", file_count=41, gathered_categories=16,
    )
    assert art.gathered_categories == 16


@pytest.mark.asyncio
async def test_a_backup_never_reports_zero_of_zero(
    _wire_db, _reset_metrics, tmp_path
):
    """An artifact that does not report its gather count still counts honestly.

    Defensive: an older/stubbed artifact object carries no
    ``gathered_categories``. Falling back to ``0 total, 0 ok`` would reproduce
    the very defect this change removes, so the floor is one item.
    """
    result = await _run(tmp_path, degraded_categories=[], gathered_categories=0)

    assert result.total_items >= 1
    assert result.success_count >= 1
    assert result.failed_count == 0
