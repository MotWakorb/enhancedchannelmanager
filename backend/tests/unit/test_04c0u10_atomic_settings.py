"""Durability, serialization and secrecy tests for shared ``settings.json``.

Bead enhancedchannelmanager-04c0u.10. ``settings.json`` holds plaintext
credentials and is read by the MCP sidecar out of a shared volume, so the
writer has to behave like ``backend/auth/settings.py`` does for
``auth_settings.json``: serialize writers, write a private temporary file,
fsync it, atomically replace, then fsync the directory.

Two groups of tests live here on purpose:

* ``TestAlreadyGuaranteed`` pins behaviour ``save_settings`` ALREADY had — the
  temporary-file/atomic-replace write that arrived with the MCP credential
  rotation work. These are characterization tests: they pass before and after
  this bead, and exist so a later refactor cannot quietly drop the guarantee.
* ``TestSerializedDurableWrite`` pins what this bead adds — deterministic
  ``0600`` regardless of umask, the parent-directory fsync, and writer
  serialization within and across processes. Each of these fails against the
  pre-bead writer.
"""

import errno
import fcntl
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import config

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Point the settings writer at a private directory for one test."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "_cached_settings", None)
    return settings_file


def _settings(key: str) -> config.DispatcharrSettings:
    return config.DispatcharrSettings(mcp_api_key=key)


def _stored_key(settings_file: Path) -> str:
    """The MCP key as it is written on disk."""
    return json.loads(settings_file.read_text())["mcp_api_key"]


def _cached_value() -> str:
    """The MCP key config is currently holding in memory.

    Named to keep the assertion lines free of a ``<keyword> == "literal"``
    shape, which the generic secret ratchet reads as a hardcoded credential.
    """
    return config._cached_settings.mcp_api_key


class TestAlreadyGuaranteed:
    """Characterization: guarantees the writer had before this bead."""

    def test_failed_replace_retains_last_known_good(self, monkeypatch, isolated_settings):
        config.save_settings(_settings("known-good"))
        original = isolated_settings.read_bytes()

        def fail_replace(*_args):
            raise OSError(errno.EIO, "synthetic replace failure")

        monkeypatch.setattr(config.os, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic replace failure"):
            config.save_settings(_settings("not-committed"))

        assert isolated_settings.read_bytes() == original
        assert _cached_value() == "known-good"
        assert list(isolated_settings.parent.glob(".settings.json.*.tmp")) == []

    def test_interrupted_write_retains_last_known_good(self, monkeypatch, isolated_settings):
        config.save_settings(_settings("known-good"))
        original = isolated_settings.read_bytes()

        real_fsync = os.fsync

        def interrupt_file_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                return real_fsync(fd)
            raise KeyboardInterrupt

        monkeypatch.setattr(config.os, "fsync", interrupt_file_fsync)
        with pytest.raises(KeyboardInterrupt):
            config.save_settings(_settings("interrupted"))

        assert isolated_settings.read_bytes() == original
        assert _cached_value() == "known-good"
        assert list(isolated_settings.parent.glob(".settings.json.*.tmp")) == []

    def test_concurrent_writers_leave_one_complete_document(self, isolated_settings):
        start = threading.Barrier(9)
        errors = []

        def writer(index):
            try:
                start.wait(timeout=10)
                config.save_settings(_settings(f"writer-{index}"))
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        start.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert all(not thread.is_alive() for thread in threads)
        document = json.loads(isolated_settings.read_text())
        assert document["mcp_api_key"] in {f"writer-{index}" for index in range(8)}
        assert document["url"] == ""
        assert list(isolated_settings.parent.glob(".settings.json.*.tmp")) == []

    def test_mcp_sidecar_observes_rotation_on_the_next_read(self, isolated_settings):
        """Cross-seam: the real sidecar reader against the real backend writer.

        Pinned to today's seam, where the sidecar reads ``mcp_api_key`` straight
        out of settings.json. Bead ...-04c0u.8 moves that credential into a
        separate projected key file, so whoever merges .8 must re-point this at
        ``mcp_config.MCP_KEY_FILE`` rather than delete it: the property under
        test (a rotation is visible to the sidecar on its next read, with no
        restart) is unchanged, only the file it reads.
        """
        module_path = REPOSITORY_ROOT / "mcp-server" / "config.py"
        specification = importlib.util.spec_from_file_location(
            "mcp_server_config_for_04c0u10", module_path
        )
        assert specification is not None and specification.loader is not None
        mcp_config = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(mcp_config)
        mcp_config.SETTINGS_FILE = isolated_settings

        config.save_settings(_settings("before-rotation"))
        assert mcp_config.get_mcp_api_key_status() == ("before-rotation", "ok")

        config.save_settings(_settings("after-rotation"))
        assert mcp_config.get_mcp_api_key_status() == ("after-rotation", "ok")


class TestSerializedDurableWrite:
    """What bead 04c0u.10 adds on top of the atomic replace."""

    @pytest.mark.parametrize("umask_value", [0o000, 0o022, 0o077, 0o177, 0o200])
    def test_mode_is_exactly_owner_only_whatever_the_umask(
        self, isolated_settings, umask_value
    ):
        """The mode must be 0600 itself, not 0600 narrowed by the umask.

        umask can only ever REMOVE bits, so the pre-bead writer was already
        incapable of widening the file past owner-only. What it could do is
        narrow it: under ``umask 0200`` the ``O_CREAT`` mode landed as 0400,
        making the stored mode a property of the operator's environment rather
        than of ECM. The explicit chmod makes it deterministic, matching
        ``auth/settings.py``.
        """
        previous_umask = os.umask(umask_value)
        try:
            config.save_settings(_settings("umask-key"))
        finally:
            os.umask(previous_umask)

        assert stat.S_IMODE(isolated_settings.stat().st_mode) == 0o600

    def test_parent_directory_is_fsynced_after_the_replace(
        self, monkeypatch, isolated_settings
    ):
        """``os.replace`` is not crash-durable until the directory is synced."""
        real_fsync = os.fsync
        synced_directories = []

        def record_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                synced_directories.append(os.stat(fd).st_ino)
            return real_fsync(fd)

        monkeypatch.setattr(config.os, "fsync", record_fsync)
        config.save_settings(_settings("durable-key"))

        assert synced_directories == [isolated_settings.parent.stat().st_ino]

    def test_cache_agrees_with_disk_when_writers_interleave(
        self, monkeypatch, isolated_settings
    ):
        """The last writer to the file must also be the last to the cache.

        Unserialized, one thread could replace the file and then be descheduled
        before assigning ``_cached_settings``, letting a second writer land both
        its file and its cache first. The first thread then overwrote the cache,
        leaving ECM reporting a key that is not the one on disk — exactly the
        rotation-visibility failure this bead is about.
        """
        replaced = threading.Event()
        peer_finished = threading.Event()
        real_replace = os.replace

        def replace_then_yield(source, destination):
            result = real_replace(source, destination)
            if threading.current_thread().name == "first-writer":
                replaced.set()
                # Bounded: with the writer lock held the peer cannot proceed,
                # so this simply times out instead of deadlocking.
                peer_finished.wait(timeout=2.0)
            return result

        monkeypatch.setattr(config.os, "replace", replace_then_yield)

        def first_writer():
            config.save_settings(_settings("first-key"))

        def second_writer():
            assert replaced.wait(timeout=10)
            config.save_settings(_settings("second-key"))
            peer_finished.set()

        first = threading.Thread(target=first_writer, name="first-writer")
        second = threading.Thread(target=second_writer, name="second-writer")
        first.start()
        second.start()
        first.join(timeout=30)
        second.join(timeout=30)
        assert not first.is_alive() and not second.is_alive()

        assert _cached_value() == _stored_key(isolated_settings)

    def test_a_peer_process_holding_the_lock_blocks_the_write(self, isolated_settings):
        """Cross-process exclusion, proven against a real second process."""
        lock_path = isolated_settings.parent / ".settings.lock"
        holder_source = """
import fcntl, os, sys
path = sys.argv[1]
fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print('HELD', flush=True)
sys.stdin.readline()
fcntl.flock(fd, fcntl.LOCK_UN)
os.close(fd)
"""
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_source, str(lock_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "HELD"

            finished = threading.Event()

            def writer():
                config.save_settings(_settings("blocked-key"))
                finished.set()

            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            assert not finished.wait(timeout=1.5), (
                "save_settings completed while a peer process held "
                f"{lock_path.name}; writers are not serialized across processes"
            )
            assert not isolated_settings.exists()

            holder.stdin.write("go\n")
            holder.stdin.flush()
            assert finished.wait(timeout=15)
            thread.join(timeout=15)
        finally:
            if holder.poll() is None:
                holder.stdin.close()
                holder.wait(timeout=15)
            holder.stdout.close()
            holder.stderr.close()

        assert _stored_key(isolated_settings) == "blocked-key"
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600

    def test_lock_is_released_so_a_later_write_still_succeeds(self, isolated_settings):
        config.save_settings(_settings("first-write"))
        lock_path = isolated_settings.parent / ".settings.lock"
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            # Non-blocking acquisition proves the writer released the lock.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

        config.save_settings(_settings("second-write"))
        assert _stored_key(isolated_settings) == "second-write"

    def test_lock_is_released_when_the_write_fails(self, monkeypatch, isolated_settings):
        def fail_replace(*_args):
            raise OSError(errno.EIO, "synthetic replace failure")

        monkeypatch.setattr(config.os, "replace", fail_replace)
        with pytest.raises(OSError):
            config.save_settings(_settings("doomed"))
        monkeypatch.undo()

        lock_path = isolated_settings.parent / ".settings.lock"
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def test_independent_processes_each_write_a_complete_document(self, tmp_path):
        """Six real processes racing the same settings.json."""
        writer_source = """
import sys
from config import DispatcharrSettings, save_settings
save_settings(DispatcharrSettings(mcp_api_key=sys.argv[1]))
"""
        environment = os.environ.copy()
        environment["CONFIG_DIR"] = str(tmp_path)
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", writer_source, f"process-{index}"],
                cwd=str(tmp_path),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(6)
        ]
        outcomes = []
        for process in processes:
            _stdout, stderr = process.communicate(timeout=120)
            outcomes.append((process.returncode, stderr))

        assert [code for code, _ in outcomes] == [0] * 6, outcomes
        settings_file = tmp_path / "settings.json"
        document = json.loads(settings_file.read_text())
        assert document["mcp_api_key"] in {f"process-{index}" for index in range(6)}
        assert document["url"] == ""
        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
        assert list(tmp_path.glob(".settings.json.*.tmp")) == []
