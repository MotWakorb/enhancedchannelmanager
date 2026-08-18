"""Regression tests for the MCP least-privilege credential projection.

Bead enhancedchannelmanager-04c0u.8. The AI-facing sidecar must receive the
public MCP client key and nothing else from ``settings.json``, and it must
receive it under the same owner-only (0600) terms that
enhancedchannelmanager-04c0u.7 established for the private
``mcp-service.json`` projection. Widening the mode, or handing the sidecar the
whole settings document, is the failure these tests exist to catch.
"""

from pathlib import Path
from unittest.mock import patch

import config
from config import DispatcharrSettings, save_settings

_KEY = "<Synthetic-MCP-Key-04c0u8>"


def test_save_projects_only_mcp_key_with_private_mode(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    settings = DispatcharrSettings(
        mcp_api_key=_KEY,
        dispatcharr_api_key="<Synthetic-Dispatcharr-Key-04c0u8>",
        password="<Synthetic-Password-04c0u8>",
    )

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        save_settings(settings)

    projected = key_file.read_text()
    assert projected == f"{_KEY}\n"
    # Only the MCP key crosses the boundary — no other credential from the
    # settings document appears in the projected material.
    assert "<Synthetic-Dispatcharr-Key-04c0u8>" not in projected
    assert "<Synthetic-Password-04c0u8>" not in projected
    assert {path.name for path in key_file.parent.iterdir()} == {"api-key"}


def test_projection_is_owner_only_and_never_group_or_world_readable(
    tmp_path: Path,
) -> None:
    """0600, unconditionally — the sidecar reads it by sharing ECM's uid.

    …-04c0u.7 pinned the private projection at 0600 and made the sidecar
    reject any other owner. Relaxing the public projection to 0640/0644 would
    publish the MCP key to every account in the container's group.
    """
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        save_settings(DispatcharrSettings(mcp_api_key=_KEY))

    mode = key_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected owner-only projection, got {mode:o}"
    assert not mode & 0o077


def test_rotation_atomically_replaces_projected_key(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "mcp" / "api-key"
    key_file.parent.mkdir()
    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        save_settings(DispatcharrSettings(mcp_api_key="<Synthetic-Old-Key-04c0u8>"))
        save_settings(DispatcharrSettings(mcp_api_key="<Synthetic-New-Key-04c0u8>"))

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


def test_projection_is_skipped_when_the_overlay_is_not_deployed(
    tmp_path: Path,
) -> None:
    """No projection directory means no MCP sidecar — and no stray key file."""
    config_file = tmp_path / "settings.json"
    key_file = tmp_path / "absent" / "api-key"

    with patch("config.CONFIG_FILE", config_file), patch("config.MCP_KEY_FILE", key_file):
        save_settings(DispatcharrSettings(mcp_api_key=_KEY))

    assert not key_file.parent.exists()


def test_projection_failure_does_not_discard_valid_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.json"
    config_file.write_text(
        '{"url":"http://dispatcharr.example:9191",'
        '"mcp_api_key":"<Synthetic-Existing-Key-04c0u8>"}'
    )
    config.clear_settings_cache()
    with patch("config.CONFIG_FILE", config_file), patch(
        "config._project_mcp_api_key", side_effect=PermissionError("denied")
    ):
        loaded = config.get_settings()
    config.clear_settings_cache()

    assert loaded.url == "http://dispatcharr.example:9191"


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
