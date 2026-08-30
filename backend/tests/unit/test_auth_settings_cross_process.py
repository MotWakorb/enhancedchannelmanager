"""Cross-process authority tests for authentication settings (qg14z)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _isolate(monkeypatch, tmp_path):
    from auth import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "AUTH_CONFIG_FILE", tmp_path / "auth_settings.json")
    monkeypatch.setattr(settings, "_cached_auth_settings", None)
    monkeypatch.setattr(settings, "_cached_auth_settings_signature", None, raising=False)
    return settings


def test_cached_process_observes_atomic_write_from_peer(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    assert settings.get_auth_settings().setup_complete is False

    peer = initial.model_copy(deep=True)
    peer.setup_complete = True
    payload = json.dumps(peer.model_dump(), indent=2)
    replacement = tmp_path / "peer-auth-settings.tmp"
    replacement.write_text(payload)
    replacement.replace(settings.AUTH_CONFIG_FILE)

    assert settings.get_auth_settings().setup_complete is True


def test_real_subprocess_cache_reader_observes_peer_replacement(tmp_path):
    config_file = tmp_path / "auth_settings.json"
    from auth.settings import AuthSettings

    initial = AuthSettings(setup_complete=False, require_auth=True)
    config_file.write_text(json.dumps(initial.model_dump()))
    probe = """
import sys
from auth.settings import get_auth_settings
assert get_auth_settings().setup_complete is False
print('READY', flush=True)
sys.stdin.readline()
print('CLOSED' if get_auth_settings().setup_complete else 'OPEN', flush=True)
"""
    env = os.environ.copy()
    env["CONFIG_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    process = subprocess.Popen(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "READY"
    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    replacement = tmp_path / "peer.tmp"
    replacement.write_text(json.dumps(completed.model_dump()))
    replacement.replace(config_file)
    process.stdin.write("continue\n")
    process.stdin.flush()
    assert process.stdout.readline().strip() == "CLOSED"
    assert process.wait(timeout=10) == 0


def test_real_process_stale_missing_initializer_cannot_overwrite_setup(tmp_path):
    backend_root = str(Path(__file__).resolve().parents[2])
    env = os.environ.copy()
    env["CONFIG_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = backend_root
    stale_probe = """
import sys
import auth.settings as settings
settings._durable_user_exists = lambda: False
real_save = settings._save_auth_settings_locked
def paused_save(candidate):
    print('READY', flush=True)
    sys.stdin.readline()
    return real_save(candidate)
settings._save_auth_settings_locked = paused_save
settings.get_auth_settings()
"""
    setup_writer = """
from auth.settings import AuthSettings, save_auth_settings
candidate = AuthSettings(setup_complete=True, require_auth=True)
print('START', flush=True)
raise SystemExit(0 if save_auth_settings(candidate) else 1)
"""
    stale = subprocess.Popen(
        [sys.executable, "-c", stale_probe],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert stale.stdout.readline().strip() == "READY"
    writer = subprocess.Popen(
        [sys.executable, "-c", setup_writer],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert writer.stdout.readline().strip() == "START"
    time.sleep(0.1)
    assert writer.poll() is None
    stale.stdin.write("continue\n")
    stale.stdin.flush()
    assert stale.wait(timeout=10) == 0
    assert writer.wait(timeout=10) == 0
    payload = json.loads((tmp_path / "auth_settings.json").read_text())
    assert payload["setup_complete"] is True


def test_failed_atomic_replace_preserves_durable_and_cached_authority(
    monkeypatch, tmp_path
):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    original = settings.AUTH_CONFIG_FILE.read_text()

    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    monkeypatch.setattr(settings.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))

    assert settings.save_auth_settings(completed) is False
    assert settings.AUTH_CONFIG_FILE.read_text() == original
    assert settings.get_auth_settings().setup_complete is False


def test_file_and_directory_fsync_are_both_required(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    real_fsync = settings.os.fsync
    calls = 0

    def count_fsync(fd):
        nonlocal calls
        calls += 1
        return real_fsync(fd)

    monkeypatch.setattr(settings.os, "fsync", count_fsync)
    assert settings.save_auth_settings(completed)
    assert calls == 2


def test_file_fsync_failure_preserves_original_and_retry(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    original = settings.AUTH_CONFIG_FILE.read_text()
    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    monkeypatch.setattr(settings.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError()))
    assert settings.save_auth_settings(completed) is False
    assert settings.AUTH_CONFIG_FILE.read_text() == original
    assert settings.get_auth_settings().setup_complete is False


def test_directory_fsync_failure_reloads_replaced_file_and_retry(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    real_fsync = settings.os.fsync
    calls = 0

    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError()
        return real_fsync(fd)

    monkeypatch.setattr(settings.os, "fsync", fail_second)
    assert settings.save_auth_settings(completed) is False
    assert json.loads(settings.AUTH_CONFIG_FILE.read_text())["setup_complete"] is True
    assert settings.get_auth_settings().setup_complete is True


def test_directory_open_failure_reloads_replaced_file(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    initial = settings.AuthSettings(setup_complete=False, require_auth=True)
    assert settings.save_auth_settings(initial)
    completed = initial.model_copy(deep=True)
    completed.setup_complete = True
    real_open = settings.os.open

    def fail_directory(path, flags, mode=0o777):
        if Path(path) == tmp_path:
            raise OSError()
        return real_open(path, flags, mode)

    monkeypatch.setattr(settings.os, "open", fail_directory)
    assert settings.save_auth_settings(completed) is False
    assert json.loads(settings.AUTH_CONFIG_FILE.read_text())["setup_complete"] is True
    assert settings.get_auth_settings().setup_complete is True


def test_repeated_invalid_json_reuses_fail_closed_cache(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    settings.AUTH_CONFIG_FILE.write_text("{")
    checks = 0

    def durable_owner():
        nonlocal checks
        checks += 1
        return True

    monkeypatch.setattr(settings, "_durable_user_exists", durable_owner)
    assert settings.get_auth_settings().setup_complete is True
    assert settings.get_auth_settings().setup_complete is True
    assert checks == 1


def test_cold_load_with_unverifiable_ownership_fails_closed(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    settings.AUTH_CONFIG_FILE.write_text("{")
    monkeypatch.setattr(settings, "_durable_user_exists", lambda: None)
    loaded = settings.get_auth_settings()
    assert loaded.require_auth is True
    assert loaded.setup_complete is True


def test_stat_failure_presenting_as_absence_still_fails_closed(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "_durable_user_exists", lambda: True)
    monkeypatch.setattr(settings, "_auth_file_signature", lambda: None)
    real_exists = Path.exists

    def report_absent(path):
        if path == settings.AUTH_CONFIG_FILE:
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", report_absent)
    loaded = settings.get_auth_settings()
    assert loaded.require_auth is True
    assert loaded.setup_complete is True
    assert not settings.AUTH_CONFIG_FILE.exists()


def test_durable_settings_lock_closes_fd_when_unlock_fails(monkeypatch, tmp_path):
    settings = _isolate(monkeypatch, tmp_path)
    real_flock = settings.fcntl.flock
    real_close = settings.os.close
    closed = []

    def fail_unlock(fd, operation):
        if operation == settings.fcntl.LOCK_UN:
            raise OSError()
        return real_flock(fd, operation)

    def record_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(settings.fcntl, "flock", fail_unlock)
    monkeypatch.setattr(settings.os, "close", record_close)
    with settings._durable_settings_lock():
        pass
    assert len(closed) == 1
