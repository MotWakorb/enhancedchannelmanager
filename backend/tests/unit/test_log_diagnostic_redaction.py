"""Scoped log-diagnostic redaction: each layer, unregistered values, controls.

``redact_log_diagnostic`` composes two layers: the credential-pattern sweep
(``cloud_storage.upload_security.redact_secrets``) plus the process registry,
then the URL-aware ``obfuscate.obfuscate_text``. Every secret here is
UNREGISTERED unless a test registers it, so a pass proves the named rule
caught it, not the registry.
"""
import pytest

from cloud_storage.upload_security import redact_secrets
from log_utils import redact_log_diagnostic, register_sensitive_values
from obfuscate import obfuscate_text

PW = "S3cretPw-unregistered-81c2"
USER = "alice-unregistered-4f0a"


# ---------------------------------------------------------------------------
# IPv6 authorities: the bracketed host must not end the URL match.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        f"GET http://[2001:db8::1]:8080/get.php?username={USER}&password={PW} failed",
        f"GET http://{USER}:{PW}@[2001:db8::1]:8080/xmltv.php failed",
        f"GET http://[2001:db8::1]:8080/live/{USER}/{PW}/1.ts failed",
        f"GET http://[fe80::1%25eth0]/movie/{USER}/{PW}/9.mkv failed",
    ],
    ids=["query-name", "userinfo", "xc-path", "zone-id"],
)
def test_ipv6_urls_are_scrubbed(text):
    out = redact_log_diagnostic(text)
    assert USER not in out
    assert PW not in out
    assert "2001:db8" not in out
    assert out.startswith("GET ") and out.endswith(" failed")


def test_ipv6_short_query_needs_registration_like_ipv4():
    # ``?u=&p=`` is not a credential-named query; like the IPv4 form it is
    # caught by registered value, which the URL rule can now reach.
    register_sensitive_values(PW, USER)
    out = redact_log_diagnostic(f"http://[2001:db8::1]/get.php?u={USER}&p={PW}")
    assert PW not in out and USER not in out


# ---------------------------------------------------------------------------
# Pattern layer: JSON bodies and API-key header forms.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        f'{{"detail": "bad", "password": "{PW}"}}',
        f"{{'token': '{PW}'}}",
        f'{{\\"password\\": \\"{PW}\\"}}',
        f'{{"api_key": "{PW}"}}',
        f"token: {PW}",
        f"token={PW}",
        f"headers={{'X-API-Key': '{PW}'}}",
        f"X-API-Key: {PW}",
        f"x-api-key={PW}",
    ],
    ids=[
        "json-password", "dict-token", "escaped-json", "json-api-key",
        "token-header", "token-kv", "x-api-key-dict", "x-api-key-header",
        "x-api-key-kv",
    ],
)
def test_pattern_layer_masks_unregistered_credential_forms(text):
    assert PW not in redact_secrets(text)
    assert PW not in redact_log_diagnostic(text)
    # The URL layer alone cannot see these: the pattern layer is the guard.
    assert PW in obfuscate_text(text)


def test_pattern_layer_keeps_key_names_and_structure():
    out = redact_secrets(f'{{"detail": "bad request", "password": "{PW}"}}')
    assert out == '{"detail": "bad request", "password": "***REDACTED***"}'


# ---------------------------------------------------------------------------
# URL layer: Xtream Codes paths, timeshift and non-HTTP userinfo.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected_path"),
    [
        (f"http://prov.example/{USER}/{PW}/123", "/user/pass/123"),
        (f"http://prov.example/live/{USER}/{PW}/123", "/live/user/pass/123"),
        (f"http://prov.example/movie/{USER}/{PW}/123", "/movie/user/pass/123"),
        (
            f"http://prov.example/timeshift/{USER}/{PW}/120/2024-01-01:10-00/123.ts",
            "/timeshift/user/pass/120/2024-01-01:10-00/123.ts",
        ),
        (f"rtmp://prov.example/live/{USER}/{PW}/1.flv", "/live/user/pass/1.flv"),
    ],
    ids=["xc-no-ext", "xc-live-no-ext", "xc-movie-no-ext", "timeshift", "rtmp-path"],
)
def test_url_layer_blanks_unregistered_xc_credentials(text, expected_path):
    out = redact_log_diagnostic(text)
    assert USER not in out and PW not in out
    assert out.endswith(expected_path)
    # The pattern layer alone does not see a bare path credential.
    assert PW in redact_secrets(text)


@pytest.mark.parametrize(
    "url",
    [
        f"rtmp://{USER}:{PW}@prov.example/live",
        f"ftp://{USER}:{PW}@prov.example/x",
        f"redis://{USER}:{PW}@cache:6379/0",
        f"rtsp://{USER}:{PW}@[2001:db8::2]:554/stream",
    ],
    ids=["rtmp", "ftp", "redis", "rtsp-ipv6"],
)
def test_url_layer_destroys_userinfo_in_any_scheme(url):
    out = redact_log_diagnostic(f"connect {url} refused")
    assert USER not in out and PW not in out
    assert out.startswith("connect ") and out.endswith(" refused")
    assert PW in redact_secrets(url)


def test_url_layer_query_name_rule_alone_catches_unregistered_values():
    text = f"http://prov.example/get.php?username={USER}&password={PW}"
    assert PW not in obfuscate_text(text)


# ---------------------------------------------------------------------------
# Controls: ordinary diagnostic text must stay readable.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "PATCH http://ecm.local/api/channels/123 returned 404",
            "PATCH http://example.com/api/channels/123 returned 404",
        ),
        (
            "GET http://disp.local/api/channels/channels/123/ failed",
            "GET http://example.com/api/channels/channels/123/ failed",
        ),
        (
            "GET http://disp.local/api/channels/logos/45 failed",
            "GET http://example.com/api/channels/logos/45 failed",
        ),
        ("max_tokens=100 tokens: 7", "max_tokens=100 tokens: 7"),
        ("Channel 100 moved to 7.5 at 10:00", "Channel 100 moved to 7.5 at 10:00"),
    ],
    ids=["ecm-api-path", "dispatcharr-api-path", "api-depth-three", "token-words", "numbers"],
)
def test_ordinary_text_is_not_over_redacted(text, expected):
    assert redact_log_diagnostic(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Uploaded ecm-backup-2026-11-26.zip to s3://bucket/backups/2026/11 in 3 parts",
        "WebDAV PUT https://dav.example/remote.php/dav/files/ecm/backup.zip -> 201",
        "Drive folder 1AbCdEfGhIjK created; quota 15 GB",
    ],
)
def test_pattern_layer_leaves_cloud_upload_messages_alone(text):
    assert redact_secrets(text) == text


# ---------------------------------------------------------------------------
# Registry sweep minimum length.
# ---------------------------------------------------------------------------

def test_short_registered_values_do_not_mangle_numbers():
    register_sensitive_values("100", "5", "7", PW)
    text = (
        "Channel 100 was not renumbered: it is on channel number 100.5, and this "
        f"request expected 7. login {PW} rejected"
    )
    out = redact_log_diagnostic(text)
    assert out == (
        "Channel 100 was not renumbered: it is on channel number 100.5, and this "
        "request expected 7. login ***REDACTED*** rejected"
    )


def test_short_registered_value_in_url_is_still_caught_structurally():
    register_sensitive_values("pw1")
    out = redact_log_diagnostic("http://prov.example/get.php?username=u&password=pw1")
    assert "pw1" not in out


def test_four_character_registered_values_are_swept():
    register_sensitive_values("Zq9x")
    assert "Zq9x" not in redact_log_diagnostic("auth rejected for Zq9x")
