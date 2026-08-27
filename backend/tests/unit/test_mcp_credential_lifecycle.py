"""Linearizability regressions for the public MCP credential lifecycle.

The sidecar reads ``api-key`` directly. These tests therefore assert against
that artifact rather than treating ``settings.json:mcp_api_key`` as a second
authority.
"""

from __future__ import annotations

import errno
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
    if hasattr(config, "_mcp_settings_mirror_dirty"):
        monkeypatch.setattr(config, "_mcp_settings_mirror_dirty", False)
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


def _recovery_document(path: Path) -> dict:
    return json.loads(path.read_text())


def _write_recovery(path: Path, key: str, state: str = "recovery-active") -> None:
    path.write_text(json.dumps({"key": key, "state": state}) + "\n")
    path.chmod(0o600)


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
    transition_result = []

    def peer_transition():
        if transition == "rotate":
            transition_result.append(config.rotate_mcp_api_key())
        else:
            config.revoke_mcp_api_key()
            transition_result.append("")
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
    expected = transition_result[0]

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


@pytest.mark.parametrize("artifact", ["multiline", "mode", "hardlink", "symlink"])
def test_untrusted_authority_degrades_without_repair_or_initialization(
    lifecycle, monkeypatch, artifact
):
    settings_file, authority_file = lifecycle
    target = authority_file.with_name("attacker-key")
    if artifact == "multiline":
        authority_file.write_text("first-secret\nsecond-secret\n")
        authority_file.chmod(0o600)
    elif artifact == "mode":
        _write_authority(authority_file, "exposed-secret")
        authority_file.chmod(0o644)
    elif artifact == "hardlink":
        _write_authority(authority_file, "linked-secret")
        os.link(authority_file, authority_file.with_name("second-link"))
    else:
        _write_authority(target, "attacker-secret")
        authority_file.symlink_to(target)
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-mirror"}))
    before_mode = stat.S_IMODE(authority_file.lstat().st_mode)
    before_target = os.readlink(authority_file) if authority_file.is_symlink() else None
    monkeypatch.setattr(
        config.secrets,
        "token_urlsafe",
        lambda _size: (_ for _ in ()).throw(AssertionError("must not generate")),
    )

    first = config.load_settings()
    second = config.load_settings()

    assert first.mcp_api_key == second.mcp_api_key == ""
    assert stat.S_IMODE(authority_file.lstat().st_mode) == before_mode
    if before_target is not None:
        assert authority_file.is_symlink()
        assert os.readlink(authority_file) == before_target
        assert _authority(target) == "attacker-secret"
    assert _stored_mirror(settings_file) == "stale-mirror"


def test_untrusted_authority_cache_recovers_when_metadata_is_repaired(lifecycle):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "repaired-key")
    authority_file.chmod(0o644)
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-mirror"}))

    assert config.load_settings().mcp_api_key == ""
    authority_file.chmod(0o600)

    assert config.load_settings().mcp_api_key == "repaired-key"
    assert _stored_mirror(settings_file) == "repaired-key"


def test_wrong_owner_authority_degrades_without_touching_artifact(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "wrong-owner-secret")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-mirror"}))
    monkeypatch.setattr(config.os, "geteuid", lambda: os.getuid() + 1)

    assert config.load_settings().mcp_api_key == ""
    assert _authority(authority_file) == "wrong-owner-secret"
    assert stat.S_IMODE(authority_file.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "raw",
    [
        b"{not-json\n",
        b"[]\n",
        b'{"key":"redo","state":"recovery-active"}\nextra\n',
    ],
)
@pytest.mark.parametrize("authority_present", [True, False])
def test_malformed_recovery_degrades_and_is_preserved(
    lifecycle, monkeypatch, raw, authority_present
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    if authority_present:
        _write_authority(authority_file, "independent-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-mirror"}))
    recovery_file.write_bytes(raw)
    recovery_file.chmod(0o600)
    monkeypatch.setattr(
        config.secrets,
        "token_urlsafe",
        lambda _size: (_ for _ in ()).throw(AssertionError("must not generate")),
    )

    expected = "independent-key" if authority_present else ""
    assert config.load_settings().mcp_api_key == expected
    assert config.load_settings().mcp_api_key == expected
    assert recovery_file.read_bytes() == raw
    if not authority_present:
        assert not authority_file.exists()


@pytest.mark.parametrize("artifact", ["mode", "owner", "hardlink", "symlink"])
@pytest.mark.parametrize("authority_present", [True, False])
def test_untrusted_recovery_metadata_degrades_and_is_preserved(
    lifecycle, monkeypatch, artifact, authority_present
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    target = recovery_file.with_name("recovery-target")
    if authority_present:
        _write_authority(authority_file, "independent-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-mirror"}))
    if artifact == "symlink":
        _write_recovery(target, "attacker-redo")
        recovery_file.symlink_to(target)
    else:
        _write_recovery(recovery_file, "attacker-redo")
        if artifact == "mode":
            recovery_file.chmod(0o644)
        elif artifact == "hardlink":
            os.link(recovery_file, recovery_file.with_name("recovery-link"))
        else:
            real_validate = config._validate_mcp_file_metadata

            def reject_recovery_owner(metadata, filename):
                if filename == config.MCP_KEY_RECOVERY_FILENAME:
                    raise PermissionError("recovery record has the wrong owner")
                return real_validate(metadata, filename)

            monkeypatch.setattr(
                config, "_validate_mcp_file_metadata", reject_recovery_owner
            )

    expected = "independent-key" if authority_present else ""
    assert config.load_settings().mcp_api_key == expected
    assert config.load_settings().mcp_api_key == expected
    if artifact == "symlink":
        assert recovery_file.is_symlink()
        assert _recovery_document(target)["key"] == "attacker-redo"
    else:
        assert recovery_file.exists()
    if not authority_present:
        assert not authority_file.exists()


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_untrusted_recovery_fails_explicit_transitions_closed(
    lifecycle, transition
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    recovery_file.write_text("{malformed\n")
    recovery_file.chmod(0o600)
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    with pytest.raises(config.MCPApiKeyStorageError):
        operation()

    assert _authority(authority_file) == "known-good"
    assert recovery_file.read_text() == "{malformed\n"


@pytest.mark.parametrize("artifact", ["authority", "recovery"])
def test_untrusted_lifecycle_artifact_fails_generic_save_closed(
    lifecycle, artifact
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    original_settings = settings_file.read_bytes()
    if artifact == "authority":
        authority_file.chmod(0o644)
    else:
        recovery_file.write_text("{malformed\n")
        recovery_file.chmod(0o600)

    with pytest.raises(config.MCPApiKeyStorageError):
        config.save_settings(config.DispatcharrSettings(user_timezone="Etc/UTC"))

    assert settings_file.read_bytes() == original_settings
    assert _authority(authority_file) == "known-good"
    if artifact == "recovery":
        assert recovery_file.read_text() == "{malformed\n"


def test_degraded_recovery_cache_converges_after_record_repair(lifecycle):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "independent-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "independent-key"}))
    recovery_file.write_text("{malformed\n")
    recovery_file.chmod(0o600)

    assert config.load_settings().mcp_api_key == "independent-key"
    _write_recovery(recovery_file, "recovered-key")

    assert config.load_settings().mcp_api_key == "recovered-key"
    assert _authority(authority_file) == "recovered-key"


def test_invalid_settings_with_valid_authority_uses_stable_degraded_cache(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "authority-key")
    settings_file.write_text("{invalid\n")
    real_acquire = config._acquire_settings_flock
    acquisitions = 0

    def count_acquire(descriptor):
        nonlocal acquisitions
        acquisitions += 1
        return real_acquire(descriptor)

    monkeypatch.setattr(config, "_acquire_settings_flock", count_acquire)

    assert config.load_settings().mcp_api_key == "authority-key"
    assert config.load_settings().mcp_api_key == "authority-key"
    assert acquisitions == 1

    settings_file.write_text(json.dumps({"mcp_api_key": "stale", "user_timezone": "Etc/UTC"}))
    converged = config.load_settings()
    assert converged.mcp_api_key == "authority-key"
    assert converged.user_timezone == "Etc/UTC"
    assert acquisitions == 2


def test_cached_absent_authority_discovers_peer_creation_without_cache_clear(
    lifecycle
):
    settings_file, authority_file = lifecycle
    settings_file.write_text("{invalid\n")

    assert config.load_settings().mcp_api_key == ""
    _write_authority(authority_file, "peer-created-key")

    assert config.load_settings().mcp_api_key == "peer-created-key"


def test_ordinary_load_sweeps_authority_and_recovery_temporaries_without_following(
    lifecycle
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    authority_target = authority_file.with_name("authority-target")
    recovery_target = authority_file.with_name("recovery-target")
    authority_target.write_text("keep-authority-target")
    recovery_target.write_text("keep-recovery-target")
    authority_temp = authority_file.with_name(".api-key.0123456789abcdef.tmp")
    recovery_temp = authority_file.with_name(
        "..api-key.recovery.0123456789abcdef.tmp"
    )
    authority_temp.symlink_to(authority_target)
    recovery_temp.symlink_to(recovery_target)

    assert config.load_settings().mcp_api_key == "known-good"

    assert not os.path.lexists(authority_temp)
    assert not os.path.lexists(recovery_temp)
    assert authority_target.read_text() == "keep-authority-target"
    assert recovery_target.read_text() == "keep-recovery-target"


@pytest.mark.parametrize(
    ("legacy_document", "expected"),
    [(None, "fresh-key"), ({"mcp_api_key": "legacy-key"}, "legacy-key")],
)
def test_initialization_without_predecessor_bypasses_wal_and_survives_fsync_refusal(
    lifecycle, monkeypatch, legacy_document, expected
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    if legacy_document is not None:
        settings_file.write_text(json.dumps(legacy_document))
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "fresh-key")
    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", lambda: False)

    assert config.load_settings().mcp_api_key == expected
    assert _authority(authority_file) == expected
    assert _stored_mirror(settings_file) == expected
    assert not recovery_file.exists()

    authority_file.unlink()
    config.clear_settings_cache()
    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", lambda: True)

    assert config.load_settings().mcp_api_key == expected
    assert _authority(authority_file) == expected


@pytest.mark.parametrize("predecessor", ["rotation", "revocation"])
def test_new_transition_cannot_replace_active_redo_until_predecessor_is_durable(
    lifecycle, monkeypatch, predecessor
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    predecessor_key = "K1" if predecessor == "rotation" else ""
    _write_authority(authority_file, "K0")
    settings_file.write_text(json.dumps({"mcp_api_key": "K0"}))
    _write_recovery(recovery_file, predecessor_key)
    original_recovery = recovery_file.read_bytes()
    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", lambda: False)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "K2")

    with pytest.raises(config.MCPApiKeyStorageError, match="predecessor"):
        config.rotate_mcp_api_key()

    assert recovery_file.read_bytes() == original_recovery
    assert _authority(authority_file) == predecessor_key

    _write_authority(authority_file, "K0")
    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", lambda: True)
    config.clear_settings_cache()
    assert config.load_settings().mcp_api_key == predecessor_key
    assert _authority(authority_file) == predecessor_key


@pytest.mark.parametrize("predecessor", ["rotation", "revocation"])
@pytest.mark.parametrize("crash_wal", ["active-predecessor", "prepared-successor"])
def test_failed_successor_staging_crash_matrix_cannot_restore_k0(
    lifecycle, monkeypatch, predecessor, crash_wal
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    predecessor_key = "K1" if predecessor == "rotation" else ""
    _write_authority(authority_file, "K0")
    settings_file.write_text(json.dumps({"mcp_api_key": "K0"}))
    _write_recovery(recovery_file, predecessor_key)
    real_fsync_parent = config._fsync_mcp_parent_directory
    calls = 0

    def fail_successor_stage():
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", fail_successor_stage)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "K2")

    with pytest.raises(OSError, match="not crash-durable"):
        config.rotate_mcp_api_key()

    assert _authority(authority_file) == predecessor_key
    if crash_wal == "active-predecessor":
        _write_authority(authority_file, "K0")
        _write_recovery(recovery_file, predecessor_key)
    else:
        _write_authority(authority_file, predecessor_key)
        _write_recovery(recovery_file, "K2", state="prepared")
    monkeypatch.setattr(config, "_fsync_mcp_parent_directory", real_fsync_parent)
    config.clear_settings_cache()

    assert config.load_settings().mcp_api_key == predecessor_key
    assert _authority(authority_file) == predecessor_key


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
    assert config.get_settings().mcp_api_key == rotated
    assert _stored_mirror(settings_file) == rotated


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_directory_fsync_refusal_is_recovered_after_crash(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    real_fsync = os.fsync
    directory_fsync_calls = 0

    def refuse_authority_commit_fsync(descriptor):
        nonlocal directory_fsync_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError(errno.EIO, "synthetic directory fsync refusal")
        return real_fsync(descriptor)

    monkeypatch.setattr(config.os, "fsync", refuse_authority_commit_fsync)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key
    result = operation()
    expected = "rotated-key" if transition == "rotate" else ""

    if transition == "rotate":
        assert result == expected
    else:
        assert result is None
    assert _authority(authority_file) == expected
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    assert _recovery_document(recovery_file) == {
        "key": expected,
        "state": "recovery-active",
    }

    # Model a host crash restoring the pre-rename directory entry. The durable
    # transition record must repair it before any settings load publishes K(n-1).
    _write_authority(authority_file, "known-good")
    config.clear_settings_cache()
    monkeypatch.setattr(config.os, "fsync", real_fsync)

    assert config.get_settings().mcp_api_key == expected
    assert _authority(authority_file) == expected
    assert _stored_mirror(settings_file) == expected


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_recovery_record_fsync_refusal_fails_before_authority_changes(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    cached = config.get_settings()
    real_fsync = os.fsync
    real_open = os.open
    real_unlink = Path.unlink

    def refuse_directory_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "synthetic recovery fsync refusal")
        return real_fsync(descriptor)

    def refuse_recovery_mutation(path, flags, *args, **kwargs):
        if Path(path) == recovery_file and flags & os.O_WRONLY:
            raise OSError(errno.EIO, "synthetic recovery mutation refusal")
        return real_open(path, flags, *args, **kwargs)

    def refuse_recovery_unlink(path, *args, **kwargs):
        if path == recovery_file:
            raise OSError(errno.EIO, "synthetic recovery unlink refusal")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(config.os, "fsync", refuse_directory_fsync)
    monkeypatch.setattr(config.os, "open", refuse_recovery_mutation)
    monkeypatch.setattr(Path, "unlink", refuse_recovery_unlink)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    with pytest.raises(OSError, match="not crash-durable"):
        operation()

    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"
    assert cached.mcp_api_key == "known-good"
    expected = "rotated-key" if transition == "rotate" else ""
    assert _recovery_document(recovery_file) == {
        "key": expected,
        "state": "prepared",
    }

    monkeypatch.setattr(config.os, "fsync", real_fsync)
    monkeypatch.setattr(config.os, "open", real_open)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    config.clear_settings_cache()

    assert config.get_settings().mcp_api_key == "known-good"
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_failed_recovery_stage_leaves_prepared_record_startup_inert(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    cached = config.get_settings()
    real_fsync = os.fsync

    def refuse_directory_fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "synthetic directory fsync refusal")
        return real_fsync(descriptor)

    monkeypatch.setattr(config.os, "fsync", refuse_directory_fsync)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    with pytest.raises(OSError, match="not crash-durable"):
        operation()

    expected = "rotated-key" if transition == "rotate" else ""
    assert _recovery_document(recovery_file) == {
        "key": expected,
        "state": "prepared",
    }
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"
    assert cached.mcp_api_key == "known-good"

    monkeypatch.setattr(config.os, "fsync", real_fsync)
    config.clear_settings_cache()

    assert config.get_settings().mcp_api_key == "known-good"
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_authority_replace_failure_cannot_activate_when_recovery_unlink_fails(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    cached = config.get_settings()
    real_replace = os.replace
    real_open = os.open
    real_unlink = Path.unlink

    def fail_authority_replace(source, destination):
        if Path(destination) == authority_file:
            raise PermissionError("synthetic authority replace refusal")
        return real_replace(source, destination)

    def fail_recovery_unlink(path, *args, **kwargs):
        if path == recovery_file:
            raise OSError(errno.EIO, "synthetic recovery unlink refusal")
        return real_unlink(path, *args, **kwargs)

    def fail_recovery_mutation(path, flags, *args, **kwargs):
        if Path(path) == recovery_file and flags & os.O_WRONLY:
            raise OSError(errno.EIO, "synthetic recovery mutation refusal")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(config.os, "replace", fail_authority_replace)
    monkeypatch.setattr(config.os, "open", fail_recovery_mutation)
    monkeypatch.setattr(Path, "unlink", fail_recovery_unlink)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    with pytest.raises(PermissionError, match="synthetic authority replace refusal"):
        operation()

    assert recovery_file.exists()
    expected = "rotated-key" if transition == "rotate" else ""
    assert _recovery_document(recovery_file) == {
        "key": expected,
        "state": "prepared",
    }
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"
    assert cached.mcp_api_key == "known-good"

    monkeypatch.setattr(config.os, "replace", real_replace)
    monkeypatch.setattr(config.os, "open", real_open)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    config.clear_settings_cache()

    assert config.get_settings().mcp_api_key == "known-good"
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
@pytest.mark.parametrize("failure_point", ["write", "fsync"])
def test_recovery_activation_failure_does_not_hide_replaced_authority(
    lifecycle, monkeypatch, caplog, transition, failure_point
):
    settings_file, authority_file = lifecycle
    recovery_file = authority_file.with_name(config.MCP_KEY_RECOVERY_FILENAME)
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    real_fsync = os.fsync
    real_write_recovery = config._write_mcp_recovery_document_locked
    directory_fsync_calls = 0
    write_attempted = False

    def refuse_authority_directory_or_recovery_fsync(descriptor):
        nonlocal directory_fsync_calls
        metadata = os.fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            directory_fsync_calls += 1
            if directory_fsync_calls == 2:
                raise OSError(errno.EIO, "synthetic authority directory fsync refusal")
        elif (
            failure_point == "fsync"
            and recovery_file.exists()
            and metadata.st_ino == recovery_file.stat().st_ino
        ):
            raise OSError(errno.EIO, "synthetic recovery state fsync refusal")
        return real_fsync(descriptor)

    def refuse_recovery_state_write(*_args, **_kwargs):
        nonlocal write_attempted
        write_attempted = True
        raise OSError(errno.EIO, "synthetic recovery state write refusal")

    monkeypatch.setattr(
        config.os,
        "fsync",
        refuse_authority_directory_or_recovery_fsync,
    )
    if failure_point == "write":
        monkeypatch.setattr(
            config,
            "_write_mcp_recovery_document_locked",
            refuse_recovery_state_write,
            raising=False,
        )
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    expected = "rotated-key" if transition == "rotate" else ""
    with pytest.raises(config.MCPApiKeyDurabilityIndeterminate) as raised:
        operation()

    assert raised.value.active_key == expected
    assert raised.value.is_revocation is (transition == "revoke")
    assert expected not in str(raised.value) or not expected
    if failure_point == "write":
        assert write_attempted
    assert _authority(authority_file) == expected
    assert _recovery_document(recovery_file) == {
        "key": expected,
        "state": "prepared" if failure_point == "write" else "recovery-active",
    }
    assert _stored_mirror(settings_file) == expected
    assert config.get_settings().mcp_api_key == expected
    assert "recovery record could not be made durable" in caplog.text

    # Model the compound failure surviving a host crash: the authority rename
    # rolls back and the WAL state write rolls back to its durable prepared form.
    _write_authority(authority_file, "known-good")
    recovery_file.write_text(
        json.dumps({"key": expected, "state": "prepared"}) + "\n"
    )
    recovery_file.chmod(0o600)
    monkeypatch.setattr(config.os, "fsync", real_fsync)
    monkeypatch.setattr(
        config,
        "_write_mcp_recovery_document_locked",
        real_write_recovery,
    )
    config.clear_settings_cache()

    assert config.get_settings().mcp_api_key == "known-good"
    assert _authority(authority_file) == "known-good"
    assert _stored_mirror(settings_file) == "known-good"


@pytest.mark.parametrize("transition", ["rotate", "revoke"])
def test_committed_authority_is_not_reported_failed_by_mirror_reconciliation(
    lifecycle, monkeypatch, transition
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")
    real_read_settings = config._read_settings_model_locked
    monkeypatch.setattr(
        config,
        "_read_settings_model_locked",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic mirror read failure")),
    )
    operation = config.rotate_mcp_api_key if transition == "rotate" else config.revoke_mcp_api_key

    result = operation()
    expected = "rotated-key" if transition == "rotate" else ""

    if transition == "rotate":
        assert result == expected
    else:
        assert result is None
    assert _authority(authority_file) == expected
    monkeypatch.setattr(config, "_read_settings_model_locked", real_read_settings)
    assert config.get_settings().mcp_api_key == expected


def test_directory_close_failure_after_commit_does_not_hide_rotated_key(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    real_close = os.close
    directory_closes = 0

    def fail_post_commit_directory_close(descriptor):
        nonlocal directory_closes
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_closes += 1
            if directory_closes == 2:
                real_close(descriptor)
                raise OSError(errno.EIO, "synthetic directory close failure")
        return real_close(descriptor)

    monkeypatch.setattr(config.os, "close", fail_post_commit_directory_close)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")

    assert config.rotate_mcp_api_key() == "rotated-key"
    assert _authority(authority_file) == "rotated-key"
    assert _stored_mirror(settings_file) == "rotated-key"
    assert config.get_settings().mcp_api_key == "rotated-key"


def test_temporary_cleanup_failure_after_commit_does_not_hide_rotated_key(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    real_unlink = Path.unlink

    def fail_missing_authority_temporary(path, *args, **kwargs):
        if path.parent == authority_file.parent and path.name.startswith(".api-key."):
            if path.name != config.MCP_KEY_RECOVERY_FILENAME and not path.exists():
                raise OSError(errno.EIO, "synthetic post-commit cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_missing_authority_temporary)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")

    assert config.rotate_mcp_api_key() == "rotated-key"
    assert _authority(authority_file) == "rotated-key"
    assert _stored_mirror(settings_file) == "rotated-key"
    assert config.get_settings().mcp_api_key == "rotated-key"


def test_security_validation_finishes_before_authority_replace(
    lifecycle, monkeypatch
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "known-good")
    settings_file.write_text(json.dumps({"mcp_api_key": "known-good"}))
    real_replace = os.replace
    real_validate = config._validate_mcp_file_metadata
    authority_replaced = False

    def track_replace(source, destination):
        nonlocal authority_replaced
        result = real_replace(source, destination)
        if Path(destination) == authority_file:
            authority_replaced = True
        return result

    def reject_post_commit_validation(metadata, filename):
        if filename == config.MCP_KEY_FILENAME:
            assert not authority_replaced, "security validation ran after linearization"
        return real_validate(metadata, filename)

    monkeypatch.setattr(config.os, "replace", track_replace)
    monkeypatch.setattr(config, "_validate_mcp_file_metadata", reject_post_commit_validation)
    monkeypatch.setattr(config.secrets, "token_urlsafe", lambda _size: "rotated-key")

    assert config.rotate_mcp_api_key() == "rotated-key"
    assert _authority(authority_file) == "rotated-key"


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_backend_degrades_authority_without_exact_owner_only_mode(
    lifecycle, mode
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "exposed-key")
    authority_file.chmod(mode)
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))

    assert config.get_settings().mcp_api_key == ""

    assert stat.S_IMODE(authority_file.stat().st_mode) == mode


def test_backend_degrades_wrong_owner_without_repairing_it(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "wrong-owner-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))
    expected_owner = os.geteuid() + 1
    monkeypatch.setattr(config.os, "geteuid", lambda: expected_owner)

    assert config.get_settings().mcp_api_key == ""

    assert _authority(authority_file) == "wrong-owner-key"


def test_backend_degrades_symlink_authority(lifecycle):
    settings_file, authority_file = lifecycle
    target = authority_file.with_name("attacker-key")
    _write_authority(target, "attacker-key")
    authority_file.symlink_to(target)
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))

    assert config.get_settings().mcp_api_key == ""
    assert authority_file.is_symlink()
    assert _authority(target) == "attacker-key"


def test_backend_degrades_hardlinked_authority(lifecycle):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "linked-key")
    os.link(authority_file, authority_file.with_name("second-link"))
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))

    assert config.get_settings().mcp_api_key == ""


def test_backend_degrades_non_regular_authority(lifecycle):
    settings_file, authority_file = lifecycle
    authority_file.mkdir()
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))

    assert config.get_settings().mcp_api_key == ""
    assert authority_file.is_dir()


def test_backend_reads_authority_from_one_descriptor(lifecycle, monkeypatch):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "descriptor-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "stale-key"}))
    real_read_text = Path.read_text

    def reject_split_path_read(path, *args, **kwargs):
        if path == authority_file:
            raise AssertionError("authority must not be re-opened after validation")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_split_path_read)

    assert config.get_settings().mcp_api_key == "descriptor-key"


@pytest.mark.parametrize("mutation", ["chmod", "hardlink"])
def test_security_metadata_change_invalidates_cached_authority(
    lifecycle, mutation
):
    settings_file, authority_file = lifecycle
    _write_authority(authority_file, "cached-key")
    settings_file.write_text(json.dumps({"mcp_api_key": "cached-key"}))
    assert config.get_settings().mcp_api_key == "cached-key"

    if mutation == "chmod":
        authority_file.chmod(0o644)
    else:
        os.link(authority_file, authority_file.with_name("second-link"))

    assert config.get_settings().mcp_api_key == ""
    if mutation == "chmod":
        assert stat.S_IMODE(authority_file.stat().st_mode) == 0o644
    else:
        assert authority_file.stat().st_nlink == 2


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
