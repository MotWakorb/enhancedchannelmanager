"""Tests for bead 0i2vt.8 — DBAS backup multi-destination cloud upload.

Covers the per-target tri-state run semantics, per-target freshness
re-validation, per-destination + insecure audit rows, and the
ecm_backup_upload_total metric. The single-target .6 pre-build freshness gate
is covered by test_dbas_backup_task.py and is unchanged here.

All adapters are mocked — NO live cloud calls.
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
from models import JournalEntry
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


def _run_counter(result_label: str) -> float:
    return observability.get_metric("backup_runs_total").labels(result=result_label)._value.get()


def _upload_counter(provider: str, result: str) -> float:
    return observability.get_metric("backup_upload_total").labels(
        provider=provider, result=result
    )._value.get()


def _make_target(session, **overrides) -> CloudStorageTarget:
    fields = dict(
        name="t-s3",
        provider_type="s3",
        credentials="enc-blob",
        upload_path="/backups",
        enabled=True,
        credential_version=1,
        token_revoked_at=None,
        insecure=False,
    )
    fields.update(overrides)
    target = CloudStorageTarget(**fields)
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _fake_artifact(dest_dir: Path) -> BackupArtifact:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "ecm-artifact-abc123.zip"
    sidecar_path = Path(str(zip_path) + ".sha256")
    zip_path.write_bytes(b"PK\x03\x04fake")
    sidecar_path.write_text("deadbeef  %s\n" % zip_path.name)
    return BackupArtifact(
        zip_path=zip_path, sidecar_path=sidecar_path,
        schema_version=1, sha256="deadbeef", file_count=7,
    )


def _ok_upload(*_a, **_k):
    return UploadResult(success=True, remote_url="s3://b/x", file_size=10, duration_ms=1)


def _fail_upload(*_a, **_k):
    return UploadResult(success=False, error="boom", duration_ms=1)


async def _run(dbas_backup, DbasBackupTask, backups_dir, config, adapter_map, notify=None):
    """Run execute() with build + adapters mocked. adapter_map maps a call
    sequence: each get_adapter call returns the next adapter from the list."""
    async def _fake_build(dest_dir=None):
        return _fake_artifact(dest_dir)

    notify = notify or AsyncMock(return_value={"id": 1})

    def _get_adapter(provider, creds):
        return adapter_map.pop(0)

    with patch.object(dbas_backup, "BACKUPS_DIR", backups_dir), \
         patch.object(dbas_backup, "build_backup_artifact", side_effect=_fake_build), \
         patch.object(dbas_backup, "create_notification_internal", notify), \
         patch("cloud_storage.crypto.decrypt_credentials", return_value={"bucket_name": "b"}), \
         patch("cloud_storage.get_adapter", side_effect=_get_adapter):
        task = DbasBackupTask()
        task.update_config(config)
        result = await task.execute()
    return result, notify


def _adapter(upload_side_effect):
    a = AsyncMock()
    a.upload = AsyncMock(side_effect=upload_side_effect)
    return a


# ---------------------------------------------------------------------------
# Tri-state run semantics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_targets_succeed_is_success(_wire_db, _reset_metrics, test_session, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="a", credential_version=1)
    t2 = _make_target(test_session, name="b", credential_version=1)

    adapters = [_adapter(_ok_upload), _adapter(_ok_upload)]
    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [
            {"cloud_target_id": t1.id, "cloud_credential_version": 1},
            {"cloud_target_id": t2.id, "cloud_credential_version": 1},
        ]},
        adapters,
    )

    assert result.success is True
    assert result.details["upload"]["run_result"] == "success"
    assert _run_counter("success") == 1.0
    assert _upload_counter("s3", "success") == 2.0


@pytest.mark.asyncio
async def test_some_targets_succeed_is_partial(_wire_db, _reset_metrics, test_session, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="a")
    t2 = _make_target(test_session, name="b")

    # t1 ok, t2 fails (zip upload fails)
    adapters = [_adapter(_ok_upload), _adapter(_fail_upload)]
    result, notify = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [
            {"cloud_target_id": t1.id, "cloud_credential_version": 1},
            {"cloud_target_id": t2.id, "cloud_credential_version": 1},
        ]},
        adapters,
    )

    assert result.success is False
    assert result.details["upload"]["run_result"] == "partial"
    assert _run_counter("partial") == 1.0
    assert _run_counter("success") == 0.0
    # partial fires a run-level notification
    titles = [c.kwargs.get("title", "") for c in notify.await_args_list]
    assert any("Partial" in t for t in titles)


@pytest.mark.asyncio
async def test_no_targets_succeed_is_failed(_wire_db, _reset_metrics, test_session, tmp_path):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="a")
    adapters = [_adapter(_fail_upload)]
    result, notify = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        adapters,
    )

    assert result.success is False
    assert result.details["upload"]["run_result"] == "failed"
    assert _run_counter("failed") == 1.0
    assert _upload_counter("s3", "failed") == 1.0
    titles = [c.kwargs.get("title", "") for c in notify.await_args_list]
    assert any("Failed" in t for t in titles)


# ---------------------------------------------------------------------------
# Per-target freshness re-validation aborts a stale target non-silently
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_target_fails_non_silently_without_upload(
    _wire_db, _reset_metrics, test_session, tmp_path, caplog
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    # Target rotated to v2; the schedule captured v1 → stale.
    t1 = _make_target(test_session, name="stale", credential_version=2)
    # An adapter is provided but must NEVER be used (freshness aborts first).
    adapter = _adapter(_ok_upload)

    with caplog.at_level("WARNING"):
        result, notify = await _run(
            dbas_backup, DbasBackupTask, tmp_path / "backups",
            {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
            [adapter],
        )

    assert result.success is False
    assert result.details["upload"]["run_result"] == "failed"
    adapter.upload.assert_not_awaited()  # stale → no upload attempted
    assert any("[DBAS_BACKUP]" in r.message for r in caplog.records)
    # non-silent: a journal row + a notification were written
    entries = test_session.query(JournalEntry).filter(
        JournalEntry.category == "backup_outbound"
    ).all()
    assert len(entries) >= 1
    assert notify.await_count >= 1


@pytest.mark.asyncio
async def test_revoked_target_fails_non_silently(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    from datetime import datetime
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(
        test_session, name="revoked",
        token_revoked_at=datetime(2026, 1, 1),
    )
    adapter = _adapter(_ok_upload)
    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [adapter],
    )
    assert result.details["upload"]["run_result"] == "failed"
    adapter.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_target_fails_non_silently(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="off", enabled=False)
    adapter = _adapter(_ok_upload)
    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [adapter],
    )
    assert result.details["upload"]["run_result"] == "failed"
    adapter.upload.assert_not_awaited()


# ---------------------------------------------------------------------------
# Audit rows — per upload + insecure TLS
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_row_written_per_upload(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="audited")
    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [_adapter(_ok_upload)],
    )
    assert result.success is True
    audit = test_session.query(JournalEntry).filter(
        JournalEntry.category == "backup_outbound",
        JournalEntry.action_type == "cloud_upload",
    ).all()
    assert len(audit) == 1


@pytest.mark.asyncio
async def test_insecure_target_writes_insecure_audit_row(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="insecure-tls", insecure=True)
    result, _ = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [_adapter(_ok_upload)],
    )
    assert result.success is True
    insecure_audit = test_session.query(JournalEntry).filter(
        JournalEntry.action_type == "cloud_upload_insecure_tls",
    ).all()
    assert len(insecure_audit) == 1


# ---------------------------------------------------------------------------
# Deferred providers (dropbox/onedrive) are a non-silent per-target failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deferred_provider_is_non_silent_failure(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    t1 = _make_target(test_session, name="dbx", provider_type="dropbox")
    adapter = _adapter(_ok_upload)
    result, notify = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [adapter],
    )
    assert result.details["upload"]["run_result"] == "failed"
    adapter.upload.assert_not_awaited()
    assert notify.await_count >= 1


# ---------------------------------------------------------------------------
# SEC-2 — a raw exception in the upload seam must be credential-masked before
# it reaches the journal description / notification message.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_upload_seam_exception_reason_is_masked(
    _wire_db, _reset_metrics, test_session, tmp_path
):
    """When adapter.upload() raises an exception whose text carries a
    secret-looking token, the journaled + notified failure reason is masked —
    no raw secret substring survives into the audit row or notification."""
    from tasks import dbas_backup
    from tasks.dbas_backup import DbasBackupTask

    secret = "AKIAIOSFODNN7EXAMPLE"  # AWS access-key-id shape masked by mask_secrets

    def _raise_with_secret(*_a, **_k):
        raise RuntimeError(
            "boto3 endpoint refused; aws_secret_access_key=%s leaked" % secret
        )

    t1 = _make_target(test_session, name="seam-raise")
    result, notify = await _run(
        dbas_backup, DbasBackupTask, tmp_path / "backups",
        {"cloud_targets": [{"cloud_target_id": t1.id, "cloud_credential_version": 1}]},
        [_adapter(_raise_with_secret)],
    )

    assert result.success is False
    assert result.details["upload"]["run_result"] == "failed"

    # The journaled failure description must not carry the raw secret.
    entries = test_session.query(JournalEntry).filter(
        JournalEntry.action_type == "cloud_upload_failed",
    ).all()
    assert len(entries) == 1
    description = entries[0].description
    assert secret not in description
    assert "***REDACTED***" in description
    # The masked surface still names the target + the raised-exception path.
    assert "raised" in description

    # The notification message must likewise be masked.
    messages = [c.kwargs.get("message", "") for c in notify.await_args_list]
    assert any("***REDACTED***" in m for m in messages)
    assert all(secret not in m for m in messages)


# ---------------------------------------------------------------------------
# Config round-trip for the new cloud_targets list
# ---------------------------------------------------------------------------
def test_cloud_targets_config_round_trip():
    from tasks.dbas_backup import DbasBackupTask

    task = DbasBackupTask()
    task.update_config({"cloud_targets": [
        {"cloud_target_id": 3, "cloud_credential_version": 7},
        {"cloud_target_id": "4", "cloud_credential_version": None},
    ]})
    cfg = task.get_config()
    assert cfg["cloud_targets"][0] == {"cloud_target_id": 3, "cloud_credential_version": 7}
    assert cfg["cloud_targets"][1] == {"cloud_target_id": 4, "cloud_credential_version": None}


def test_no_cloud_targets_keeps_local_only_success(_wire_db):
    """An empty cloud_targets list = local-only backup, run_result stays success."""
    from tasks.dbas_backup import DbasBackupTask

    task = DbasBackupTask()
    task.update_config({"cloud_targets": []})
    assert task.cloud_targets == []
