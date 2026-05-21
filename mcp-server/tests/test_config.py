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


class TestGetSigningKeyStatus:
    """Tests for config.get_signing_key_status() — bd-buiqr10 Option-A slice.

    The MCP RS verifies OAuth Bearer JWTs OFFLINE using a shared HS256 secret
    from /config/settings.json (ADR-009 §1). This helper reports whether that
    secret is present, using the same four-status pattern as get_mcp_api_key_status().

    The settings.json key is SIGNING_KEY_SETTINGS_JSON_KEY (currently
    'mcp_oauth_signing_secret'). This name is a ONE-LINE change point;
    see config.py comment on SIGNING_KEY_SETTINGS_JSON_KEY for context on the
    pending buiqr.8 contract finalization.

    SECURITY: these tests assert that the secret VALUE is never returned or
    exposed by the status helper — only presence/absence is reported.
    """

    def test_ok_when_signing_key_present(self, tmp_path):
        """Returns ('', 'ok') when mcp_oauth_signing_secret is present and non-empty.

        Note: returns empty string (not the key) — presence only, never the value.
        """
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_oauth_signing_secret": "super-secret-hs256-key"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_signing_key_status
            status_val, status = get_signing_key_status()
            assert status == "ok"
            # SECURITY: the secret value must NEVER be returned
            assert status_val != "super-secret-hs256-key"
            assert "super-secret-hs256-key" not in status_val

    def test_file_not_found_when_settings_missing(self, tmp_path):
        """Returns ('', 'file_not_found') when settings.json doesn't exist."""
        missing = tmp_path / "absent.json"

        with patch("config.SETTINGS_FILE", missing):
            from config import get_signing_key_status
            status_val, status = get_signing_key_status()
            assert status_val == ""
            assert status == "file_not_found"

    def test_invalid_json_when_settings_corrupt(self, tmp_path):
        """Returns ('', 'invalid_json') when settings.json is not valid JSON."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{not valid json")

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_signing_key_status
            status_val, status = get_signing_key_status()
            assert status_val == ""
            assert status == "invalid_json"

    def test_field_missing_when_key_absent_from_json(self, tmp_path):
        """Returns ('', 'signing_key_missing') when JSON is valid but the key is absent."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_api_key": "some-key"}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_signing_key_status
            status_val, status = get_signing_key_status()
            assert status_val == ""
            assert status == "signing_key_missing"

    def test_field_missing_when_key_empty_string(self, tmp_path):
        """Returns ('', 'signing_key_missing') when the field is present but empty."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_oauth_signing_secret": ""}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_signing_key_status
            status_val, status = get_signing_key_status()
            assert status_val == ""
            assert status == "signing_key_missing"

    def test_secret_value_never_in_return(self, tmp_path):
        """SECURITY: get_signing_key_status() never returns the secret value.

        This is a direct security assertion for threat model ID1: the /health
        endpoint is effectively public, and the signing secret must never leak
        through it — not even in a success response.
        """
        secret = "very-secret-signing-key-must-not-appear"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"mcp_oauth_signing_secret": secret}))

        with patch("config.SETTINGS_FILE", settings_file):
            from config import get_signing_key_status
            result_val, result_status = get_signing_key_status()
            # The status string must not contain the secret
            assert secret not in result_val
            assert secret not in result_status
            # The first element must be empty (never the key itself)
            assert result_val == ""
