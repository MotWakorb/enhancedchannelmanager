"""Producer → saved HTTP handoff and retention filename contract (0m06f.3.1).

Real ZIP/envelope producers, private SQLite/config, and real router, decrypt and
manifest validation. Dispatcharr is stubbed; the restore engine stops at the
validated artifact boundary, not an external destination mutation.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import io
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote
import zipfile

import pytest

from dbas import retention
from routers import backup
from task_scheduler import TaskResult
from tasks.dbas_restore import DbasRestoreTask


STAMP = datetime(2026, 9, 12, 1, 2, 3, tzinfo=timezone.utc)
CANONICAL = "ecm-backup-2026-09-12_010203.zip"
LEGACY = "ecm-backup-lvo9lde8.zip"  # Exact retained pre-fix producer shape.
PASSPHRASE = "<synthetic-backup-collision-passphrase>"
_source_label = ContextVar("collision_source_label", default="first")


class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return STAMP.astimezone(tz) if tz else STAMP.replace(tzinfo=None)


@pytest.fixture
def producer(tmp_path, monkeypatch, test_engine, test_session):
    root = tmp_path / "config"
    root.mkdir(mode=0o700)
    saved = root / "backups"
    saved.mkdir(mode=0o700)
    monkeypatch.setattr(backup, "CONFIG_DIR", root)
    monkeypatch.setattr(backup, "CONFIG_FILE", root / "settings.json")
    monkeypatch.setattr(backup, "JOURNAL_DB_FILE", root / "journal.db")
    monkeypatch.setattr(backup, "BACKUPS_DIR", saved)
    monkeypatch.setattr(backup, "datetime", _FrozenClock)
    monkeypatch.setattr(backup, "get_engine", lambda: test_engine)
    monkeypatch.setattr(backup, "get_session", lambda: test_session)
    monkeypatch.setattr(backup, "get_settings", lambda: SimpleNamespace(
        model_dump=lambda: {"smtp_from_name": _source_label.get()},
    ))
    client = MagicMock()
    for method in (
        "get_m3u_accounts", "get_epg_sources", "get_channel_groups",
        "get_channel_profiles", "get_stream_profiles", "get_users",
        "get_user_agents", "get_dvr_rules", "get_server_groups",
        "get_recordings", "get_core_settings", "get_all_logos_paginated",
    ):
        setattr(client, method, AsyncMock(return_value=[]))
    for method in ("get_channels", "get_streams"):
        setattr(client, method, AsyncMock(return_value={"results": [], "next": None}))
    monkeypatch.setattr(backup, "get_client", lambda: client)

    async def build(label, *, encrypted=False):
        token = _source_label.set(label)
        try:
            return await backup.build_backup_artifact(
                dest_dir=saved,
                passphrase=PASSPHRASE if encrypted else None,
                acknowledge_unrecoverable=encrypted,
            )
        finally:
            _source_label.reset(token)

    return saved, build


def _assert_private_intact(art):
    assert stat.S_IMODE(art.zip_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(art.sidecar_path.stat().st_mode) == 0o600
    assert backup.verify_artifact_sha256(art.zip_path, art.sidecar_path)
    assert art.sidecar_path.read_text().split()[1] == art.zip_path.name


@pytest.fixture
def handoff(monkeypatch):
    """Consume the actual task handoff synchronously, through its crypto gate."""
    seen = []
    pending = []

    async def consume(self, started_at, artifact_path):
        with zipfile.ZipFile(artifact_path) as archive:
            manifest = backup.validate_artifact_manifest(archive)
            assert manifest["schema_version"] == backup.BACKUP_SCHEMA_VERSION
            import yaml
            label = yaml.safe_load(archive.read("categories/settings.yaml"))["settings"]
            seen.append((self.confirm_apply, label, self.artifact_path))
        return TaskResult(success=True, message="Validated saved artifact")

    async def run_task(task_id, *, parameters, run_id):
        assert task_id == "dbas_restore"
        assert run_id
        assert parameters["cleanup_artifact"] is False
        task = DbasRestoreTask()
        task.update_config(parameters)
        return await task.execute()

    def capture(coro):
        pending.append(coro)
        return MagicMock()

    monkeypatch.setattr(DbasRestoreTask, "_run_restore", consume)
    monkeypatch.setattr("task_engine.get_engine", lambda: SimpleNamespace(run_task=run_task))
    # Scope to each HTTP call: asyncio itself is shared with the transport.
    async def restore(client, name, *, apply=False, passphrase=None):
        from unittest.mock import patch
        with patch.object(backup.asyncio, "create_task", side_effect=capture):
            response = await client.post("/api/backup/restore-dbas-saved", json={
                "filename": name, "confirm_apply": apply, "passphrase": passphrase,
            })
        results = []
        while pending:
            results.append(await pending.pop(0))
        return response, results

    return restore, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
async def test_same_second_producer_outputs_reach_saved_consumers(
    producer, async_client, handoff, monkeypatch, encrypted,
):
    saved, build = producer
    first = await build("first")
    original = first.zip_path.read_bytes()
    second = await build("second", encrypted=encrypted)
    assert first.zip_path.name == CANONICAL
    assert second.zip_path != first.zip_path
    assert second.zip_path.read_bytes() != original
    assert first.zip_path.read_bytes() == original
    for art in (first, second):
        _assert_private_intact(art)

    listing = await async_client.get("/api/backup/saved")
    assert listing.status_code == 200
    assert {entry["filename"] for entry in listing.json()} == {
        first.zip_path.name, second.zip_path.name,
    }
    restore, seen = handoff
    for art, label in ((first, "first"), (second, "second")):
        response = await async_client.get("/api/backup/saved/" + art.zip_path.name)
        assert response.status_code == 200
        assert response.content == art.zip_path.read_bytes()
        for apply in (False, True):
            response, results = await restore(
                async_client, art.zip_path.name, apply=apply,
                passphrase=PASSPHRASE if art.encrypted else None,
            )
            assert response.status_code == 200, response.text
            assert len(results) == 1 and results[0].success
            assert seen[-1] == (apply, {"smtp_from_name": label}, str(art.zip_path))
        _assert_private_intact(art)

    # Both timestamped outputs count under the EXISTING retention floor. The
    # collision's timestamp remains usable locally and in a cloud listing.
    assert retention._filename_timestamp(second.zip_path.name) == STAMP
    monkeypatch.setattr(retention, "_audit_deletion", lambda *args: None)
    adapter = SimpleNamespace(list_files=AsyncMock(return_value=[
        first.zip_path.name, second.zip_path.name, LEGACY,
    ]), delete=AsyncMock(return_value=True))
    result = await retention.prune_cloud_destination(
        adapter, "backups", "private-test", True, last_n=1, now=STAMP,
    )
    assert result["deleted"] == 1
    assert adapter.delete.call_args.args[0].endswith(second.zip_path.name)
    assert retention.prune_local_backups(False, last_n=1, backups_dir=saved)["deleted"] == 0
    before = {art.zip_path: art.zip_path.read_bytes() for art in (first, second)}
    result = retention.prune_local_backups(True, last_n=1, now=STAMP, backups_dir=saved)
    assert result["deleted"] == 1
    # Equal timestamps retain input order under the existing policy; filesystem
    # enumeration is not ordered. Either survivor is valid, with its own bytes.
    remaining = [path for path in before if path.exists()]
    assert len(remaining) == 1
    survivor = remaining[0]
    assert survivor.read_bytes() == before[survivor]
    response = await async_client.delete("/api/backup/saved/" + survivor.name)
    assert response.status_code == 200
    assert not survivor.exists()


@pytest.mark.asyncio
async def test_concurrent_producers_reserve_distinct_private_artifacts(producer, monkeypatch):
    _, build = producer

    async def yield_after_allocation(*args, **kwargs):
        # This is a real await seam after filename selection, before ZIP open.
        await asyncio.sleep(0)
        return [], [], {}, 0

    monkeypatch.setattr(backup, "_gather_dispatcharr_logo_payloads", yield_after_allocation)
    artifacts = await asyncio.gather(
        *(build(str(i), encrypted=bool(i % 2)) for i in range(4)),
        return_exceptions=True,
    )
    assert not any(isinstance(art, Exception) for art in artifacts), artifacts
    assert len({art.zip_path for art in artifacts}) == 4
    assert len({art.zip_path.read_bytes() for art in artifacts}) == 4
    for art in artifacts:
        _assert_private_intact(art)


@pytest.mark.asyncio
@pytest.mark.parametrize("occupied", ["zip", "sidecar", "encrypted-temp", "symlink"])
async def test_allocation_preserves_existing_artifact_family(producer, occupied):
    saved, build = producer
    suffix = {"zip": "", "sidecar": ".sha256", "encrypted-temp": ".enc", "symlink": ""}[occupied]
    existing = saved / (CANONICAL + suffix)
    victim = saved.parent / "victim"
    if occupied == "symlink":
        victim.write_bytes(b"existing operator bytes")
        existing.symlink_to(victim)
    else:
        existing.write_bytes(b"existing operator bytes")
    art = await build("new", encrypted=True)
    assert art.zip_path.name != CANONICAL
    assert existing.read_bytes() == b"existing operator bytes"
    _assert_private_intact(art)


@pytest.mark.asyncio
async def test_collision_encrypt_failure_cleans_only_its_own_files(producer, monkeypatch):
    saved, build = producer
    first = await build("first")
    before = {path.name: path.read_bytes() for path in saved.iterdir()}
    def fail(*args):
        raise RuntimeError("synthetic encryption failure")
    monkeypatch.setattr(backup.artifact_crypto, "encrypt_file", fail)
    with pytest.raises(RuntimeError, match="synthetic encryption failure"):
        await build("second", encrypted=True)
    assert {path.name: path.read_bytes() for path in saved.iterdir()} == before
    _assert_private_intact(first)


@pytest.mark.asyncio
async def test_legacy_random_zip_is_recoverable_but_not_newly_prunable(
    producer, async_client, handoff,
):
    saved, build = producer
    art = await build("legacy", encrypted=True)
    legacy = saved / LEGACY
    # Model an existing old-producer artifact; no operator rename is required
    # by the product. The ciphertext is copied without modification.
    legacy.write_bytes(art.zip_path.read_bytes())
    legacy.chmod(0o600)
    restore, seen = handoff
    for apply in (False, True):
        response, results = await restore(async_client, LEGACY, apply=apply, passphrase=PASSPHRASE)
        assert response.status_code == 200, response.text
        assert len(results) == 1 and results[0].success
        assert seen[-1][2] == str(legacy)
    response, results = await restore(async_client, LEGACY, passphrase="<synthetic-wrong-passphrase>")
    assert response.status_code == 200
    assert len(results) == 1 and not results[0].success
    assert len(seen) == 2  # Wrong key never reaches the archive consumer.
    assert not list(saved.glob("ecm-restore-dec-*"))
    response = await async_client.get("/api/backup/saved/" + LEGACY)
    assert response.status_code == 200 and response.content == legacy.read_bytes()
    assert retention._filename_timestamp(LEGACY) is None
    assert retention.select_prunable([LEGACY], last_n=0, max_age_days=0) == ([], [])
    response = await async_client.delete("/api/backup/saved/" + LEGACY)
    assert response.status_code == 200 and not legacy.exists()


BAD_NAMES = [
    "ecm-backup-bad.zip", "ecm-backup-abcdefghij.zip", "ecm-backup-ABCDEFGH.zip",
    "ecm-backup-abcd.efg.zip", "ecm-backup-abcd-efg.zip", "ecm-backup-lvo9lde8.yaml",
    "ecm-backup-2026-09-12_010203-abcdefg.zip",
    "ecm-backup-2026-09-12_010203-abcdefghij.zip",
    "ecm-backup-2026-09-12_010203-abcdefgh.yaml",
    LEGACY + ".zip", LEGACY + "\n", "../" + LEGACY, "..\\" + LEGACY,
    "/tmp/" + LEGACY, "." + LEGACY, LEGACY + "\x00",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", BAD_NAMES)
async def test_bad_filename_forms_never_reach_a_consumer(producer, async_client, handoff, name):
    restore, seen = handoff
    response, results = await restore(async_client, name)
    assert response.status_code == 400
    assert results == [] and seen == []
    response = await async_client.post("/api/backup/restore-saved", json={"filename": name})
    assert response.status_code == 400
    for method in (async_client.get, async_client.delete):
        response = await method("/api/backup/saved/" + quote(name, safe=""))
        assert response.status_code in (400, 404)


@pytest.mark.asyncio
async def test_legacy_name_keeps_existing_symlink_escape_guards(producer, async_client):
    saved, _ = producer
    victim = saved.parent / "victim"
    victim.write_bytes(b"not a backup")
    (saved / LEGACY).symlink_to(victim)
    for method in (async_client.get, async_client.delete):
        response = await method("/api/backup/saved/" + LEGACY)
        assert response.status_code == 400
    response = await async_client.post("/api/backup/restore-saved", json={"filename": LEGACY})
    assert response.status_code == 404
    assert victim.read_bytes() == b"not a backup"


@pytest.mark.asyncio
async def test_legacy_save_does_not_overwrite_a_same_second_dbas_backup(
    producer, async_client, monkeypatch,
):
    _, build = producer
    art = await build("dbas")
    original = art.zip_path.read_bytes()
    monkeypatch.setattr(backup, "_create_backup_zip", lambda: io.BytesIO(b"legacy redacted ZIP"))
    response = await async_client.post("/api/backup/save")
    assert response.status_code == 200
    assert response.json()["filename"] != art.zip_path.name
    assert art.zip_path.read_bytes() == original
    _assert_private_intact(art)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [LEGACY, "ecm-backup-2026-09-12_010203-0123abcd.zip"])
async def test_unauthenticated_requests_cannot_access_accepted_names(
    producer, async_client, handoff, monkeypatch, name,
):
    saved, _ = producer
    artifact = saved / name
    artifact.write_bytes(b"private artifact")
    auth_on = lambda: SimpleNamespace(require_auth=True, setup_complete=True)
    monkeypatch.setattr("main.get_auth_settings", auth_on)
    monkeypatch.setattr("auth.dependencies.get_auth_settings", auth_on)
    for method in (async_client.get, async_client.delete):
        response = await method("/api/backup/saved/" + name)
        assert response.status_code == 401
        assert b"private artifact" not in response.content
    response = await async_client.get("/api/backup/saved")
    assert response.status_code == 401
    restore, seen = handoff
    for apply in (False, True):
        response, results = await restore(async_client, name, apply=apply)
        assert response.status_code == 401
        assert results == [] and seen == []
    response = await async_client.post("/api/backup/restore-saved", json={"filename": name})
    assert response.status_code == 401
    assert artifact.read_bytes() == b"private artifact"
