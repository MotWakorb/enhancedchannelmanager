"""Tests for MCP server config module."""
import json
import pytest
from pathlib import Path
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
