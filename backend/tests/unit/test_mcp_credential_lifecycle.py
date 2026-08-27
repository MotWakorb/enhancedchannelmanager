"""Linearizability regressions for the public MCP credential lifecycle.

The sidecar reads ``api-key`` directly. These tests therefore assert against
that artifact rather than treating ``settings.json:mcp_api_key`` as a second
authority.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import config
from dbas.importers import ecm_settings as ecm_settings_importer
from dbas.restore_contracts import RestoreReport
from routers import backup as backup_router


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    settings_file = tmp_path / "settings.json"
    authority_file = tmp_path / "mcp" / "api-key"
    authority_file.parent.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", authority_file.parent)
    monkeypatch.setattr(config, "MCP_KEY_FILE", authority_file)
    monkeypatch.setattr(config, "_cached_settings", None)
    if hasattr(config, "_cached_mcp_authority_signature"):
        monkeypatch.setattr(config, "_cached_mcp_authority_signature", None)
    yield settings_file, authority_file
    config.clear_settings_cache()


def _write_authority(path: Path, key: str) -> None:
    path.write_text(f"{key}\n")
    path.chmod(0o600)


def _authority(path: Path) -> str:
    raw = path.read_text()
    lines = raw.splitlines()
    assert len(lines) <= 1
    return lines[0] if lines else ""


def _stored_mirror(path: Path) -> str:
    return json.loads(path.read_text())["mcp_api_key"]


def test_stale_unrelated_save_cannot_restore_rotated_or_revoked_key(lifecycle):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "initial-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "initial-key", "user_timezone": "UTC"}))
    stale = config.get_settings().model_copy(deep=True)

    rotated = config.rotate_mcp_api_key()
    stale.user_timezone = "America/Chicago"
    config.save_settings(stale)

    assert _authority(authority_file) == rotated
    assert _stored_mirror(settings_file) == rotated
    assert json.loads(settings_file.read_text())["user_timezone"] == "America/Chicago"

    stale_after_rotation = stale.model_copy(deep=True)
    config.revoke_mcp_api_key()
    stale_after_rotation.user_timezone = "Europe/London"
    config.save_settings(stale_after_rotation)

    assert authority_file.read_text() == "\n"
    assert _stored_mirror(settings_file) == ""
    assert json.loads(settings_file.read_text())["user_timezone"] == "Europe/London"


def test_stale_save_from_peer_process_cannot_restore_rotated_key(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "initial-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "initial-key"}))
    environment = os.environ.copy()
    environment["CONFIG_DIR"] = str(settings_file.parent)
    environment["MCP_SECRETS_DIR"] = str(authority_file.parent)
    environment["PYTHONPATH"] = str(Path(config.__file__).parent)
    peer_source = """
import sys
import config
stale = config.get_settings().model_copy(deep=True)
print("READY", flush=True)
sys.stdin.readline()
stale.user_timezone = "America/Chicago"
config.save_settings(stale)
"""
    peer = subprocess.Popen(
        [sys.executable, "-c", peer_source],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert peer.stdout is not None
        assert peer.stdout.readline().strip() == "READY"
        monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
        assert config.rotate_mcp_api_key() == "rotated-key"
        assert peer.stdin is not None
        peer.stdin.write("save\n")
        peer.stdin.flush()
        _stdout, stderr = peer.communicate(timeout=30)
        assert peer.returncode == 0, stderr
    finally:
        if peer.poll() is None:
            peer.kill()
            peer.communicate(timeout=10)

    assert _authority(authority_file) == "rotated-key"
    assert _stored_mirror(settings_file) == "rotated-key"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_startup_paused_after_settings_read_cannot_republish_peer_transition(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "startup-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "startup-key", "user_timezone": "UTC"}))
    settings_read = threading.Event()
    resume_startup = threading.Event()
    real_read_text = Path.read_text

    def paused_read(path, *args, **kwargs):
        content = real_read_text(path, *args, **kwargs)
        if path == settings_file and not settings_read.is_set():
            settings_read.set()
            assert resume_startup.wait(timeout=10)
        return content

    monkeypatch.setattr(Path, "read_text", paused_read)
    loaded = []
    startup = threading.Thread(target=lambda: loaded.append(config.load_settings()))
    startup.start()
    assert settings_read.wait(timeout=10)

    transition_done = threading.Event()

    def peer_transition():
        if transition == "rotate":
            config.rotate_mcp_api_key()
        else:
            config.revoke_mcp_api_key()
        transition_done.set()

    peer = threading.Thread(target=peer_transition)
    peer.start()
    assert not transition_done.wait(timeout=0.1)
    resume_startup.set()
    startup.join(timeout=10)
    peer.join(timeout=10)
    assert not startup.is_alive() and not peer.is_alive()

    stale_startup_model = loaded[0]
    stale_startup_model.user_timezone = "America/Chicago"
    config.save_settings(stale_startup_model)
    expected = "" if transition == "revoke" else _authority(authority_file)

    assert _authority(authority_file) == expected
    assert _stored_mirror(settings_file) == expected


def test_two_rotations_serialize_and_load_observes_last_authority(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "before")
    settings_file.write_text(json.dumps({"mcp_api_key": "before"}))
    values = iter(("rotation-one", "rotation-two"))
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: next(values))
    first_entered = threading.Event()
    release_first = threading.Event()
    real_publish = config._publish_mcp_api_key_locked

    def serialized_publish(key):
        if key == "rotation-one":
            first_entered.set()
            assert release_first.wait(timeout=10)
        return real_publish(key)

    monkeypatch.setattr(config, "_publish_mcp_api_key_locked", serialized_publish)
    returned = []
    first = threading.Thread(target=lambda: returned.append(config.rotate_mcp_api_key()))
    second = threading.Thread(target=lambda: returned.append(config.rotate_mcp_api_key()))
    first.start()
    assert first_entered.wait(timeout=10)
    second.start()
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    config.clear_settings_cache()
    assert returned == ["rotation-one", "rotation-two"]
    assert _authority(authority_file) == "rotation-two"
    assert config.load_settings().mcp_api_key == "rotation-two"
    assert _stored_mirror(settings_file) == "rotation-two"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_authority_replace_failure_does_not_mutate_mirror_or_cache(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    cached = config.get_settings()
    real_replace = os.replace

    def fail_authority_replace(source, destination):
        if Path(destination) == authority_file:
            raise PermissionError("synthetic projection refusal")
        return real_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", fail_authority_replace)
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key
    with pytest.raises(PermissionError, match="synthetic projection refusal"):
        operation()

    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"
    assert cached.mcp_api_key == "known-good"


def test_explicit_empty_authority_survives_startup_and_unrelated_save(lifecycle):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key", "user_timezone": "UTC"}))

    loaded = config.load_settings()
    loaded.user_timezone = "America/Chicago"
    config.save_settings(loaded)

    assert authority_file.read_text() == "\n"
    assert _stored_mirror(settings_file) == ""
    assert config.get_settings().mcp_api_key == ""


def test_first_install_generates_once_under_the_lifecycle_lock(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    generated = []

    def mint(_size):
        generated.append("fresh-key")
        return generated[-1]

    monkeypatch.setattr(config.secrets, "token_urlsafe", mint)

    first = config.load_settings()
    config.clear_settings_cache()
    second = config.load_settings()

    assert generated == ["fresh-key"]
    assert first.mcp_api_key == second.mcp_api_key == "fresh-key"
    assert _authority(authority_file) == "fresh-key"
    assert _stored_mirror(settings_file) == "fresh-key"


@pytest.mark.parametrize(
    ("document", "expected", "mint_count"),
    [
        ({"mcp_api_key": "legacy-key"}, "legacy-key", 0),
        ({"mcp_api_key": ""}, "", 0),
        ({"user_timezone": "America/Chicago"}, "generated-key", 1),
    ],
)
def test_legacy_or_missing_field_initializes_authority_once(
    lifecycle, monkeypatch, document, expected, mint_count
):
    settings_file, authority_file = lifecycle
    settings_file.write_text(json.dumps(document))
    minted = []
    monkeypatch.setattr(
        config.secrets,
        "token_urlsafe",
        lambda _size: minted.append("generated-key") or minted[-1],
    )

    loaded = config.load_settings()

    assert len(minted) == mint_count
    assert loaded.mcp_api_key == expected
    assert _authority(authority_file) == expected
    assert _stored_mirror(settings_file) == expected


@pytest.mark.parametrize("raw", [b"{not-json\n", b"[1, 2, 3]\n", b'"scalar"\n'])
def test_invalid_or_non_object_settings_are_byte_preserved_without_generation(
    lifecycle, monkeypatch, raw
):
    settings_file, authority_file = lifecycle
    settings_file.write_bytes(raw)
    monkeypatch.setattr(
        config.secrets,
        "token_urlsafe",
        lambda _size: (_ for _ in ()).throw(AssertionError("must not generate")),
    )

    loaded = config.load_settings()

    assert loaded.mcp_api_key == ""
    assert settings_file.read_bytes() == raw
    assert not authority_file.exists()


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_lock_timeout_mutates_neither_authority_nor_settings(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    original_settings = settings_file.read_bytes()
    monkeypatch.setattr(
        config,
        "_acquire_settings_flock",
        lambda _fd: (_ for _ in ()).throw(config.SettingsWriteTimeout("busy")),
    )

    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key
    with pytest.raises(config.SettingsWriteTimeout, match="busy"):
        operation()

    assert _authority(authority_file) == "known-good"
    assert settings_file.read_bytes() == original_settings


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_missing_projection_parent_fails_transition_without_claiming_success(
    lifecycle, transition
):
    settings_file, authority_file = lifecycle
    authority_file.parent.rmdir()
    settings_file.write_text(json.dumps({"mcp_api_key": "legacy-key"}))

    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key
    with pytest.raises(FileNotFoundError):
        operation()

    assert json.loads(settings_file.read_text())["mcp_api_key"] == "legacy-key"
    assert not authority_file.exists()


def test_authority_commit_survives_mirror_failure_and_startup_repairs_it(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "before")
    settings_file.write_text(json.dumps({"mcp_api_key": "before"}))
    real_replace = os.replace
    failed = False

    def fail_first_settings_replace(source, destination):
        nonlocal failed
        if Path(destination) == settings_file and not failed:
            failed = True
            raise OSError("synthetic mirror failure")
        return real_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", fail_first_settings_replace)
    rotated = config.rotate_mcp_api_key()

    assert _authority(authority_file) == rotated
    assert _stored_mirror(settings_file) == "before"
    config.clear_settings_cache()
    assert config.load_settings().mcp_api_key == rotated
    assert _stored_mirror(settings_file) == rotated


def test_projection_is_private_complete_and_sweeps_stale_temporaries(
    lifecycle, monkeypatch
):
    _settings_file, authority_file = lifecycle
    orphan = authority_file.with_name(".api-key.0123456789abcdef.tmp")
    orphan.write_text("superseded-key\n")
    orphan.chmod(0o600)
    previous_umask = os.umask(0)
    try:
        monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "complete-key")
        assert config.rotate_mcp_api_key() == "complete-key"
    finally:
        os.umask(previous_umask)

    metadata = authority_file.stat()
    assert _authority(authority_file) == "complete-key"
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.geteuid()
    assert not orphan.exists()
    assert list(authority_file.parent.glob(".api-key.*.tmp")) == []


def test_yaml_restore_ignores_archived_mcp_key(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "destination-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "destination-key", "user_timezone": "UTC"}))
    monkeypatch.setattr(backup_router, "get_settings", config.get_settings)
    monkeypatch.setattr(backup_router, "save_settings", config.save_settings)
    monkeypatch.setattr(backup_router, "clear_settings_cache", config.clear_settings_cache)

    backup_router._restore_settings(
        {"mcp_api_key": "archived-key", "user_timezone": "America/Chicago"}
    )

    assert _authority(authority_file) == "destination-key"
    assert _stored_mirror(settings_file) == "destination-key"
    assert json.loads(settings_file.read_text())["user_timezone"] == "America/Chicago"


@pytest.mark.asyncio
async def test_dbas_restore_ignores_archived_mcp_key(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "destination-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "destination-key", "user_timezone": "UTC"}))
    monkeypatch.setattr(ecm_settings_importer, "get_settings", config.get_settings)
    monkeypatch.setattr(ecm_settings_importer, "save_settings", config.save_settings)
    report = RestoreReport(is_dry_run=False)

    await ecm_settings_importer.import_ecm_settings(
        archive_settings={"mcp_api_key": "archived-key", "user_timezone": "America/Chicago"},
        selected=True,
        report=report,
    )

    assert _authority(authority_file) == "destination-key"
    assert _stored_mirror(settings_file) == "destination-key"
    assert json.loads(settings_file.read_text())["user_timezone"] == "America/Chicago"


def test_restore_snapshot_taken_before_rotation_preserves_commit_time_authority(lifecycle):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "before")
    settings_file.write_text(json.dumps({"mcp_api_key": "before", "user_timezone": "UTC"}))
    stale_restore = config.DispatcharrSettings(
        **json.loads(
            backup_router._merge_settings_preserving_redacted(
                json.dumps(
                    {"mcp_api_key": "archived", "user_timezone": "America/Chicago"}
                ).encode()
            )
        )
    )

    rotated = config.rotate_mcp_api_key()
    config.save_settings(stale_restore)

    assert _authority(authority_file) == rotated
    assert _stored_mirror(settings_file) == rotated
    assert json.loads(settings_file.read_text())["user_timezone"] == "America/Chicago"
