"""Regression corpus + unit tests for the DBAS outbound SSRF chokepoint.

Bead enhancedchannelmanager-0i2vt.5 — the backend SSRF chokepoint for
operator-configurable outbound destinations (cloud upload). The authoritative
spec is ``docs/security/threat_model_dbas_import.md`` §9 (Addendum B), in
particular §9.4 (the validator requirements handed to this bead) and §9.4
item 8 (the mandatory regression corpus).

These tests are the regression backbone of the egress trust boundary. They are
deliberately exhaustive about the denylist and DNS-rebinding semantics: a future
refactor that quietly drops a range or fails-open on resolution is meant to turn
this file red.

All DNS / transport is mocked — no real network is touched, so the corpus is
deterministic in CI.
"""
import ipaddress
import socket
from unittest.mock import patch

import pytest

from security import ssrf
from security.ssrf import (
    SSRFError,
    SSRFMode,
    validate_outbound_url,
)


# ---------------------------------------------------------------------------
# Helpers — mock DNS resolution so the corpus is deterministic.
# ---------------------------------------------------------------------------

def _addrinfo(*ips):
    """Build a ``socket.getaddrinfo``-shaped return for the given IP strings.

    Each entry is ``(family, type, proto, canonname, sockaddr)``. IPv4
    sockaddr is ``(ip, port)``; IPv6 is ``(ip, port, flowinfo, scopeid)``.
    """
    out = []
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if addr.version == 6:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)))
    return out


def _patch_dns(*ips):
    """Patch the resolver used by the SSRF module to return ``ips``."""
    return patch.object(ssrf, "_resolve", lambda host, port: [ipaddress.ip_address(i) for i in ips])


# ---------------------------------------------------------------------------
# Scheme allowlist (§9.4 item 2 — scheme; corpus: non-http(s) schemes).
# ---------------------------------------------------------------------------

class TestSchemeAllowlist:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/_x",
        "data:text/plain,hello",
        "dict://example.com:11211/",
        "ldap://example.com/",
        "//example.com/no-scheme",
    ])
    @pytest.mark.parametrize("mode", [SSRFMode.LAN_FRIENDLY, SSRFMode.PUBLIC_ONLY])
    def test_non_http_schemes_rejected_both_modes(self, url, mode):
        with pytest.raises(SSRFError):
            validate_outbound_url(url, mode)

    def test_http_scheme_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_outbound_url("http://example.com/path", SSRFMode.LAN_FRIENDLY)
        assert result.scheme == "http"

    def test_https_scheme_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_outbound_url("https://example.com/path", SSRFMode.LAN_FRIENDLY)
        assert result.scheme == "https"

    def test_missing_hostname_rejected(self):
        with pytest.raises(SSRFError):
            validate_outbound_url("http:///nohost", SSRFMode.LAN_FRIENDLY)


# ---------------------------------------------------------------------------
# Always-on denylist — rejected in BOTH modes, no opt-out (§9.4 item 2;
# corpus: each always-on range, v4 and v6). Tested as literal-IP hosts so the
# denylist itself is under test, independent of DNS.
# ---------------------------------------------------------------------------

ALWAYS_ON_DENIED = [
    # IMDS + link-local
    "http://169.254.169.254/latest/meta-data/",   # AWS/GCP/Azure IMDS
    "http://169.254.1.1/",                          # link-local 169.254.0.0/16
    # CGNAT 100.64.0.0/10
    "http://100.64.0.1/",
    "http://100.127.255.254/",
    # 0.0.0.0/8 wildcard
    "http://0.0.0.0/",
    "http://0.1.2.3/",
    # multicast 224.0.0.0/4
    "http://224.0.0.1/",
    "http://239.255.255.250/",
    # IPv6 loopback ::1
    "http://[::1]/",
    # IPv6 ULA fc00::/7
    "http://[fc00::1]/",
    "http://[fd12:3456:789a::1]/",
    # IPv6 link-local fe80::/10
    "http://[fe80::1]/",
    # IPv6 site-local fec0::/10 (deprecated but still denied)
    "http://[fec0::1]/",
    # IPv6 unspecified ::
    "http://[::]/",
    # IPv6 multicast ff00::/8
    "http://[ff02::1]/",
    # IPv4-mapped IPv6 of ALWAYS-on denied targets — must be unwrapped +
    # re-checked. (Mapped 127.0.0.1 / RFC1918 follow the LAN toggle, NOT the
    # always-on list — see TestWizardToggledBand below.)
    "http://[::ffff:169.254.169.254]/",            # mapped IMDS (169.254/16 always-on)
    "http://[::ffff:0.0.0.0]/",                     # mapped wildcard (0.0.0.0/8 always-on)
    "http://[::ffff:100.64.0.1]/",                  # mapped CGNAT (100.64/10 always-on)
]


class TestAlwaysOnDenylist:
    @pytest.mark.parametrize("url", ALWAYS_ON_DENIED)
    @pytest.mark.parametrize("mode", [SSRFMode.LAN_FRIENDLY, SSRFMode.PUBLIC_ONLY])
    def test_always_on_denied_in_both_modes(self, url, mode):
        # Literal-IP host — no DNS needed. If a hostname slipped through to the
        # resolver here that would itself be a bug, so we do not patch DNS.
        with pytest.raises(SSRFError):
            validate_outbound_url(url, mode)

    def test_mapped_imds_via_dns_rejected(self):
        # A hostname that resolves to the IPv4-mapped IMDS address.
        with _patch_dns("::ffff:169.254.169.254"):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://evil.example.com/", SSRFMode.LAN_FRIENDLY)

    def test_no_settings_key_can_disable_denylist(self):
        # §9.4 item 2 / B6: the always-on denylist is unconditional in code.
        # There must be no module-level allowlist that re-enables a denied IP.
        # Assert the public surface exposes no such hook.
        assert not hasattr(ssrf, "ALLOWLIST")
        assert not hasattr(ssrf, "add_to_allowlist")


# ---------------------------------------------------------------------------
# Wizard-toggled band: RFC1918 + loopback (§9.4 item 2 — wizard-toggled;
# corpus: RFC1918 allowed in LAN-friendly / rejected in public-only).
# ---------------------------------------------------------------------------

class TestWizardToggledBand:
    @pytest.mark.parametrize("url", [
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.10/",
        "http://127.0.0.1/",
        "http://127.0.0.53/",
    ])
    def test_rfc1918_and_loopback_allowed_in_lan_friendly(self, url):
        result = validate_outbound_url(url, SSRFMode.LAN_FRIENDLY)
        assert result is not None

    @pytest.mark.parametrize("url", [
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.10/",
        "http://127.0.0.1/",
    ])
    def test_rfc1918_and_loopback_blocked_in_public_only(self, url):
        with pytest.raises(SSRFError):
            validate_outbound_url(url, SSRFMode.PUBLIC_ONLY)

    def test_mapped_rfc1918_follows_toggle(self):
        # ::ffff:<RFC1918> unwraps to the v4 rules: allowed in LAN-friendly,
        # blocked in public-only (§9.4 item 2 "re-checked against IPv4 rules").
        result = validate_outbound_url("http://[::ffff:192.168.1.10]/", SSRFMode.LAN_FRIENDLY)
        assert result is not None
        with pytest.raises(SSRFError):
            validate_outbound_url("http://[::ffff:192.168.1.10]/", SSRFMode.PUBLIC_ONLY)

    def test_mapped_loopback_follows_toggle(self):
        # ::ffff:127.0.0.1 unwraps to 127.0.0.1 (loopback band): allowed in
        # LAN-friendly, blocked in public-only.
        result = validate_outbound_url("http://[::ffff:127.0.0.1]/", SSRFMode.LAN_FRIENDLY)
        assert result is not None
        with pytest.raises(SSRFError):
            validate_outbound_url("http://[::ffff:127.0.0.1]/", SSRFMode.PUBLIC_ONLY)

    def test_ipv6_loopback_always_denied_even_lan_friendly(self):
        # ::1 is in the ALWAYS-on denylist, NOT the toggled band — it stays
        # denied even in LAN-friendly mode.
        with pytest.raises(SSRFError):
            validate_outbound_url("http://[::1]/", SSRFMode.LAN_FRIENDLY)

    def test_public_address_allowed_in_both_modes(self):
        for mode in (SSRFMode.LAN_FRIENDLY, SSRFMode.PUBLIC_ONLY):
            with _patch_dns("93.184.216.34"):
                result = validate_outbound_url("http://example.com/", mode)
            assert str(result.ip) == "93.184.216.34"


# ---------------------------------------------------------------------------
# Unicode / punycode hostnames (§9.4 item 8 — corpus: unicode/punycode that
# decodes to a denied target).
# ---------------------------------------------------------------------------

class TestUnicodePunycode:
    def test_punycode_host_resolving_to_denied_rejected(self):
        with _patch_dns("169.254.169.254"):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://xn--80ak6aa92e.example/", SSRFMode.LAN_FRIENDLY)

    def test_unicode_host_resolving_to_denied_rejected(self):
        # A unicode hostname (IDNA) that the resolver maps to IMDS.
        with _patch_dns("169.254.169.254"):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://рф.example/", SSRFMode.LAN_FRIENDLY)

    def test_punycode_host_resolving_to_public_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = validate_outbound_url("http://xn--80ak6aa92e.example/", SSRFMode.LAN_FRIENDLY)
        assert str(result.ip) == "93.184.216.34"


# ---------------------------------------------------------------------------
# DNS-rebinding mitigation: resolve-then-connect-by-IP (§9.4 item 3; corpus:
# two-A-record (one allowed, one denied) -> rejected; resolution that changes
# between validate and connect -> connection still goes to the validated IP).
# ---------------------------------------------------------------------------

class TestDnsRebinding:
    def test_two_records_one_denied_rejects_whole_request(self):
        # One public + one IMDS record -> reject the WHOLE request (do not
        # "pick the allowed one").
        with _patch_dns("93.184.216.34", "169.254.169.254"):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://evil.example.com/", SSRFMode.LAN_FRIENDLY)

    def test_two_records_both_allowed_passes(self):
        with _patch_dns("93.184.216.34", "1.1.1.1"):
            result = validate_outbound_url("http://example.com/", SSRFMode.LAN_FRIENDLY)
        # Connects by ONE validated IP (the first), hostname preserved.
        assert str(result.ip) in {"93.184.216.34", "1.1.1.1"}
        assert result.hostname == "example.com"

    def test_resolve_once_result_carries_validated_ip(self):
        # The returned target pins to the validated IP; downstream connect uses
        # result.ip, NOT a second lookup of the hostname.
        with _patch_dns("93.184.216.34"):
            result = validate_outbound_url("https://example.com/path", SSRFMode.PUBLIC_ONLY)
        assert str(result.ip) == "93.184.216.34"
        assert result.hostname == "example.com"   # preserved for SNI / Host
        assert result.scheme == "https"

    def test_no_second_dns_lookup_between_validate_and_connect(self):
        # Resolver returns a benign IP on the FIRST (validation) call and IMDS
        # on any SUBSEQUENT call. The validated target must carry the benign IP
        # and the connect path must NOT re-resolve. We assert the resolver was
        # invoked exactly once during validation.
        calls = {"n": 0}

        def flaky(host, port):
            calls["n"] += 1
            if calls["n"] == 1:
                return [ipaddress.ip_address("93.184.216.34")]
            return [ipaddress.ip_address("169.254.169.254")]  # rebind to IMDS

        with patch.object(ssrf, "_resolve", flaky):
            result = validate_outbound_url("http://evil.example.com/", SSRFMode.LAN_FRIENDLY)

        assert calls["n"] == 1
        assert str(result.ip) == "93.184.216.34"


# ---------------------------------------------------------------------------
# Fail-closed on unresolvable host (§9.4 — the cloud path FAILS CLOSED, unlike
# the media-server validator which fails open).
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_unresolvable_host_rejected(self):
        def boom(host, port):
            raise socket.gaierror("Name or service not known")

        with patch.object(ssrf, "_resolve", boom):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://does-not-exist.invalid/", SSRFMode.LAN_FRIENDLY)

    def test_empty_resolution_rejected(self):
        with patch.object(ssrf, "_resolve", lambda host, port: []):
            with pytest.raises(SSRFError):
                validate_outbound_url("http://example.com/", SSRFMode.LAN_FRIENDLY)


# ---------------------------------------------------------------------------
# Redirect re-validation (§9.4 item 4; corpus: 302 -> IMDS blocked;
# https->http downgrade blocked; chain over cap blocked).
# ---------------------------------------------------------------------------

class TestRedirectValidation:
    def test_redirect_to_imds_rejected(self):
        with _patch_dns("169.254.169.254"):
            with pytest.raises(SSRFError):
                ssrf.validate_redirect(
                    "https://example.com/",
                    "http://169.254.169.254/latest/meta-data/",
                    SSRFMode.LAN_FRIENDLY,
                )

    def test_https_to_http_downgrade_rejected(self):
        # Target host is a benign public IP, but the scheme downgrades.
        with _patch_dns("93.184.216.34"):
            with pytest.raises(SSRFError):
                ssrf.validate_redirect(
                    "https://example.com/",
                    "http://example.com/elsewhere",
                    SSRFMode.LAN_FRIENDLY,
                )

    def test_http_to_https_upgrade_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = ssrf.validate_redirect(
                "http://example.com/",
                "https://example.com/secure",
                SSRFMode.LAN_FRIENDLY,
            )
        assert result.scheme == "https"

    def test_same_scheme_redirect_to_public_allowed(self):
        with _patch_dns("93.184.216.34"):
            result = ssrf.validate_redirect(
                "https://example.com/",
                "https://cdn.example.com/file",
                SSRFMode.LAN_FRIENDLY,
            )
        assert result.hostname == "cdn.example.com"

    def test_redirect_chain_over_cap_rejected(self):
        # follow_redirects-style cap: a chain deeper than MAX_REDIRECTS rejects.
        with pytest.raises(SSRFError):
            ssrf.check_redirect_depth(ssrf.MAX_REDIRECTS + 1)

    def test_redirect_chain_within_cap_ok(self):
        # Does not raise.
        ssrf.check_redirect_depth(ssrf.MAX_REDIRECTS)


# ---------------------------------------------------------------------------
# ipaddress backstop (§9.5 residual mitigation): even ranges not explicitly
# enumerated fail closed via is_private / is_reserved / is_loopback / etc.
# ---------------------------------------------------------------------------

class TestIpaddressBackstop:
    @pytest.mark.parametrize("ip", [
        "192.0.2.1",      # TEST-NET-1 (reserved/documentation)
        "198.51.100.1",   # TEST-NET-2
        "203.0.113.1",    # TEST-NET-3
        "240.0.0.1",      # reserved (future use)
    ])
    def test_reserved_ranges_denied_in_both_modes(self, ip):
        # These are is_reserved / is_private-ish per stdlib and must be denied
        # even though they are not in the explicit CGNAT/IMDS list.
        for mode in (SSRFMode.LAN_FRIENDLY, SSRFMode.PUBLIC_ONLY):
            with pytest.raises(SSRFError):
                validate_outbound_url(f"http://{ip}/", mode)


# ---------------------------------------------------------------------------
# Mode resolution from the settings store (single global setting; default
# LAN-friendly per ADR-012 D4 / §9.4 item 7).
# ---------------------------------------------------------------------------

class TestModeFromSettings:
    def test_default_mode_is_lan_friendly(self):
        from config import DispatcharrSettings, clear_settings_cache
        clear_settings_cache()
        s = DispatcharrSettings()
        assert s.ssrf_outbound_mode == "lan_friendly"

    def test_get_ssrf_mode_reads_setting(self):
        import config

        class _S:
            ssrf_outbound_mode = "public_only"

        with patch.object(config, "get_settings", lambda: _S()):
            assert ssrf.get_ssrf_mode() is SSRFMode.PUBLIC_ONLY

    def test_get_ssrf_mode_defaults_lan_friendly_on_bad_value(self):
        import config

        class _S:
            ssrf_outbound_mode = "garbage"

        with patch.object(config, "get_settings", lambda: _S()):
            assert ssrf.get_ssrf_mode() is SSRFMode.LAN_FRIENDLY
