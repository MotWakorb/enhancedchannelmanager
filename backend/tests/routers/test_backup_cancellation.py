"""Actual coroutine cancellation, not the task UI's cooperative cancel flag.

Exercise the real producer, logo gather, crypto and local retention in private
fixtures. Each test owns its executor and joins it before fixture teardown.
"""
import asyncio
from concurrent.futures import CancelledError as WorkerCancelledError, ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import stat
import threading

import pytest

from dbas import retention
from routers import backup
from tests.routers.test_backup_collision_names import (
    CANONICAL, PASSPHRASE, _assert_private_intact, producer,  # noqa: F401
)


def _snapshot(saved):
    return {p.name: p.read_bytes() for p in saved.iterdir()}


async def _retention_sequence(saved, build, monkeypatch, older):
    """A failed middle build must not displace an older, verified backup."""
    monkeypatch.setattr(backup, "_get_backup_filename", lambda: "ecm-backup-2026-09-13_010203.zip")
    newer = await build("newer", encrypted=True)
    monkeypatch.setattr(retention, "_audit_deletion", lambda *args: None)
    result = retention.prune_local_backups(True, last_n=2, backups_dir=saved)
    assert result["deleted"] == 0
    for art in (older, newer):
        _assert_private_intact(art)
    assert {p.name for p in saved.glob("*.zip")} == {older.zip_path.name, newer.zip_path.name}


@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
async def test_logo_coroutine_cancel_preserves_valid_retention(producer, monkeypatch, encrypted):
    saved, build = producer
    monkeypatch.setattr(backup, "_get_backup_filename", lambda: "ecm-backup-2026-09-11_010203.zip")
    older = await build("older")
    before = _snapshot(saved)
    entered = asyncio.Event()
    handles = []
    real_allocate = backup._allocate_backup_file

    def allocate(path):
        result = real_allocate(path)
        handles.append(result[1])
        return result

    async def fetch(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as scoped:
        scoped.setattr(backup, "_get_backup_filename", lambda: CANONICAL)
        scoped.setattr(backup, "_allocate_backup_file", allocate)
        client = backup.get_client()
        scoped.setattr(client, "get_all_logos_paginated", _logos)
        scoped.setattr(client, "fetch_logo_image", fetch)
        task = asyncio.create_task(build("cancelled", encrypted=encrypted))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel("logo cancellation")
            with pytest.raises(asyncio.CancelledError, match="logo cancellation"):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert handles and all(h.closed for h in handles)
    # Run retention before the residue assertion so the test pins the data loss.
    await _retention_sequence(saved, build, monkeypatch, older)
    assert not (saved / CANONICAL).exists()
    assert all((saved / name).read_bytes() == data for name, data in before.items())
    assert not list(saved.glob(backup._LOGO_SPOOL_PREFIX + "*"))


async def _logos():
    return [{"id": 17, "name": "fixture", "url": "/media/logos/fixture.png"}]


@pytest.fixture
def journal_temps(producer, monkeypatch):
    saved, _ = producer
    temps = saved.parent / "temps"
    temps.mkdir(mode=0o700)
    monkeypatch.setattr(backup.tempfile, "tempdir", str(temps))
    with sqlite3.connect(backup.JOURNAL_DB_FILE) as db:
        db.execute("CREATE TABLE private_fixture (secret TEXT)")
        db.execute("INSERT INTO private_fixture VALUES ('synthetic-private-value')")
    return temps


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", ".sha256", ".enc"])
@pytest.mark.parametrize("fault", ["fchmod", "fdopen"])
async def test_exclusive_open_fault_cleans_owned_files_and_descriptors(
    producer, monkeypatch, suffix, fault,
):
    saved, build = producer
    unrelated = saved / "operator-notes"
    unrelated.write_bytes(b"keep this")
    opened = []
    target = saved / (CANONICAL + suffix)
    real_open = os.open
    real_fault = getattr(os, fault)
    error = OSError("injected " + fault)

    def record_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path) == target:
            opened.append(fd)
        return fd

    def fail(fd, *args, **kwargs):
        if fd in opened:
            raise error
        return real_fault(fd, *args, **kwargs)

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, fault, fail)
    with pytest.raises(OSError) as caught:
        await build("allocation fault", encrypted=bool(suffix))
    assert caught.value is error
    assert opened
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert _snapshot(saved) == {unrelated.name: b"keep this"}


@pytest.mark.parametrize("cleanup_fault", ["close", "unlink"])
def test_exclusive_opener_preserves_initial_exception(tmp_path, monkeypatch, cleanup_fault):
    target = tmp_path / CANONICAL
    original = OSError("initial fchmod failure")
    real_close = os.close
    real_unlink = Path.unlink

    def fail_chmod(*args):
        raise original

    def fail_close(fd):
        real_close(fd)
        raise OSError("secondary close failure")

    def fail_unlink(path, *args, **kwargs):
        real_unlink(path, *args, **kwargs)
        raise OSError("secondary unlink failure")

    monkeypatch.setattr(os, "fchmod", fail_chmod)
    if cleanup_fault == "close":
        monkeypatch.setattr(os, "close", fail_close)
    else:
        monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError) as caught:
        backup._open_private_binary(target, exclusive=True)
    assert caught.value is original
    assert not target.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".sha256", ".enc"])
@pytest.mark.parametrize("symlink", [False, True])
async def test_late_operator_sibling_is_not_overwritten_or_cleaned(
    producer, monkeypatch, suffix, symlink,
):
    saved, build = producer
    sibling = saved / (CANONICAL + suffix)
    victim = saved.parent / "operator-file"
    victim.write_bytes(b"operator bytes")

    async def plant(*args, **kwargs):
        if symlink:
            sibling.symlink_to(victim)
        else:
            sibling.write_bytes(b"operator bytes")
        return [], [], {}, 0

    monkeypatch.setattr(backup, "_gather_dispatcharr_logo_payloads", plant)
    with pytest.raises(FileExistsError):
        await build("late sibling", encrypted=True)
    assert _snapshot(saved) == {sibling.name: b"operator bytes"}
    assert sibling.is_symlink() is symlink
    assert victim.read_bytes() == b"operator bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "writing"])
@pytest.mark.parametrize("outcome", ["success", "worker_error"])
async def test_encryption_coroutine_cancel_joins_worker_before_cleanup_and_reuse(
    producer, monkeypatch, journal_temps, phase, outcome,
):
    saved, build = producer
    monkeypatch.setattr(backup, "_get_backup_filename", lambda: "ecm-backup-2026-09-11_010203.zip")
    older = await build("older", encrypted=True)
    journal_before = backup.JOURNAL_DB_FILE.read_bytes()
    unrelated = saved / "operator-notes"
    unrelated.write_bytes(b"preserved")
    before = _snapshot(saved)
    loop = asyncio.get_running_loop()
    submitted = asyncio.Event()
    entered = asyncio.Event()
    release = threading.Event()
    futures = []
    handles = []
    worker_error = OSError("injected real crypto write failure")
    real_run = loop.run_in_executor
    real_aead = backup.artifact_crypto.ChaCha20Poly1305
    real_open = backup.artifact_crypto._open_private_binary
    real_input_open = open

    class PausedAEAD:
        def __init__(self, key):
            self.inner = real_aead(key)

        def encrypt(self, *args):
            # Real crypto has opened BOTH files and written its header here.
            if phase == "writing":
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10), "test did not release its crypto worker"
            if outcome == "worker_error":
                raise worker_error
            return self.inner.encrypt(*args)

    def record_open(path):
        handle = real_open(path)
        handles.append(handle)
        return handle

    def record_input_open(*args, **kwargs):
        handle = real_input_open(*args, **kwargs)
        handles.append(handle)
        return handle

    def occupy_worker():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10), "test did not release its private executor"

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="backup-cancel-test") as pool:
        blocker = pool.submit(occupy_worker) if phase == "queued" else None

        def run(executor, func, *args):
            if func is backup.artifact_crypto.encrypt_file:
                future = pool.submit(func, *args)
                futures.append(future)
                submitted.set()
                return asyncio.wrap_future(future)
            return real_run(executor, func, *args)

        with monkeypatch.context() as scoped:
            scoped.setattr(backup, "_get_backup_filename", lambda: CANONICAL)
            scoped.setattr(loop, "run_in_executor", run)
            scoped.setattr(backup.artifact_crypto, "ChaCha20Poly1305", PausedAEAD)
            scoped.setattr(backup.artifact_crypto, "_open_private_binary", record_open)
            scoped.setattr(backup.artifact_crypto, "open", record_input_open, raising=False)
            task = asyncio.create_task(build("cancelled", encrypted=True))
            try:
                await asyncio.wait_for(submitted.wait(), 5)
                await asyncio.wait_for(entered.wait(), 5)
                assert backup.zipfile.is_zipfile(saved / CANONICAL)
                assert not (saved / (CANONICAL + ".sha256")).exists()
                assert list(journal_temps.glob("ecm-backup-journal-*.db"))
                if phase == "writing":
                    assert handles and not handles[0].closed
                task.cancel("original encryption cancellation")
                await asyncio.sleep(0)
                assert not task.done(), "awaiter abandoned a live/queued encryption worker"
                # Repeated cancellations must not interrupt the drain or replace
                # the first cancellation. The reservation is held until join.
                for _ in range(3):
                    task.cancel("repeated cancellation")
                    await asyncio.sleep(0)
                    assert not task.done()
                    assert (saved / CANONICAL).exists()
                release.set()
                with pytest.raises(asyncio.CancelledError, match="original encryption cancellation"):
                    await asyncio.wait_for(task, 5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # Foreground join is also required on a red assertion, before
                # monkeypatch removes the real-worker fault/pause instrumentation.
                if blocker is not None:
                    blocker.result(timeout=5)
                for future in futures:
                    try:
                        future.result(timeout=5)
                    except WorkerCancelledError:
                        pass  # Red control can cancel a queued executor future.
                    except OSError as exc:
                        assert exc is worker_error
            assert futures and all(f.done() and not f.cancelled() for f in futures)
            assert handles and all(h.closed for h in handles)
            assert list(journal_temps.iterdir()) == []
            assert backup.JOURNAL_DB_FILE.read_bytes() == journal_before
            assert all((saved / name).read_bytes() == data for name, data in before.items())
        # The failed middle build must not consume the retention floor.
        await _retention_sequence(saved, build, monkeypatch, older)
        assert not (saved / CANONICAL).exists()
        assert not (saved / (CANONICAL + ".enc")).exists()
        assert not list(saved.glob(backup._LOGO_SPOOL_PREFIX + "*"))
        # Reuse the SAME generated name only after termination. A late worker
        # must not truncate, replace or remove this next valid encrypted build.
        monkeypatch.setattr(backup, "_get_backup_filename", lambda: CANONICAL)
        replacement = await build("replacement", encrypted=True)
        assert replacement.zip_path.name == CANONICAL
        _assert_private_intact(replacement)
        decrypted = saved.parent / "decrypted.zip"
        backup.artifact_crypto.decrypt_file(replacement.zip_path, PASSPHRASE, decrypted)
        with backup.zipfile.ZipFile(decrypted) as archive:
            backup.validate_artifact_manifest(archive)
        decrypted.unlink()
        with pytest.raises(backup.artifact_crypto.ArtifactDecryptError):
            backup.artifact_crypto.decrypt_file(replacement.zip_path, "wrong-test-passphrase", decrypted)
        assert not decrypted.exists()
        assert stat.S_IMODE(replacement.zip_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_on_completion", [False, True])
async def test_real_crypto_error_and_coincident_cancel_preserve_primary_exception(
    producer, monkeypatch, journal_temps, cancel_on_completion,
):
    saved, build = producer
    initial = await build("good control", encrypted=True)
    before = _snapshot(saved)
    loop = asyncio.get_running_loop()
    original = OSError("real worker error")
    real_run = loop.run_in_executor
    futures = []

    class FailingAEAD:
        def __init__(self, key):
            pass

        def encrypt(self, *args):
            raise original

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="backup-error-test") as pool:
        def run(executor, func, *args):
            if func is not backup.artifact_crypto.encrypt_file:
                return real_run(executor, func, *args)
            task = asyncio.current_task()
            future = pool.submit(func, *args)
            futures.append(future)
            wrapped = asyncio.wrap_future(future)
            if cancel_on_completion:
                # Install before shield's completion callback: the real worker
                # error and task cancellation arrive in the same event-loop turn.
                wrapped.add_done_callback(lambda _: task.cancel("coincident cancellation"))
            return wrapped

        with monkeypatch.context() as scoped:
            scoped.setattr(loop, "run_in_executor", run)
            scoped.setattr(backup.artifact_crypto, "ChaCha20Poly1305", FailingAEAD)
            task = asyncio.create_task(build("failure", encrypted=True))
            try:
                if cancel_on_completion:
                    with pytest.raises(asyncio.CancelledError, match="coincident cancellation"):
                        await task
                else:
                    with pytest.raises(OSError) as caught:
                        await task
                    assert caught.value is original
            finally:
                await asyncio.gather(task, return_exceptions=True)
                for future in futures:
                    with pytest.raises(OSError) as caught:
                        future.result(timeout=5)
                    assert caught.value is original
    assert _snapshot(saved) == before
    assert list(journal_temps.iterdir()) == []
    _assert_private_intact(initial)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["submit", "replace", "checksum", "sidecar-write"])
async def test_encrypted_producer_fault_has_no_plaintext_fallback(
    producer, monkeypatch, journal_temps, failure,
):
    saved, build = producer
    old = await build("existing", encrypted=True)
    before = _snapshot(saved)
    original = OSError("injected " + failure)

    def fail(*args, **kwargs):
        raise original

    if failure == "submit":
        monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", fail)
    elif failure == "replace":
        monkeypatch.setattr(os, "replace", fail)
    elif failure == "checksum":
        monkeypatch.setattr(backup, "_compute_sha256_streaming", fail)
    else:
        real_open = backup._open_private_binary

        class PartialSidecar:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def write(self, data):
                self.handle.write(data[:8])
                raise original

        def partial_open(path, **kwargs):
            handle = real_open(path, **kwargs)
            return PartialSidecar(handle) if path.suffix == ".sha256" else handle

        monkeypatch.setattr(backup, "_open_private_binary", partial_open)
    with pytest.raises(OSError) as caught:
        await build("failure", encrypted=True)
    assert caught.value is original
    assert _snapshot(saved) == before
    assert list(journal_temps.iterdir()) == []
    assert old.zip_path.exists()
