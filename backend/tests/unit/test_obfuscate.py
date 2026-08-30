"""
Tests for backend/obfuscate.py (bd-k6ud9).

Regression coverage for a crash discovered live: ``obfuscate_url`` accessed
``parsed.port`` unguarded. Python's ``urlparse().port`` raises ``ValueError``
for a port outside 0-65535 (or non-numeric), and that exception was
uncaught — so a hostile URL (e.g. one embedded in a reported stack trace)
crashed the caller instead of being safely obfuscated.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlparse

import pytest

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


# ---------------------------------------------------------------------------
# bead …-d0hoc: the layered credential rules.
#
# LAYER ATTRIBUTION. The artifact-level invariant ("no credential value in any
# bundle member") is proven end-to-end in
# ``tests/routers/test_d0hoc_debug_bundle_credential_leak.py``, through the real
# bundle builder. These are UNIT tests of the individual layers, and they exist
# because the bundle's final literal sweep is broad enough to mask every layer
# beneath it: with only the artifact-level tests, deleting the value rule from
# ``obfuscate_url`` outright left all 28 of them green. A layer whose removal no
# test notices is a layer nobody is maintaining.
# ---------------------------------------------------------------------------
from obfuscate import REDACTED, scrub_credential_values

_SECRET = "d0hocUnitPass77"
_IDENTITY = "d0hocUnitUser11"
_SECRETS = frozenset({_SECRET})
_IDENTITIES = frozenset({_IDENTITY})


class TestValueRuleReachesShapesTheShapeRuleCannot:
    """Layer 1, isolated: URLs whose credential no path-shape rule can find."""

    def test_hls_playlist_path_is_scrubbed_by_value(self):
        # Trailing segment is ``index.m3u8``, not ``<digits>.<ext>``, so the
        # XtreamCodes tail rule does not fire at all.
        url = f"http://host:8080/hls/{_IDENTITY}/{_SECRET}/1234/index.m3u8"
        assert _SECRET in obfuscate_url(url), "shape rule unexpectedly reached this"
        result = obfuscate_url(url, _SECRETS, _IDENTITIES)
        assert _SECRET not in result
        assert _IDENTITY not in result
        assert "1234" in result  # the address survives

    def test_a_decorated_credential_segment_loses_the_whole_segment(self):
        # A provider that decorates the credential segment is still handing the
        # password over, so containment (not equality) is the rule.
        url = f"http://host/vod/{_IDENTITY}/{_SECRET}-hd/playlist.m3u8"
        assert _SECRET in obfuscate_url(url), "shape rule unexpectedly reached this"
        result = obfuscate_url(url, _SECRETS, _IDENTITIES)
        assert _SECRET not in result

    def test_percent_encoded_credential_is_not_a_way_through(self):
        secret = "p@ss d0hoc"
        url = f"http://host/gate/{quote(secret, safe='')}/x/index.m3u8"
        assert quote(secret, safe="") in obfuscate_url(url), (
            "shape rule unexpectedly reached this"
        )
        result = obfuscate_url(url, frozenset({secret}), frozenset())
        assert quote(secret, safe="") not in result
        assert secret not in result


class TestQueryStringCredentials:
    """Layer 3, isolated. No path segment carries the credential at all, so
    every path-shaped rule — the shipped one and any widening of it — is blind."""

    def test_credential_named_query_params_lose_their_values(self):
        url = "http://host/get.php?username=someone&password=hunter2&type=m3u_plus"
        result = obfuscate_url(url)
        assert "hunter2" not in result
        assert "someone" not in result
        assert "type=m3u_plus" in result  # the diagnostic half survives

    def test_an_unconventionally_named_param_is_caught_by_value(self):
        url = f"http://host/play?u={_IDENTITY}&p={_SECRET}"
        assert _SECRET in obfuscate_url(url), "name rule unexpectedly reached this"
        result = obfuscate_url(url, _SECRETS, _IDENTITIES)
        assert _SECRET not in result
        assert _IDENTITY not in result

    def test_a_clean_query_is_left_byte_identical(self):
        assert obfuscate_url("http://host/a?x=1&y=2") == "http://example.com/a?x=1&y=2"


class TestXcPathTailAtAnyDepth:
    """Layer 2, isolated. The rule is the TAIL shape, not a prefix list — the
    distinction that separates a fix from a pinned reproduction."""

    @pytest.mark.parametrize("path", [
        "/u/p/13580.ts",                 # the only shape the original rule matched
        "/live/u/p/13580.ts",
        "/movie/u/p/99.mkv",
        "/series/u/p/4242.mp4",
        "/server1/live/u/p/1.ts",
        "/a/b/c/live/u/p/5.ts",
    ])
    def test_the_credential_pair_is_blanked_at_any_prefix_depth(self, path):
        result = obfuscate_url(f"http://host:8080{path}")
        assert "/user/pass/" in result, result
        assert result.endswith(path.rsplit("/", 1)[-1])

    def test_a_non_stream_path_is_untouched(self):
        assert obfuscate_url("http://host/api/v1/status") == "http://example.com/api/v1/status"


class TestLiteralSweepOverNonUrlText:
    """Layer 5, isolated. ``_URL_RE`` cannot see a credential a log line names
    outside any URL, and a bundle member is only as safe as its least
    URL-shaped byte."""

    def test_a_bare_credential_in_a_log_line_is_scrubbed(self):
        line = f"[M3U] auth rejected for user={_IDENTITY} pass={_SECRET}"
        result = obfuscate_text(line, _SECRETS, _IDENTITIES)
        assert _SECRET not in result
        assert _IDENTITY not in result
        assert REDACTED in result

    def test_the_json_escaped_form_is_scrubbed(self):
        secret = 'quote"and\\slash'
        payload = json.dumps({"note": f"saw {secret} here"})
        assert secret not in payload  # json escaped it; the raw form is absent
        result = scrub_credential_values(payload, frozenset({secret}), frozenset())
        assert json.dumps(secret)[1:-1] not in result

    def test_a_value_shorter_than_the_sweep_floor_is_left_alone(self):
        """A NAMED gap, asserted so it cannot change silently. An unanchored
        sweep for a 1-character value would replace every incidental occurrence
        and destroy the artifact it protects; below the floor only the
        structural layers (which are precise about position) apply."""
        text = "channel 1 at 11:11 on stream 1"
        assert scrub_credential_values(text, frozenset({"1"}), frozenset()) == text

    def test_the_longest_value_is_replaced_first(self):
        """A username that is a substring of the password must not leave a
        half-scrubbed remainder behind."""
        user, password = "d0hocUser", "d0hocUserSecretTail"
        result = scrub_credential_values(
            f"login {user} / {password}", frozenset({password}), frozenset({user})
        )
        assert "SecretTail" not in result
        assert result == f"login {REDACTED} / {REDACTED}"


class TestDefaultsAreUnchangedForExistingCallers:
    """``secrets``/``identities`` default to empty, so every pre-existing caller
    keeps working and gains the shape, query and authority layers for free."""

    def test_no_arguments_still_obfuscates_the_host(self):
        assert obfuscate_url("http://real-host.example:8080/x") == "http://example.com:80/x"

    def test_userinfo_credentials_die_with_the_authority(self):
        result = obfuscate_url("http://someone:hunter2@real-host.example/playlist.m3u8")
        assert "hunter2" not in result
        assert "someone" not in result
