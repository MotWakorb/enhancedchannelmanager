"""
Backwards-compat test for ``allow_multi_provider_auto_sync`` (bd-dgs64, GH #591).

An operator upgrading from a build that predates this field has a
``settings.json`` on disk with no ``allow_multi_provider_auto_sync`` key at
all. ``config.load_settings()`` must load that file successfully and default
the field to ``False`` — preserving today's single-owner auto-sync lock
rather than failing Pydantic validation or silently defaulting to the more
permissive (duplicate-channel-risk) value.

Mirrors the on-disk-fixture pattern in
``test_config_dispatcharr_api_key_migration.py`` (write a real settings.json
under ``tmp_path``, point ``config`` at it, load, assert) so the contract is
verified against the file system, not just the in-memory Pydantic default.
"""
import json
from pathlib import Path

import pytest

import config


@pytest.fixture(autouse=True)
def _reset_settings_state(tmp_path, monkeypatch):
    """Point ``config`` at a per-test tmp_path settings.json and clear the
    cached settings so each test starts clean."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    config.clear_settings_cache()
    yield settings_file
    config.clear_settings_cache()


def _write_settings(settings_file: Path, data: dict) -> None:
    """Write a settings.json file with just enough fields for Pydantic to
    accept (url + auth_method)."""
    base = {"url": "http://dispatcharr:8000", "auth_method": "password", "username": "admin", "password": "secret"}
    base.update(data)
    settings_file.write_text(json.dumps(base, indent=2))


def test_missing_field_loads_with_default_false(_reset_settings_state):
    """A pre-bd-dgs64 settings.json (no ``allow_multi_provider_auto_sync``
    key at all) must load successfully with the field defaulted to False —
    the single-owner auto-sync guard stays locked for an upgrading operator
    who never opted in."""
    settings_file = _reset_settings_state
    _write_settings(settings_file, {})  # no allow_multi_provider_auto_sync key

    settings = config.load_settings()

    assert settings.allow_multi_provider_auto_sync is False


def test_missing_field_does_not_break_other_settings(_reset_settings_state):
    """The absent field must not fail Pydantic validation for the rest of
    the file — other settings load normally alongside the defaulted field."""
    settings_file = _reset_settings_state
    _write_settings(settings_file, {"theme": "light"})

    settings = config.load_settings()

    assert settings.allow_multi_provider_auto_sync is False
    assert settings.theme == "light"
    assert settings.is_configured() is True


def test_explicit_true_on_disk_is_preserved(_reset_settings_state):
    """Sanity check the other direction — an operator who already opted in
    (post-upgrade settings.json with the field explicitly True) keeps that
    choice across a reload."""
    settings_file = _reset_settings_state
    _write_settings(settings_file, {"allow_multi_provider_auto_sync": True})

    settings = config.load_settings()

    assert settings.allow_multi_provider_auto_sync is True
