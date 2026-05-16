"""
Unit tests for the ``api_key`` → ``dispatcharr_api_key`` migration in
``backend/config.py`` (bd-jmi1c, GH #273).

The migration covers the field-rename half of the GH #273 fix: ECM used to
store the Dispatcharr REST API token in ``settings.json:api_key``, which
collided lexically with the MCP integration's ``mcp_api_key``. The canonical
field is now ``dispatcharr_api_key``; the legacy ``api_key`` field is
accepted on read with a one-time deprecation WARN.

These tests exercise ``config.load_settings()`` and ``config.save_settings()``
against a real on-disk settings.json (in tmp_path) so the migration's
contract with the file system is verified, not just the in-memory dict
shuffle.
"""
import json
import logging
from pathlib import Path

import pytest

import config


@pytest.fixture(autouse=True)
def _reset_settings_state(tmp_path, monkeypatch):
    """Point ``config`` at a per-test tmp_path settings.json and reset the
    cached settings + warn-once flag so each test starts clean.

    Without the reset, the ``_legacy_api_key_warned`` flag would carry
    across tests and the WARN assertions would only pass for the first
    test to exercise the legacy path.
    """
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    config.clear_settings_cache()
    yield settings_file
    config.clear_settings_cache()


def _write_settings(settings_file: Path, data: dict) -> None:
    """Write a settings.json file with just enough fields for Pydantic to
    accept (url + auth_method)."""
    base = {"url": "http://dispatcharr:8000", "auth_method": "api_key"}
    base.update(data)
    settings_file.write_text(json.dumps(base, indent=2))


class TestLoadSettingsCanonicalFieldOnly:
    """``dispatcharr_api_key`` populated, legacy ``api_key`` empty."""

    def test_canonical_field_is_used(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"dispatcharr_api_key": "canonical-key"})

        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()

        assert settings.dispatcharr_api_key == "canonical-key"
        # No deprecation WARN fires when only the canonical field is set.
        assert not any(
            "deprecated 'api_key'" in record.getMessage()
            for record in caplog.records
        ), "WARN log fired when only canonical field was populated"

    def test_is_configured_reads_canonical_field(self, _reset_settings_state):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"dispatcharr_api_key": "canonical-key"})

        settings = config.load_settings()
        assert settings.is_configured() is True


class TestLoadSettingsLegacyFieldOnly:
    """Only legacy ``api_key`` populated — the GH #273 operator state."""

    def test_legacy_field_is_migrated_to_canonical(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"api_key": "legacy-key"})

        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()

        # Migration: legacy value is copied into the canonical field so any
        # code reading ``settings.dispatcharr_api_key`` works post-rename.
        assert settings.dispatcharr_api_key == "legacy-key"
        # The legacy field is not zeroed out — external readers may still
        # depend on it during the back-compat window.
        assert settings.api_key == "legacy-key"

    def test_one_deprecation_warn_fires(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"api_key": "legacy-key"})

        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()

        warn_messages = [
            record.getMessage()
            for record in caplog.records
            if "deprecated 'api_key'" in record.getMessage()
        ]
        assert len(warn_messages) == 1, (
            f"Expected exactly one deprecation WARN; got: {warn_messages}"
        )
        assert "dispatcharr_api_key" in warn_messages[0]
        assert "GH #273" in warn_messages[0]

    def test_is_configured_after_migration(self, _reset_settings_state):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"api_key": "legacy-key"})

        settings = config.load_settings()
        assert settings.is_configured() is True

    def test_warn_only_fires_once_per_process(self, _reset_settings_state, caplog):
        """Reloading without ``clear_settings_cache`` returns the cached
        settings — no second WARN. After ``clear_settings_cache``, the
        WARN flag resets and the next load fires it again (so tests can
        assert on the warning per test)."""
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"api_key": "legacy-key"})

        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()
            # Second call hits the in-memory cache — does not re-read or warn.
            config.load_settings()

        warn_count = sum(
            1 for record in caplog.records
            if "deprecated 'api_key'" in record.getMessage()
        )
        assert warn_count == 1, (
            f"Cached reload should not re-emit WARN; got {warn_count}"
        )


class TestLoadSettingsBothFieldsPopulated:
    """When both fields exist, canonical wins (legacy is treated as stale)."""

    def test_canonical_field_wins_over_legacy(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "canonical-key",
            "api_key": "legacy-key-stale",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()

        assert settings.dispatcharr_api_key == "canonical-key"
        # Legacy field passes through unchanged — operator can clean it up
        # manually but ECM doesn't touch it.
        assert settings.api_key == "legacy-key-stale"

    def test_no_warn_when_both_populated(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "canonical-key",
            "api_key": "legacy-key-stale",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()

        # No WARN — the canonical field is populated, the migration is a no-op.
        assert not any(
            "deprecated 'api_key'" in record.getMessage()
            for record in caplog.records
        )


class TestLoadSettingsBothFieldsEmpty:
    """Neither field populated — fresh install, password-mode operator, etc."""

    def test_both_empty_no_warn(self, _reset_settings_state, caplog):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"auth_method": "password", "username": "a", "password": "b"})

        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()

        assert settings.dispatcharr_api_key == ""
        assert settings.api_key == ""
        assert not any(
            "deprecated 'api_key'" in record.getMessage()
            for record in caplog.records
        )


class TestSaveSettingsMirroring:
    """``save_settings()`` mirrors canonical → legacy on disk so external
    readers stay current. The reverse mirror (legacy → canonical) is the
    loader's job."""

    def test_save_mirrors_canonical_to_legacy(self, _reset_settings_state):
        settings_file = _reset_settings_state

        settings = config.DispatcharrSettings(
            url="http://dispatcharr:8000",
            auth_method="api_key",
            dispatcharr_api_key="new-canonical-key",
        )
        config.save_settings(settings)

        on_disk = json.loads(settings_file.read_text())
        # Canonical field written.
        assert on_disk["dispatcharr_api_key"] == "new-canonical-key"
        # Legacy field also populated so external scripts that read
        # ``api_key`` see the current value (the GH #273 workaround does this).
        assert on_disk["api_key"] == "new-canonical-key"

    def test_save_with_both_empty_does_not_fabricate_legacy_value(self, _reset_settings_state):
        """An explicit clear (both fields empty) stays cleared — the mirror
        only runs when the canonical field is populated."""
        settings_file = _reset_settings_state

        settings = config.DispatcharrSettings(
            url="http://dispatcharr:8000",
            auth_method="password",
            username="admin",
            password="secret",
            dispatcharr_api_key="",
            api_key="",
        )
        config.save_settings(settings)

        on_disk = json.loads(settings_file.read_text())
        assert on_disk["dispatcharr_api_key"] == ""
        assert on_disk["api_key"] == ""


class TestMigrationIdempotency:
    """The migration is safe to run repeatedly on the same file."""

    def test_load_save_load_is_stable(self, _reset_settings_state, caplog):
        """After a legacy-only file is loaded and saved, the next load sees
        both fields populated and triggers no further WARN."""
        settings_file = _reset_settings_state
        _write_settings(settings_file, {"api_key": "legacy-key"})

        # First load: triggers the WARN, migrates value in-memory.
        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()
        first_warn_count = sum(
            1 for record in caplog.records
            if "deprecated 'api_key'" in record.getMessage()
        )

        # Save: persists both fields to disk.
        config.save_settings(settings)
        on_disk_after_save = json.loads(settings_file.read_text())
        assert on_disk_after_save["dispatcharr_api_key"] == "legacy-key"
        assert on_disk_after_save["api_key"] == "legacy-key"

        # Second load (clear cache first so we re-read disk and reset
        # the warn-once flag): no further WARN, both fields stay populated.
        config.clear_settings_cache()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="config"):
            settings_again = config.load_settings()
        second_warn_count = sum(
            1 for record in caplog.records
            if "deprecated 'api_key'" in record.getMessage()
        )

        assert first_warn_count == 1
        # Migration is a no-op the second time around — canonical field is
        # already populated so the WARN does not fire.
        assert second_warn_count == 0
        assert settings_again.dispatcharr_api_key == "legacy-key"
        assert settings_again.api_key == "legacy-key"


# Marker string used to identify the conflict WARN log in caplog records.
# Lives in config.py — keep this in sync with the literal there.
_CONFLICT_WARN_MARKER = "are populated with differing values"


class TestLoadSettingsLegacyCanonicalConflict:
    """Both fields populated AND differ — bd-jmi1c P1-1 / bd-46g4t.

    Prior to the WARN, the legacy field's contents were silently dropped on
    the next ``save_settings()`` mirror, with no audit trail. The WARN
    surfaces this so an operator who intentionally edited the legacy field
    by hand sees that their change will be overwritten.
    """

    def test_conflict_fires_warn_and_canonical_wins(
        self, _reset_settings_state, caplog
    ):
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "canonical-active-WINNER",
            "api_key": "legacy-stale-OVERRIDDEN",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            settings = config.load_settings()

        # Canonical wins.
        assert settings.dispatcharr_api_key == "canonical-active-WINNER"
        # WARN fires, references both field names and the bead.
        conflict_warns = [
            record.getMessage()
            for record in caplog.records
            if _CONFLICT_WARN_MARKER in record.getMessage()
        ]
        assert len(conflict_warns) == 1, (
            f"Expected exactly one conflict WARN; got: {conflict_warns}"
        )
        assert "dispatcharr_api_key" in conflict_warns[0]
        assert "api_key" in conflict_warns[0]
        assert "bd-jmi1c" in conflict_warns[0]

    def test_identical_values_do_not_fire_conflict_warn(
        self, _reset_settings_state, caplog
    ):
        """The idempotent post-mirror state — both fields populated with the
        same value — is the steady state ECM itself produces via
        ``save_settings()``. No WARN should fire on this shape."""
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "same-value",
            "api_key": "same-value",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()

        assert not any(
            _CONFLICT_WARN_MARKER in record.getMessage()
            for record in caplog.records
        ), "Conflict WARN fired when both fields hold identical values"

    def test_conflict_warn_fires_once_per_process(
        self, _reset_settings_state, caplog
    ):
        """Like the deprecation WARN, the conflict WARN is one-shot. Two
        ``load_settings`` calls within the same process — bracketed by a
        cache clear so the second call re-reads disk — should still only
        produce one WARN unless ``clear_settings_cache`` also resets the
        flag (see the dedicated test below)."""
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "canonical-WINNER",
            "api_key": "legacy-STALE",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            # First load: WARN fires.
            config.load_settings()
            # Cached reload: does not re-read or warn.
            config.load_settings()

        conflict_count = sum(
            1 for record in caplog.records
            if _CONFLICT_WARN_MARKER in record.getMessage()
        )
        assert conflict_count == 1, (
            f"Cached reload should not re-emit conflict WARN; got {conflict_count}"
        )

    def test_clear_settings_cache_resets_conflict_warn_flag(
        self, _reset_settings_state, caplog
    ):
        """``clear_settings_cache()`` resets the conflict flag so the next
        load re-fires the WARN. Without this, multi-load tests in a single
        process would silently lose the warning after the first call."""
        settings_file = _reset_settings_state
        _write_settings(settings_file, {
            "dispatcharr_api_key": "canonical-WINNER",
            "api_key": "legacy-STALE",
        })

        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()
        first_count = sum(
            1 for record in caplog.records
            if _CONFLICT_WARN_MARKER in record.getMessage()
        )

        # Reset cache + flag, re-load → WARN should fire again.
        config.clear_settings_cache()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="config"):
            config.load_settings()
        second_count = sum(
            1 for record in caplog.records
            if _CONFLICT_WARN_MARKER in record.getMessage()
        )

        assert first_count == 1
        assert second_count == 1, (
            "clear_settings_cache() should reset the conflict WARN flag"
        )
