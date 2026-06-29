"""Tests for bead 0i2vt.9 — DBAS backup task retention wiring.

Covers the integration of the retention policy into the backup task's
post-verified-success hook: the canonical artifact rename, the LOCAL prune gated
on checksum verification, and the PER-DESTINATION cloud prune gated on each
target's verified-successful upload (never on a partial/failed run).

All adapters + filesystem are mocked at module level — NO live cloud calls.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from cloud_storage.types import UploadResult
from export_models import CloudStorageTarget
from routers.backup import BackupArtifact


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
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


def _make_target(session, **overrides) -> CloudStorageTarget:
    fields = dict(
        name="t-s3", provider_type="s3", credentials="enc-blob",
        upload_path="/backups", enabled=True, credential_version=1,
        token_revoked_at=None, insecure=False,
    )
    fields.update(overrides)
    target = CloudStorageTarget(**fields)
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _fake_artifact_factory(dest_dir: Path) -> BackupArtifact:
    """A built artifact with the CANONICAL timestamped name (the builder now owns
    it directly — bead e0r3h; there is no post-build rename), plus a sidecar whose
    hash matches the bytes so verify_artifact_sha256 passes."""
    import hashlib
    from routers.backup import _get_backup_filename

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / _get_backup_filename()  # ecm-backup-<UTC ts>.zip
    data = b"PK\x03\x04realish-artifact-bytes"
    zip_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    sidecar_path = Path(str(zip_path) + ".sha256")
    sidecar_path.write_text("%s  %s\n" % (sha, zip_path.name))
    return BackupArtifact(
        zip_path=zip_path, sidecar_path=sidecar_path,
        schema_version=1, sha256=sha, file_count=7,
    )


def _ok_upload(*_a, **_k):
    return UploadResult(success=True, remote_url="s3://b/x", file_size=10, duration_ms=1)


def _adapter(upload_side_effect, list_return=None):
    a = AsyncMock()
    a.upload = AsyncMock(side_effect=upload_side_effect)
    a.list_files = AsyncMock(return_value=list_return or [])
    a.delete = AsyncMock(return_value=True)
    return a


async def _run(dbas_backup, DbasBackupTask, backups_dir, config, adapters):
    async def _fake_build(dest_dir=None, **_kwargs):
        return _fake_artifact_factory(dest_dir)

    def _get_adapter(provider, creds):
        return adapters.pop(0)

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), \
         patch.object(dbas_backup, "build_backup_artifact", side_effect=_fake_build), \
         patch.object(dbas_backup, "create_notification_internal", AsyncMock(return_value={"id": 1})), \
         patch("cloud_storage.crypto.decrypt_credentials", return_value={"bucket_name": "b"}), \
         patch("cloud_storage.get_adapter", side_effect=_get_adapter):
        task = DbasBackupTask()
        task.update_config(config)
        result = await task.execute()
    return result, task


# ---------------------------------------------------------------------------
# Canonical artifact name (builder-owned — bead e0r3h).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_artifact_has_canonical_timestamped_name(_wire_db, _reset_metrics, tmp_path):
    """The artifact carries the canonical ecm-backup-<ts>.zip name (produced by
    the builder directly — bead e0r3h, no post-build rename) so it matches the
    retention allowlist + is sortable. No ecm-artifact-<rand> temp name is ever
    left behind."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups = tmp_path / "backups"
    result, _ = await _run(
        dbas_backup, DbasBackupTask, backups, {"retention_enabled": False}, []
    )
    assert result.success is True
    on_disk = list(backups.glob("ecm-backup-*.zip"))
    assert len(on_disk) == 1
    # No mkstemp temp name is ever created/left behind by the builder.
    assert not list(backups.glob("ecm-artifact-*.zip"))
    # The sidecar tracks the canonical zip.
    sidecar = Path(str(on_disk[0]) + ".sha256")
    assert sidecar.exists()
    assert on_disk[0].name in sidecar.read_text()


# ---------------------------------------------------------------------------
# Local retention gated on checksum verification.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_local_retention_prunes_old_after_verified_backup(_wire_db, _reset_metrics, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups = tmp_path / "backups"
    backups.mkdir()
    # Pre-seed 3 OLD canonical backups (older than 30d).
    for ts in ("2020-01-01_000000", "2020-01-02_000000", "2020-01-03_000000"):
        (backups / ("ecm-backup-%s.zip" % ts)).write_bytes(b"old")

    result, _ = await _run(
        dbas_backup, DbasBackupTask, backups,
        {"retention_enabled": True, "retention_last_n": 1, "retention_max_age_days": 30},
        [],
    )
    assert result.success is True
    # last_n=1 floor + the new (verified) backup is newest → only it survives.
    remaining = sorted(p.name for p in backups.glob("ecm-backup-*.zip"))
    assert len(remaining) == 1
    assert remaining[0].startswith("ecm-backup-20")  # the new one (current year)


@pytest.mark.asyncio
async def test_local_retention_disabled_prunes_nothing(_wire_db, _reset_metrics, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups = tmp_path / "backups"
    backups.mkdir()
    for ts in ("2020-01-01_000000", "2020-01-02_000000"):
        (backups / ("ecm-backup-%s.zip" % ts)).write_bytes(b"old")

    await _run(
        dbas_backup, DbasBackupTask, backups,
        {"retention_enabled": False, "retention_last_n": 1},
        [],
    )
    # Disabled → both old + the new one all survive (3 total).
    assert len(list(backups.glob("ecm-backup-*.zip"))) == 3


@pytest.mark.asyncio
async def test_local_retention_skips_when_checksum_fails(_wire_db, _reset_metrics, tmp_path):
    """NEVER-ZERO: if the local artifact fails checksum verification, local
    retention does NOT prune."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    backups = tmp_path / "backups"
    backups.mkdir()
    for ts in ("2020-01-01_000000", "2020-01-02_000000"):
        (backups / ("ecm-backup-%s.zip" % ts)).write_bytes(b"old")

    # Force checksum verification to report failure.
    with patch.object(dbas_backup, "verify_artifact_sha256", return_value=False):
        await _run(
            dbas_backup, DbasBackupTask, backups,
            {"retention_enabled": True, "retention_last_n": 1, "retention_max_age_days": 30},
            [],
        )
    # Unverified → nothing pruned (old ones survive + the new one = 3).
    assert len(list(backups.glob("ecm-backup-*.zip"))) == 3


# ---------------------------------------------------------------------------
# Per-destination cloud retention — only on a verified-successful upload.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cloud_retention_runs_per_successful_destination(_wire_db, _reset_metrics, test_session, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="dest-a")
    # Post-upload, the destination holds the just-uploaded NEW artifact (newest)
    # plus 4 old canonical backups. With last_n=1 the new one is the floor →
    # all 4 old ones are pruned.
    old = ["ecm-backup-2020-01-0%d_000000.zip" % i for i in range(1, 5)]
    listing = ["ecm-backup-2026-06-18_120000.zip"] + old
    adapter = _adapter(_ok_upload, list_return=listing)

    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {
            "retention_enabled": True,
            "retention_last_n": 1,
            "retention_max_age_days": 30,
            "cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}],
        },
        [adapter],
    )
    assert result.success is True
    adapter.list_files.assert_awaited()
    # 4 old beyond the floor of 1 → all 4 deleted.
    assert adapter.delete.await_count == 4


@pytest.mark.asyncio
async def test_cloud_retention_skipped_for_failed_destination(_wire_db, _reset_metrics, test_session, tmp_path):
    """Per-destination no-prune-on-failure: a target whose upload FAILS must NOT
    have its retention run (no list/delete), while a sibling SUCCESS target is
    pruned."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t_ok = _make_target(test_session, name="ok-dest")
    t_fail = _make_target(test_session, name="fail-dest")

    def _fail_upload(*_a, **_k):
        return UploadResult(success=False, error="boom", duration_ms=1)

    old = ["ecm-backup-2020-01-0%d_000000.zip" % i for i in range(1, 4)]
    listing = ["ecm-backup-2026-06-18_120000.zip"] + old
    ok_adapter = _adapter(_ok_upload, list_return=listing)
    fail_adapter = _adapter(_fail_upload, list_return=listing)

    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {
            "retention_enabled": True,
            "retention_last_n": 1,
            "retention_max_age_days": 30,
            "cloud_targets": [
                {"cloud_target_id": t_ok.id, "cloud_credential_version": 1},
                {"cloud_target_id": t_fail.id, "cloud_credential_version": 1},
            ],
        },
        [ok_adapter, fail_adapter],
    )
    assert result.details["upload"]["run_result"] == "partial"
    # The succeeding destination pruned its old artifacts.
    ok_adapter.list_files.assert_awaited()
    assert ok_adapter.delete.await_count == 3
    # The FAILING destination never ran retention.
    fail_adapter.list_files.assert_not_awaited()
    fail_adapter.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_retention_disabled_does_not_list(_wire_db, _reset_metrics, test_session, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="dest")
    adapter = _adapter(_ok_upload, list_return=["ecm-backup-2020-01-01_000000.zip"])
    await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {
            "retention_enabled": False,
            "cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}],
        },
        [adapter],
    )
    adapter.list_files.assert_not_awaited()
    adapter.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Config round-trip.
# ---------------------------------------------------------------------------
def test_retention_config_round_trip():
    from tasks.dbas_backup import DbasBackupTask

    task = DbasBackupTask()
    task.update_config({
        "retention_enabled": True,
        "retention_last_n": 14,
        "retention_max_age_days": 90,
    })
    cfg = task.get_config()
    assert cfg["retention_enabled"] is True
    assert cfg["retention_last_n"] == 14
    assert cfg["retention_max_age_days"] == 90


def test_retention_last_n_floored_to_one():
    """A misconfigured last_n=0 is floored to 1 at config time (never-zero)."""
    from tasks.dbas_backup import DbasBackupTask

    task = DbasBackupTask()
    task.update_config({"retention_last_n": 0})
    assert task.retention_last_n == 1
