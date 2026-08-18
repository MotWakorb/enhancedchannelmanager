"""Tests for MCP server config module."""
from unittest.mock import patch

import pytest


class TestGetMCPApiKey:
    """Tests for config.get_mcp_api_key()."""

    def test_reads_key_from_settings(self, tmp_path):
        """Returns the API key from the credential projection."""
        settings_file = tmp_path / "api-key"
        settings_file.write_text("test-key-123\n")

        with patch("config.MCP_KEY_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == "test-key-123"

    def test_returns_empty_when_no_file(self, tmp_path):
        """Returns empty string when the credential projection doesn't exist."""
        missing_file = tmp_path / "nonexistent"

        with patch("config.MCP_KEY_FILE", missing_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == ""

    def test_returns_empty_on_multiline_projection(self, tmp_path):
        """Rejects an ambiguous multi-line credential projection."""
        settings_file = tmp_path / "api-key"
        settings_file.write_text("first\nsecond\n")

        with patch("config.MCP_KEY_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == ""


class TestGetMCPApiKeyStatus:
    """Tests for config.get_mcp_api_key_status() — bd-ix1g6 self-diagnosing /health.

    Returns a (key, status) tuple. Status is one of:
      "ok"             — projection contains one non-empty line
      "file_not_found" — projection does not exist
      "invalid_key"    — projection is unreadable or ambiguous
      "field_empty"    — projection is empty (not configured or revoked)
    """

    def test_ok_when_key_present(self, tmp_path):
        """Returns (key, 'ok') when projection has a non-empty credential."""
        settings_file = tmp_path / "api-key"
        settings_file.write_text("real-key-abc\n")

        with patch("config.MCP_KEY_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == "real-key-abc"
            assert status == "ok"

    def test_file_not_found_status(self, tmp_path):
        """Returns ('', 'file_not_found') when the projection doesn't exist.

        This is the most common deployment-misconfiguration signature: the MCP
        container's credential-projection mount is empty (different named
        volume, wrong bind path, or ECM never generated a key). Reporting this
        distinctly lets the operator diagnose without container shell access
        (bd-ix1g6).
        """
        missing = tmp_path / "absent"

        with patch("config.MCP_KEY_FILE", missing):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "file_not_found"

    def test_invalid_key_status(self, tmp_path):
        """Returns ('', 'invalid_key') for a multi-line projection."""
        settings_file = tmp_path / "api-key"
        settings_file.write_text("one\ntwo\n")

        with patch("config.MCP_KEY_FILE", settings_file):
            from config import get_mcp_api_key_status
            key, status = get_mcp_api_key_status()
            assert key == ""
            assert status == "invalid_key"

    def test_field_empty_status(self, tmp_path):
        """Returns ('', 'field_empty') when the projection is present but blank.

        Most likely: ECM created the projection but the user has not generated
        a key (or has revoked it).
        """
        settings_file = tmp_path / "api-key"
        settings_file.write_text("")

        with patch("config.MCP_KEY_FILE", settings_file):
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
        settings_file = tmp_path / "api-key"
        settings_file.write_text("k\n")

        with patch("config.MCP_KEY_FILE", settings_file):
            from config import get_mcp_api_key
            assert get_mcp_api_key() == "k"


class TestMCPAllowedHosts:
    """The sidecar has a small safe default plus explicit operator additions."""

    def test_defaults_cover_loopback_and_compose_service(self):
        from config import get_mcp_allowed_hosts

        assert get_mcp_allowed_hosts("") == (
            "localhost",
            "127.0.0.1",
            "[::1]",
            "ecm-mcp",
        )

    def test_operator_hosts_are_trimmed_and_deduplicated(self):
        from config import get_mcp_allowed_hosts

        assert get_mcp_allowed_hosts(
            " media-box.local,192.168.1.20,media-box.local "
        ) == (
            "localhost",
            "127.0.0.1",
            "[::1]",
            "ecm-mcp",
            "media-box.local",
            "192.168.1.20",
        )

    @pytest.mark.parametrize(
        "value",
        [
            "example.com/health",
            "example.com?path=",
            "example.com#fragment",
            "example.com:6101",
            "*",
            "-bad.example",
        ],
    )
    def test_malformed_or_permissive_entries_fail_closed(self, value):
        from config import get_mcp_allowed_hosts

        with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
            get_mcp_allowed_hosts(value)


class TestMCPAllowedOrigins:
    def test_defaults_are_exact_loopback_origins(self):
        from config import get_mcp_allowed_origins

        origins = get_mcp_allowed_origins("")
        assert "http://localhost" in origins
        assert "https://127.0.0.1" in origins
        assert "*" not in origins

    @pytest.mark.parametrize(
        "value",
        [
            "*",
            "mcp.example.home",
            "https://mcp.example.home/",
            "https://bad host",
            "https://user@mcp.example.home",
            "https://mcp.example.home?credential=value",
            "https://mcp.example.home#fragment",
        ],
    )
    def test_permissive_or_malformed_origin_fails_closed(self, value):
        from config import get_mcp_allowed_origins

        with pytest.raises(ValueError, match="MCP_ALLOWED_ORIGINS"):
            get_mcp_allowed_origins(value)


class TestMCPTrustedProxyIPs:
    def test_exact_addresses_and_bounded_cidrs_are_normalized(self):
        from config import get_mcp_trusted_proxy_ips

        assert get_mcp_trusted_proxy_ips(
            "127.0.0.1, 172.20.0.0/24, 2001:db8::1"
        ) == "127.0.0.1,172.20.0.0/24,2001:db8::1"

    @pytest.mark.parametrize(
        "value",
        ["*", "0.0.0.0/0", "::/0", "not-an-ip", "127.0.0.1,,::1"],
    )
    def test_permissive_or_malformed_proxy_trust_fails_closed(self, value):
        from config import get_mcp_trusted_proxy_ips

        with pytest.raises(ValueError, match="MCP_TRUSTED_PROXY_IPS"):
            get_mcp_trusted_proxy_ips(value)
