"""Unit tests for bd-gy5nd URL-hostname → M3U provider resolution.

bd-gy5nd replaces the broken bd-5g7kx channel-streams fallback (which
called ``GET /api/channels/channels/<channel_uuid>/streams/`` with a
proxy-session UUID — Dispatcharr returned the SPA HTML rather than 404,
generating ``provider_resolution_failed`` WARN spam every 10s and never
attributing the stream) with URL-hostname matching against the
configured M3U accounts.

These tests lock the helper-level contract:

* ``_parse_url_hostname`` — bare hostname extraction (case, port, query
  string, scheme tolerance).
* ``_match_provider_from_url`` — match outcomes (M3U match found, no
  match → bare hostname fallback, unparsable URL → all None).
"""
from __future__ import annotations

import pytest

from bandwidth_tracker import (
    _match_provider_from_url,
    _parse_url_hostname,
)


class TestParseUrlHostname:
    """Hostname parsing isolated from the M3U-match logic so we can
    pin the input-tolerance contract without dragging an accounts
    fixture into every case."""

    def test_extracts_bare_hostname(self):
        """Plain ``.ts`` URL → lowercased hostname."""
        url = "https://infinity.gives/live/mot/16118141/85796.ts"
        assert _parse_url_hostname(url) == "infinity.gives"

    def test_lowercases_uppercase_hostname(self):
        """Mixed-case hostname → lowercased (the M3U match is
        case-insensitive — the matcher lowercases both sides)."""
        url = "https://INFINITY.gives/live/x/y.ts"
        assert _parse_url_hostname(url) == "infinity.gives"

    def test_strips_port(self):
        """Hostname with port → bare hostname (no port). The M3U match
        compares hostnames only — port differences between the active
        URL and the configured M3U server URL must not block the match."""
        url = "https://infinity.gives:8080/live/x/y.ts"
        assert _parse_url_hostname(url) == "infinity.gives"

    def test_tolerates_query_string(self):
        """Active URLs carry session-token query strings — must not
        affect hostname parse."""
        url = "https://infinity.gives/live/x/y.ts?session=tok123"
        assert _parse_url_hostname(url) == "infinity.gives"

    def test_returns_none_for_none(self):
        """``None`` input → ``None`` output (caller guard)."""
        assert _parse_url_hostname(None) is None

    def test_returns_none_for_empty_string(self):
        """Empty string → ``None``."""
        assert _parse_url_hostname("") is None

    def test_returns_none_for_non_string(self):
        """Defensive: an unexpected non-string (Dispatcharr serialization
        glitch) must not raise."""
        assert _parse_url_hostname(12345) is None  # type: ignore[arg-type]

    def test_returns_none_for_scheme_only(self):
        """Scheme-only string (no host) → ``None``."""
        assert _parse_url_hostname("https://") is None


class TestMatchProviderFromUrl:
    """End-to-end URL → ``(provider_id, provider_name, hostname)``
    tuple. Locks the operator-visible contract: when an M3U source is
    configured, surface its name; otherwise surface the bare hostname
    so the operator still sees something concrete."""

    def test_m3u_match_by_hostname_returns_account_id_and_name(self):
        """The PO's exact scenario: ``infinity.gives`` stream URL and
        an ``Infinity TV`` M3U account with ``server_url`` pointing at
        the same host."""
        url = "https://infinity.gives/live/mot/16118141/85796.ts"
        accounts = [
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
        assert _match_provider_from_url(url, accounts) == (6, "Infinity TV", "infinity.gives")

    def test_m3u_match_is_port_tolerant(self):
        """Active URL on port 443 (default https), M3U ``server_url``
        explicit-port 8080 — hostnames still match."""
        url = "https://infinity.gives/live/85796.ts"
        accounts = [
            {"id": 6, "name": "Infinity TV", "server_url": "http://infinity.gives:8080/get.php"},
        ]
        provider_id, provider_name, hostname = _match_provider_from_url(url, accounts)
        assert provider_id == 6
        assert provider_name == "Infinity TV"

    def test_no_match_returns_bare_hostname_as_provider_name(self):
        """No M3U account configured for the URL's hostname →
        ``provider_name`` is the bare hostname so the operator sees
        ``infinity.gives`` instead of "Unknown"."""
        url = "https://infinity.gives/live/85796.ts"
        accounts = [
            {"id": 1, "name": "Other", "server_url": "https://other.example/list.m3u"},
        ]
        provider_id, provider_name, hostname = _match_provider_from_url(url, accounts)
        assert provider_id is None
        assert provider_name == "infinity.gives"
        assert hostname == "infinity.gives"

    def test_empty_accounts_list_returns_bare_hostname(self):
        """Empty M3U accounts list (Dispatcharr fetch returned nothing
        OR ECM hasn't been configured with any sources yet) — bare
        hostname still surfaces so the badge has a label."""
        url = "https://infinity.gives/live/85796.ts"
        provider_id, provider_name, hostname = _match_provider_from_url(url, [])
        assert provider_id is None
        assert provider_name == "infinity.gives"
        assert hostname == "infinity.gives"

    def test_unparsable_url_returns_all_none(self):
        """When the URL itself is unparsable (None, empty, no scheme +
        no host), every field stays ``None`` — the caller treats this
        as "no provider information available"."""
        assert _match_provider_from_url(None, []) == (None, None, None)
        assert _match_provider_from_url("", []) == (None, None, None)

    def test_skips_malformed_account_entries(self):
        """Defensive: an account dict with no ``server_url`` (or a
        non-string ``server_url``) is skipped rather than raised on —
        Dispatcharr serialization changes must not break the resolver."""
        url = "https://infinity.gives/live/85796.ts"
        accounts = [
            {"id": 1, "name": "No URL"},  # No server_url at all.
            {"id": 2, "name": "Bad URL", "server_url": None},  # NULL.
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"},
        ]
        provider_id, provider_name, _ = _match_provider_from_url(url, accounts)
        assert provider_id == 6
        assert provider_name == "Infinity TV"

    def test_first_match_wins_on_duplicate_hostnames(self):
        """If two M3U accounts share a hostname (rare — same upstream
        provider configured twice for different group filters), the
        first match wins. Documented behavior so the resolver is
        deterministic across runs."""
        url = "https://infinity.gives/live/85796.ts"
        accounts = [
            {"id": 5, "name": "Infinity Group A", "server_url": "https://infinity.gives/a.m3u"},
            {"id": 6, "name": "Infinity Group B", "server_url": "https://infinity.gives/b.m3u"},
        ]
        provider_id, provider_name, _ = _match_provider_from_url(url, accounts)
        assert provider_id == 5
        assert provider_name == "Infinity Group A"
