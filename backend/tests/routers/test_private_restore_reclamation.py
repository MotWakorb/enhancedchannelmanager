"""Reclamation touches only old, unlocked, privately owned snapshot directories."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from routers import backup


def abandoned(root):
    directory = root / "ecm-saved-restore-abc12345"
    directory.mkdir(mode=0o700)
    artifact = directory / "ecm-restore-abc12345.zip"
    artifact.write_bytes(b"private backup")
    artifact.chmod(0o600)
    age(directory)
    return directory, artifact


def age(directory):
    old = time.time() - backup._DBAS_RESTORE_TMP_MAX_AGE_SECONDS - 60
    for child in directory.iterdir():
        os.utime(child, (old, old), follow_symlinks=False)
    os.utime(directory, (old, old))


def test_reclaims_old_private_snapshot_without_touching_original(tmp_path):
    original = tmp_path / "ecm-backup-2026-01-01_000000.zip"
    original.write_bytes(b"operator original")
    directory, _ = abandoned(tmp_path)
    backup._sweep_stale_restore_temps(tmp_path)
    assert not directory.exists()
    assert original.read_bytes() == b"operator original"


@pytest.mark.parametrize(
    "unsafe", ["symlink", "directory", "unknown", "public", "fresh"]
)
def test_refuses_unexpected_or_fresh_snapshot_contents(tmp_path, unsafe):
    directory, artifact = abandoned(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside retained")
    if unsafe == "symlink":
        artifact.unlink()
        artifact.symlink_to(outside)
    elif unsafe == "directory":
        artifact.unlink()
        artifact.mkdir()
    elif unsafe == "unknown":
        (directory / "keep.txt").write_text("unrelated")
    elif unsafe == "public":
        directory.chmod(0o755)
    else:
        os.utime(artifact, None)
    if unsafe != "fresh":
        age(directory)
    backup._sweep_stale_restore_temps(tmp_path)
    assert directory.exists()
    assert outside.read_bytes() == b"outside retained"


def test_does_not_follow_snapshot_directory_symlink(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    directory, artifact = abandoned(elsewhere)
    (tmp_path / directory.name).symlink_to(directory, target_is_directory=True)
    backup._sweep_stale_restore_temps(tmp_path)
    assert artifact.read_bytes() == b"private backup"


def test_keeps_unowned_layouts_and_fresh_or_empty_directories(tmp_path):
    directory, artifact = abandoned(tmp_path)
    other_link = tmp_path / "retained-hardlink.zip"
    other_link.hardlink_to(artifact)
    backup._sweep_stale_restore_temps(tmp_path)
    assert artifact.exists() and other_link.exists()
    other_link.unlink()
    directory.rename(tmp_path / "ecm-saved-restore-not-generated")
    empty = tmp_path / "ecm-saved-restore-empty123"
    empty.mkdir(mode=0o700)
    backup._sweep_stale_restore_temps(tmp_path)
    assert empty.exists()
    age(empty)
    backup._sweep_stale_restore_temps(tmp_path)
    assert not empty.exists()
    assert (tmp_path / "ecm-saved-restore-not-generated").exists()


@pytest.mark.asyncio
async def test_saved_trigger_reclaims_orphan_and_retains_original(
    async_client, tmp_path, monkeypatch
):
    pool = tmp_path / "restore_uploads"
    pool.mkdir(mode=0o700)
    orphan, _ = abandoned(pool)
    saved = tmp_path / "backups"
    saved.mkdir()
    original = saved / "ecm-backup-2026-01-01_000000.zip"
    original.write_bytes(b"original retained")
    monkeypatch.setattr(backup, "BACKUPS_DIR", saved)
    monkeypatch.setattr(backup, "_DBAS_RESTORE_TMP_DIR", pool)
    create_task = asyncio.create_task
    tasks = []

    def capture(coro):
        task = create_task(coro)
        tasks.append(task)
        return task

    with patch("task_engine.get_engine", return_value=MagicMock(run_task=AsyncMock())):
        monkeypatch.setattr(backup.asyncio, "create_task", capture)
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": original.name}
        )
        assert tasks
        await asyncio.gather(*tasks)
    assert response.status_code == 200
    assert not orphan.exists()
    assert not list(pool.iterdir())
    assert original.read_bytes() == b"original retained"


def test_old_active_snapshot_is_protected_by_cross_process_lease(tmp_path):
    # A separate interpreter owns the lease: a process-local active-name set
    # would fail this test and would not protect multi-worker/restart overlap.
    script = """
import sys
from restore_snapshot_storage import PrivateRestoreSnapshot
snapshot = PrivateRestoreSnapshot(sys.argv[1])
print(snapshot.name, flush=True)
sys.stdin.read()
"""
    env = dict(os.environ, PYTHONPATH=str(Path(backup.__file__).parents[1]))
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        directory = Path(process.stdout.readline().strip())
        assert directory.is_dir() and directory.parent == tmp_path
        artifact = directory / "ecm-restore-abc12345.zip"
        artifact.write_bytes(b"active backup")
        artifact.chmod(0o600)
        age(directory)
        backup._sweep_stale_restore_temps(tmp_path)
        assert artifact.read_bytes() == b"active backup"
        process.kill()  # no Python finally/finalizer; kernel releases the lease
        process.wait(timeout=10)
        backup._sweep_stale_restore_temps(tmp_path)
        assert not directory.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def plaintext(directory, name="ecm-restore-dec-k3_9zq0a.zip"):
    decrypted = directory / name
    decrypted.write_bytes(b"decrypted plaintext")
    decrypted.chmod(0o600)
    age(directory)
    return decrypted


def test_reclaims_orphan_with_artifact_and_decrypted_plaintext(tmp_path):
    # A process killed mid-decrypt leaves the snapshot ZIP and its plaintext.
    directory, artifact = abandoned(tmp_path)
    decrypted = plaintext(directory)
    backup._sweep_stale_restore_temps(tmp_path)
    assert not artifact.exists() and not decrypted.exists()
    assert not directory.exists()


def test_reclaims_orphan_holding_only_decrypted_plaintext(tmp_path):
    # Task cleanup unlinks the artifact before the plaintext, so a kill between
    # the two leaves plaintext alone; it must not outlive the snapshot.
    directory, artifact = abandoned(tmp_path)
    artifact.unlink()
    decrypted = plaintext(directory)
    backup._sweep_stale_restore_temps(tmp_path)
    assert not decrypted.exists()
    assert not directory.exists()


def test_real_mkstemp_plaintext_name_and_mode_are_reclaimable(tmp_path):
    import tempfile

    directory, artifact = abandoned(tmp_path)
    fd, name = tempfile.mkstemp(
        prefix="ecm-restore-dec-", suffix=".zip", dir=str(directory)
    )
    os.close(fd)
    info = os.stat(name)
    assert info.st_uid == os.geteuid() and (info.st_mode & 0o777) == 0o600
    age(directory)
    backup._sweep_stale_restore_temps(tmp_path)
    assert not directory.exists()


@pytest.mark.parametrize(
    "unsafe",
    ["two_plaintexts", "two_artifacts", "plaintext_hardlink", "plaintext_public",
     "plaintext_fresh", "plaintext_symlink", "foreign_name", "plaintext_bad_charset"],
)
def test_refuses_unexpected_plaintext_layouts(tmp_path, unsafe):
    directory, artifact = abandoned(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside retained")
    decrypted = plaintext(directory)
    if unsafe == "two_plaintexts":
        plaintext(directory, "ecm-restore-dec-second00.zip")
    elif unsafe == "two_artifacts":
        second = directory / "ecm-restore-second00.zip"
        second.write_bytes(b"second")
        second.chmod(0o600)
    elif unsafe == "plaintext_hardlink":
        (tmp_path / "retained-plaintext.zip").hardlink_to(decrypted)
    elif unsafe == "plaintext_public":
        decrypted.chmod(0o644)
    elif unsafe == "plaintext_symlink":
        decrypted.unlink()
        decrypted.symlink_to(outside)
    elif unsafe == "foreign_name":
        (directory / "keep.txt").write_text("unrelated")
    elif unsafe == "plaintext_bad_charset":
        decrypted.rename(directory / "ecm-restore-dec-ABCDEFGH.zip")
    if unsafe == "plaintext_fresh":
        os.utime(decrypted, None)
    else:
        age(directory)
    backup._sweep_stale_restore_temps(tmp_path)
    assert directory.exists()
    assert artifact.exists()
    assert outside.read_bytes() == b"outside retained"
