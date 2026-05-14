"""Tests for MCP server config module."""
import json
from unittest.mock import patch


class TestGetMCPApiKey:
    """Tests for config.get_mcp_api_key()."""

    def test_reads_key_from_settings(self, tmp_path):
        """Returns the API key from settings.json."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_api_key": "test-key-123"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == "test-key-123"

    def test_returns_empty_when_no_file(self, tmp_path):
        """Returns empty string when settings.json doesn't exist."""
        missing_file = tmp_path / "nonexistent.json"

        with patch("config.SETTINGS_FILE", missing_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == ""

    def test_returns_empty_when_key_missing(self, tmp_path):
        """Returns empty string when mcp_api_key field is absent."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"url": "http://test"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == ""

    def test_returns_empty_on_malformed_json(self, tmp_path):
        """Returns empty string when settings.json is not valid JSON."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("not json {{{")

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == ""


class TestGetMCPApiKeyStatus:
    """Tests for config.get_mcp_api_key_status() — bd-ix1g6 self-diagnosing /health.

    Returns a (key, status) tuple. Status is one of:
      "ok"             — file exists, JSON valid, mcp_api_key field present, non-empty
      "file_not_found" — settings.json does not exist on the mounted volume
      "invalid_json"   — settings.json exists but is not valid JSON
      "field_missing"  — JSON valid but does not contain mcp_api_key (legacy file
                          from before the MCP feature shipped, never re-saved)
      "field_empty"    — JSON valid, field present, but value is empty string
                          (no key generated yet, or key was revoked)
    """

    def test_ok_when_key_present(self, tmp_path):
        """Returns (key, 'ok') when settings.json has a non-empty mcp_api_key."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_api_key": "real-key-abc"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == "real-key-abc"
            assert status == "ok"

    def test_file_not_found_status(self, tmp_path):
        """Returns ('', 'file_not_found') when settings.json doesn't exist.

        This is the most common deployment-misconfiguration signature: the MCP
        container's /config volume mount is empty (different named volume,
        wrong bind path, or ECM never wrote settings.json because the user
        never saved anything). Reporting this distinctly lets the operator
        diagnose without container shell access (bd-ix1g6).
        """
        missing = tmp_path / "absent.json"

        with patch("config.SETTINGS_FILE", missing):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "file_not_found"

    def test_invalid_json_status(self, tmp_path):
        """Returns ('', 'invalid_json') when settings.json is corrupted."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{not valid json")

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "invalid_json"

    def test_field_missing_status(self, tmp_path):
        """Returns ('', 'field_missing') when JSON is valid but no mcp_api_key key."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"url": "http://test", "username": "u"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "field_missing"

    def test_field_empty_status(self, tmp_path):
        """Returns ('', 'field_empty') when mcp_api_key field is present but blank.

        Most likely: ECM wrote settings.json after the MCP feature shipped, but
        the user has not generated a key (or has revoked it).
        """
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_api_key": "", "url": "http://test"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "field_empty"

    def test_get_mcp_api_key_preserves_existing_behavior(self, tmp_path):
        """get_mcp_api_key() still returns just the key (back-compat).

        Existing callers (auth middleware, ecm_client) read only the key, not
        the status. The status-aware helper is additive; the original helper
        keeps its signature so we don't have to retrofit every call site.
        """
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_api_key": "k"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == "k"
