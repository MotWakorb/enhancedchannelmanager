"""Regression tests for the MCP least-privilege credential projection.

Bead enhancedchannelmanager-04c0u.8. The AI-facing sidecar must receive the
public MCP client key and nothing else from ``settings.json``, and it must
receive it under the same owner-only (0600) terms that
enhancedchannelmanager-04c0u.7 established for the private
``mcp-service.json`` projection. Widening the mode, or handing the sidecar the
whole settings document, is the failure these tests exist to catch.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from config import DispatcharrSettings, save_settings

_KEY = "<Synthetic-MCP-Key-04c0u8>"


def _resolve_projection_paths(**environment: str) -> dict[str, str]:
    """Import the real ``config`` module in a clean process under ``environment``.

    The projection paths are module-level constants resolved at import time, so
    the only honest way to test their resolution is to import the module again
    with a different environment. A subprocess keeps that out of the running
    interpreter, where a reload would leave the rest of the suite looking at
    rebound constants.
    """
    backend_dir = Path(config.__file__).resolve().parent
    program = (
        "import json, config; "
        "print(json.dumps({name: str(getattr(config, name)) for name in "
        "('MCP_SECRETS_DIR', 'MCP_KEY_FILE', 'MCP_SERVICE_FILE')}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": str(backend_dir),
            **environment,
        },
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def umask(request):
    """Run the test body under an explicit umask, restoring the runner's.

    Without this the 0600 assertions below are satisfied by any implementation
    at all on a runner whose umask is already 0o077 — including a naive
    ``write_text`` — so the guard would pass the exact mutation it exists to
    catch.
    """
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


def test_save_projects_only_mcp_key_with_private_mode(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    settings = DispatcharrSettings(
        mcp_api_key=_KEY,
        dispatcharr_api_key="<Synthetic-Dispatcharr-Key-04c0u8>",
        password="<Synthetic-Password-04c0u8>",
    )

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file), patch(
        "config.secrets.token_urlsafe", return_value=_KEY
    ):
        save_settings(settings)

    projected = key_file.read_text()
    assert projected == f"{_KEY}\n"
    # Only the MCP key crosses the boundary — no other credential from the
    # settings document appears in the projected material.
    assert "<Synthetic-Dispatcharr-Key-04c0u8>" not in projected
    assert "<Synthetic-Password-04c0u8>" not in projected
    assert {path.name for path in key_file.parent.iterdir()} == {"api-key"}


@pytest.mark.parametrize("umask", [0o000, 0o077], indirect=True)
def test_projection_is_owner_only_and_never_group_or_world_readable(
    tmp_path: Path, umask: int
) -> None:
    """0600, unconditionally — the sidecar reads it by sharing ECM's uid.

    …-04c0u.7 pinned the private projection at 0600 and made the sidecar
    reject any other owner. Relaxing the public projection to 0640/0644 would
    publish the MCP key to every account in the container's group.

    Parametrised over the umask because a permissive one (0o000) is the case
    where the implementation has to set the mode itself; a restrictive one
    (0o077) would mask a missing ``fchmod`` and let the guard pass while the
    property was unenforced.
    """
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file), patch(
        "config.secrets.token_urlsafe", return_value=_KEY
    ):
        save_settings(DispatcharrSettings(mcp_api_key=_KEY))

    mode = key_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected owner-only projection, got {mode:o}"
    assert not mode & 0o077


def test_rotation_atomically_replaces_projected_key(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file), patch(
        "config.secrets.token_urlsafe",
        side_effect=["<Synthetic-Old-Key-04c0u8>", "<Synthetic-New-Key-04c0u8>"],
    ):
        config.rotate_mcp_api_key()
        config.rotate_mcp_api_key()

    assert key_file.read_text() == "<Synthetic-New-Key-04c0u8>\n"
    assert key_file.stat().st_mode & 0o777 == 0o600
    # No temporary file is left behind for the sidecar to read.
    assert {path.name for path in key_file.parent.iterdir()} == {"api-key"}


def test_existing_key_is_projected_when_settings_load(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    config_file.write_text('{"mcp_api_key":"<Synthetic-Existing-Key-04c0u8>"}')

    config.clear_settings_cache()
    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        config.get_settings()
    config.clear_settings_cache()

    assert key_file.read_text() == "<Synthetic-Existing-Key-04c0u8>\n"


def test_save_fails_when_the_projection_directory_is_absent(
    tmp_path: Path,
) -> None:
    """An absent MCP_SECRETS_DIR must not be conjured into existence.

    Note what this does NOT say. It is not "no overlay means no projection":
    ``MCP_SECRETS_DIR`` defaults to ``CONFIG_DIR``, which always exists, so the
    guard this exercises is unreachable on a default deployment — see
    ``test_the_default_projection_directory_is_config_dir``. It fires when the
    configured directory genuinely is not there, e.g. ``MCP_SECRETS_DIR``
    pointing at a mount that failed to attach. The save must fail before either
    authority or settings changes and must not fabricate the missing path.
    """
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "absent" / "api-key"

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file), pytest.raises(
        FileNotFoundError
    ):
        save_settings(DispatcharrSettings(mcp_api_key=_KEY))

    assert not key_file.parent.exists()
    assert not config_file.exists()


def test_the_default_projection_directory_is_config_dir() -> None:
    """The fallback is CONFIG_DIR, so every deployment projects (finding 6).

    Pins the claim the module header and ``_project_mcp_api_key``'s docstring
    make, because the opposite claim — "a deployment without the MCP overlay
    has no projection directory, so nothing is written" — was asserted in
    three places and was false. The fallback is deliberate: it keeps a newer
    backend working under an older compose file whose sidecar still reads
    ``/config``.
    """
    resolved = _resolve_projection_paths(CONFIG_DIR="/example-config")

    assert resolved["MCP_SECRETS_DIR"] == "/example-config"
    assert resolved["MCP_KEY_FILE"] == "/example-config/api-key"
    assert resolved["MCP_SERVICE_FILE"] == "/example-config/mcp-service.json"


def test_an_empty_projection_dir_env_var_falls_back_to_config_dir() -> None:
    """``MCP_SECRETS_DIR=`` must not resolve to the process CWD (finding 8).

    ``os.environ.get("MCP_SECRETS_DIR", CONFIG_DIR)`` returns ``""`` for an
    explicitly empty entry in an ``.env`` file, and ``Path("")`` is ``Path(".")``
    — so ECM would have projected the credentials into ``/app`` while the
    sidecar, which uses the ``or`` form, kept looking in ``/config``.
    """
    resolved = _resolve_projection_paths(
        CONFIG_DIR="/example-config", MCP_SECRETS_DIR=""
    )

    assert resolved["MCP_SECRETS_DIR"] == "/example-config"
    assert resolved["MCP_KEY_FILE"] == "/example-config/api-key"


def test_projection_failure_does_not_discard_valid_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        '{"url":"http://dispatcharr.example:9191",'
        '"mcp_api_key":"<Synthetic-Existing-Key-04c0u8>"}'
    )
    config.clear_settings_cache()
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file), patch(
        "config._replace_mcp_authority_locked", side_effect=PermissionError("denied")
    ), pytest.raises(PermissionError, match="denied"):
        config.get_settings()
    config.clear_settings_cache()

    assert json.loads(config_file.read_text())["url"] == "http://dispatcharr.example:9191"
    assert not key_file.exists()


def test_public_and_private_projections_are_distinct_files() -> None:
    """Three credentials, never collapsed into one artifact.

    ``api-key`` carries the operator-disclosed client key; ``mcp-service.json``
    carries the private backend principal key and the destructive-confirmation
    signing key (…-04c0u.7). They share a directory so the sidecar needs only
    one mount, but they are separate files holding separate secrets.
    """
    assert config.MCP_KEY_FILE != config.MCP_SERVICE_FILE
    assert config.MCP_KEY_FILE.name == "api-key"
    assert config.MCP_SERVICE_FILE.name == "mcp-service.json"
    assert config.MCP_KEY_FILE.parent == config.MCP_SECRETS_DIR
    assert config.MCP_SERVICE_FILE.parent == config.MCP_SECRETS_DIR


def test_a_failed_projection_never_leaves_the_cache_behind_the_saved_file(
    tmp_path: Path,
) -> None:
    """Finding 5 — the ordering invariant, stated as a property.

    ``save_settings`` first resolves and, when necessary, initializes the MCP
    authority, then writes ``settings.json`` as its compatibility mirror.
    Whatever authority publication does, these two must hold afterwards:

      * the process must not serve a key the settings file no longer contains
        (the cache is never behind disk), and
      * an authority-publication failure must still reach the caller before the
        authority, mirror, or cache changes (rotation is fail-closed).

    The old ordering broke the first while keeping the second: the new key was
    durably on disk, ``_cached_settings`` still held the old one, and the
    caller got a 500. A credential rotation is exactly where that split is
    least acceptable.

    Any projection failure exercises this — a full disk, a read-only mount, an
    SELinux denial. ``PermissionError`` below is one example of the class, not
    the specification.
    """
    config_file = tmp_path / "settings.json"
    config.clear_settings_cache()

    key_file = tmp_path / "api-key"
    key_file.write_text("<Synthetic-Old-Key-04c0u8>\n")
    key_file.chmod(0o600)
    config_file.write_text('{"mcp_api_key":"<Synthetic-Old-Key-04c0u8>"}')
    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        old = config.get_settings()
        with patch(
            "config._publish_mcp_api_key_locked", side_effect=PermissionError("denied")
        ), pytest.raises(PermissionError):
            config.rotate_mcp_api_key()

        assert key_file.read_text() == "<Synthetic-Old-Key-04c0u8>\n"
        assert "<Synthetic-Old-Key-04c0u8>" in config_file.read_text()
        assert old.mcp_api_key == "<Synthetic-Old-Key-04c0u8>"

    config.clear_settings_cache()


class TestSupersededProjectionIsReportedNotDeleted:
    """SEC-07 — the pre-…-04c0u.8 private projection left behind by an upgrade.

    A deployment that ran …-04c0u.7 has a live-format ``mcp-service.json``
    (backend principal key + confirmation signing key) in ``CONFIG_DIR``.
    Moving the projection to ``MCP_SECRETS_DIR`` does not remove it. ECM
    reports it so it stops being invisible, and does not delete it, because
    deleting an operator's credential material is destructive and
    irreversible — and because on a default deployment the two paths are the
    same file.
    """

    def test_it_is_reported_when_the_projection_moved(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        orphan = config_dir / "mcp-service.json"
        orphan.write_text("{}")
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(
            config, "MCP_SERVICE_FILE", tmp_path / "ecm-mcp" / "mcp-service.json"
        )
        monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path / "ecm-mcp")

        assert config.superseded_mcp_service_projection() == orphan
        # Reported, never removed.
        assert orphan.exists()

    def test_the_live_projection_is_never_reported_as_superseded(
        self, tmp_path, monkeypatch
    ):
        """The default deployment has MCP_SECRETS_DIR == CONFIG_DIR.

        Reporting there — or worse, acting on the report — would name the file
        ECM is actively using.
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "mcp-service.json").write_text("{}")
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config, "MCP_SECRETS_DIR", config_dir)
        monkeypatch.setattr(
            config, "MCP_SERVICE_FILE", config_dir / "mcp-service.json"
        )

        assert config.superseded_mcp_service_projection() is None

    def test_nothing_is_reported_when_the_upgrade_left_nothing_behind(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path / "ecm-mcp")
        monkeypatch.setattr(
            config, "MCP_SERVICE_FILE", tmp_path / "ecm-mcp" / "mcp-service.json"
        )

        assert config.superseded_mcp_service_projection() is None
