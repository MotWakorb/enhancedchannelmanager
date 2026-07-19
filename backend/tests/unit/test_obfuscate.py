"""
Tests for backend/obfuscate.py (bd-k6ud9).

Regression coverage for a crash discovered live: ``obfuscate_url`` accessed
``parsed.port`` unguarded. Python's ``urlparse().port`` raises ``ValueError``
for a port outside 0-65535 (or non-numeric), and that exception was
uncaught — so a hostile URL (e.g. one embedded in a reported stack trace)
crashed the caller instead of being safely obfuscated.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from obfuscate import obfuscate_text, obfuscate_url


class TestObfuscateUrlOutOfRangePort:
    def test_out_of_range_port_does_not_raise(self):
        # Must not raise — this is the regression under test.
        obfuscate_url("http://host:99999/path")

    def test_out_of_range_port_treated_as_no_port(self):
        result = obfuscate_url("http://host:99999/path")
        assert result == "http://example.com/path"

    def test_port_above_16bit_max_does_not_raise(self):
        obfuscate_url("http://host:65536/path")

    def test_negative_port_does_not_raise(self):
        obfuscate_url("http://user:pass@host:-1/path")

    def test_non_numeric_port_does_not_raise(self):
        obfuscate_url("http://host:abc/path")

    def test_valid_port_still_obfuscated_normally(self):
        result = obfuscate_url("http://host:8080/path")
        assert result == "http://example.com:80/path"

    def test_no_port_still_obfuscated_normally(self):
        result = obfuscate_url("http://host/path")
        assert result == "http://example.com/path"

    def test_ipv6_host_with_out_of_range_port_does_not_raise(self):
        obfuscate_url("http://[::1]:99999/path")


class TestObfuscateTextOutOfRangePort:
    """The stack-trace scrubbing entry point used by client_errors.py."""

    def test_hostile_url_in_text_does_not_raise(self):
        text = "TypeError at http://host:99999/path in bundle.js:42:17"
        obfuscate_text(text)

    def test_hostile_url_in_text_is_obfuscated(self):
        text = "fetch failed: http://host:99999/stale-bundle.js"
        result = obfuscate_text(text)
        assert "host:99999" not in result
        # Parse the redacted URL out of the text and check the hostname
        # exactly (not a raw substring check) — an "X in result" check
        # against a hostname flags CodeQL py/incomplete-url-substring-
        # sanitization, since that pattern is unsafe for real sanitizer
        # code (e.g. "evil-example.com" would also match). This is test
        # assertion code, not a sanitizer, but the exact-hostname check
        # is strictly more precise anyway.
        match = re.search(r"https?://\S+", result)
        assert match is not None
        assert urlparse(match.group(0)).hostname == "example.com"
