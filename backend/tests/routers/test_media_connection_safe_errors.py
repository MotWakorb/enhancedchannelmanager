"""m8dvz: real media wrappers and routes, with synthetic upstream failures."""

import logging
import re
import ssl
from urllib.parse import quote
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from emby_client import EmbyClient
from jellyfin_client import JellyfinClient
from plex_client import PlexClient
from models import User
from media_connection_errors import media_connection_error, redact_media_diagnostic
from tests.conftest import patch_ssrf_dns


SECRET = "synthetic-media-key+/=98eah"
DETAIL = (
    "synthetic diagnostic "
    f"https://url-user:url-pass@media.invalid/live/path-user/path-pass/42.ts?api_key={SECRET} "
    f"X-Emby-Token: {SECRET} X-Plex-Token: {SECRET} "
    f'Authorization: MediaBrowser Token="{SECRET}"'
)
CLIENTS = [("emby", EmbyClient), ("plex", PlexClient), ("jellyfin", JellyfinClient)]
CASES = [
    (401, "Authentication failed. Check the media server credentials."),
    (403, "Authentication failed. Check the media server credentials."),
    (503, "Media server returned an upstream error status."),
    ("timeout", "Connection timed out. Check the media server and try again."),
    ("tls", "TLS connection failed. Check the media server certificate."),
    ("unreachable", "Media server is unreachable. Check the address and network."),
    ("malformed", "Media server returned a malformed response."),
    ("unknown", "Connection test failed. Check server logs for details."),
]


@pytest.fixture(autouse=True)
def isolated_media_state(tmp_path):
    import config
    import log_utils
    from auth import settings as auth_settings
    from tests._config_harness import cleanup_test_config, initialize_test_config

    private_dir = initialize_test_config({"ECM_TEST_CONFIG_ROOT": str(tmp_path)})
    previous_factory = logging.getLogRecordFactory()
    with pytest.MonkeyPatch.context() as state:
        state.setenv("CONFIG_DIR", str(private_dir))
        for name, value in {
            "CONFIG_DIR": private_dir,
            "CONFIG_FILE": private_dir / "settings.json",
            "MCP_SECRETS_DIR": private_dir,
            "MCP_KEY_FILE": private_dir / "api-key",
            "MCP_SERVICE_FILE": private_dir / "mcp-service.json",
            "_cached_settings": None,
            "_cached_mcp_authority_signature": None,
            "_cached_mcp_files_signature": None,
            "_mcp_settings_mirror_dirty": False,
            "_legacy_api_key_warned": False,
            "_legacy_api_key_conflict_warned": False,
            "_dedup_threshold_floor_warned": False,
            "_public_base_url_unset_warned": False,
            "_public_base_url_invalid_warned": False,
            "_session_cookie_transport_warned": False,
        }.items():
            state.setattr(config, name, value)
        state.setattr(auth_settings, "CONFIG_DIR", private_dir)
        state.setattr(auth_settings, "AUTH_CONFIG_FILE", private_dir / "auth_settings.json")
        state.setattr(auth_settings, "_cached_auth_settings", None)
        state.setattr(auth_settings, "_cached_auth_settings_signature", None)
        # Retired credentials intentionally survive in production. Borrow a new
        # registry here rather than clearing the preceding tests' original set.
        with log_utils._sensitive_values_lock:
            previous_values = log_utils._sensitive_value_forms
            log_utils._sensitive_value_forms = set()
        try:
            yield
        finally:
            logging.setLogRecordFactory(previous_factory)
            with log_utils._sensitive_values_lock:
                log_utils._sensitive_value_forms = previous_values
            cleanup_test_config(private_dir)


def upstream(case, request):
    if isinstance(case, int):
        return httpx.Response(case, text=DETAIL, request=request)
    if case == "malformed":
        return httpx.Response(200, text=DETAIL, request=request)
    if case == "tls":
        try:
            raise ssl.SSLCertVerificationError(1, DETAIL)
        except ssl.SSLError as cause:
            raise httpx.ConnectError(DETAIL, request=request) from cause
    error = {
        "timeout": httpx.ReadTimeout,
        "unreachable": httpx.ConnectError,
        "unknown": RuntimeError,
    }[case]
    raise error(DETAIL)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,client_type", CLIENTS)
@pytest.mark.parametrize("case,message", CASES)
@pytest.mark.parametrize("admin", [False, True], ids=["first-run", "human-admin"])
async def test_actual_client_failure_is_safe_at_route(
    async_client, caplog, name, client_type, case, message, admin,
):
    caplog.set_level(logging.DEBUG)
    client = client_type("https://media.invalid", SECRET)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: upstream(case, request)),
    )
    with (
        patch(f"routers.settings.{client_type.__name__}", return_value=client),
        patch_ssrf_dns("192.168.1.10"),
        patch("auth.dependencies.get_auth_settings") as auth,
        patch("auth.dependencies.get_current_user", new=AsyncMock(return_value=User(
            id=7151, username="synthetic-admin", is_admin=True,
            is_active=True, auth_provider="local",
        ) if admin else None)),
    ):
        auth.return_value.require_auth = admin
        auth.return_value.setup_complete = admin
        response = await async_client.post(
            f"/api/settings/{name}/test-connection",
            json={
                "base_url": f"https://media.invalid/live/path-user/path-pass/42.ts?api_key={SECRET}",
                "token" if name == "plex" else "api_key": SECRET,
            },
        )
    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": message}
    assert client._client.is_closed
    for value in (SECRET, "url-user", "url-pass", "path-user", "path-pass"):
        assert value not in response.text
        assert value not in caplog.text
    assert "synthetic diagnostic" not in response.text
    if case in ("timeout", "tls", "unreachable", "unknown"):
        assert "synthetic diagnostic" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("name,client_type", CLIENTS)
async def test_actual_boolean_client_failure_logs_are_safe(caplog, name, client_type):
    caplog.set_level(logging.DEBUG)
    client = client_type("https://media.invalid", SECRET)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: upstream("timeout", request)),
    )
    try:
        assert await client.test_connection() is False
    finally:
        await client.close()
    assert SECRET not in caplog.text
    assert "path-pass" not in caplog.text
    assert "synthetic diagnostic" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("name,client_type", CLIENTS)
async def test_actual_client_success_and_close(async_client, name, client_type):
    client = client_type("https://media.invalid", SECRET)
    await client._client.aclose()
    payload = "<MediaContainer/>" if name == "plex" else "[]"
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )
    with patch(f"routers.settings.{client_type.__name__}", return_value=client), patch_ssrf_dns("192.168.1.10"):
        response = await async_client.post(
            f"/api/settings/{name}/test-connection",
            json={"base_url": "https://media.invalid", "token" if name == "plex" else "api_key": SECRET},
        )
    assert response.json() == {"ok": True}
    assert client._client.is_closed


ENCODED = quote(SECRET, safe="")
MEDIA_VALUES = [
    SECRET, ENCODED,
    re.sub(r"%[0-9A-F]{2}", lambda m: m[0].lower(), ENCODED),
    ENCODED.replace("%2B", "%2b"),
    quote(ENCODED, safe=""), quote(quote(ENCODED, safe=""), safe=""),
    ENCODED.replace("%", "%" + "25" * 12),
    "".join(f"%{byte:02x}" for byte in SECRET.encode()),
]
MEDIA_SHAPES = [
    "https://url-user:url-pass@media.invalid/arbitrary/{value}/resource",
    "free text {value} end",
    "X-Emby-Token: {value}", 'x-eMbY-tOkEn: "{value}"',
    "X-Plex-Token: '{value}'",
    'Authorization: MediaBrowser Client="ECM", Token="{value}"',
]


@pytest.mark.parametrize("value", MEDIA_VALUES)
@pytest.mark.parametrize("shape", MEDIA_SHAPES)
def test_media_encoded_diagnostic_boundary(value, shape):
    result = redact_media_diagnostic("useful context\n" + shape.format(value=value), SECRET)
    assert value not in result
    assert SECRET not in result
    assert "useful context" in result
    assert "url-user" not in result
    assert "url-pass" not in result


@pytest.mark.parametrize("secret", ["x", "xy", "xyz", "a+b", "a.*[b]\\c"])
@pytest.mark.parametrize("header", ["X-Emby-Token", "X-Plex-Token", "Authorization"])
@pytest.mark.parametrize("quoted", [False, True])
def test_media_complete_header_value_without_shredding_context(secret, header, quoted):
    value = f'"{secret}"' if quoted else secret
    if header == "Authorization":
        value = f'MediaBrowser Client="ECM", Token={value}'
    result = redact_media_diagnostic(f"context xyz 123\n{header}: {value}\nnext diagnostic", secret)
    assert result.splitlines()[1] == f"{header}: ***REDACTED***"
    assert result.startswith("context xyz 123\n")
    assert result.endswith("\nnext diagnostic")


@pytest.mark.parametrize("detail", [
    "401 unauthorized", "403", "TimeoutError", "SSL CERTIFICATE_VERIFY_FAILED",
    "ConnectionError host unreachable", "JSONDecodeError", "HTTPStatusError 503",
])
def test_media_category_is_not_inferred_from_text(detail):
    assert media_connection_error(RuntimeError(detail), SECRET) == CASES[-1][1]


def test_media_known_value_is_literal_and_case_sensitive():
    secret = r"key.*[x]\z+/=%98"
    value = quote(quote(quote(secret, safe=""), safe=""), safe="")
    result = redact_media_diagnostic(f"keep KEY.*[X] unrelated\nvalue {value}", secret)
    assert result == "keep KEY.*[X] unrelated\nvalue ***REDACTED***"


@pytest.mark.parametrize("secret", [r"key.*[x]\z+/=%98", "key-\u00e9\U0001f512"])
def test_media_pattern_escapes_raw_and_encoded_literals(secret):
    with patch("media_connection_errors.re.escape", wraps=re.escape) as escape:
        result = redact_media_diagnostic(f"value {quote(secret, safe='')} end", secret)
    assert result == "value ***REDACTED*** end"
    literals = [call.args[0] for call in escape.call_args_list]
    for char in secret:
        assert char in literals
        for byte in char.encode("utf-8"):
            assert f"{byte:02x}" in literals


@pytest.mark.asyncio
@pytest.mark.parametrize("name,client_type", CLIENTS)
@pytest.mark.parametrize("routed", [False, True], ids=["boolean-client", "get-sessions-route"])
@pytest.mark.parametrize("secret,value", [(SECRET, value) for value in MEDIA_VALUES] + [
    (short, short) for short in ("x", "xy", "xyz")
])
async def test_media_nested_failure_through_log_pipeline(
    async_client, caplog, tmp_path, name, client_type, routed, secret, value,
):
    from log_utils import InterProcessRotatingJsonHandler, _safe_record_factory
    from observability import JsonFormatter

    caplog.set_level(logging.INFO)
    client = client_type("https://media.invalid", secret)
    await client._client.aclose()

    def transport(request):
        detail = "useful transport context\n" + "\n".join(
            shape.format(value=value) for shape in (MEDIA_SHAPES if len(secret) >= 4 else MEDIA_SHAPES[2:])
        )
        try:
            raise ValueError(detail)
        except ValueError as cause:
            raise httpx.ConnectError(detail, request=request) from cause

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    previous_factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(_safe_record_factory)
    handler = InterProcessRotatingJsonHandler(tmp_path / "ecm.log", max_bytes=1000000, backup_count=1)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    try:
        if routed:
            with patch(f"routers.settings.{client_type.__name__}", return_value=client), patch_ssrf_dns("192.168.1.10"):
                response = await async_client.post(
                    f"/api/settings/{name}/test-connection",
                    json={"base_url": "https://media.invalid", "token" if name == "plex" else "api_key": secret},
                )
            assert response.json() == {"ok": False, "error": CASES[5][1]}
            assert client._client.is_closed
        else:
            assert await client.test_connection() is False
    finally:
        try:
            await client.close()
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
            logging.setLogRecordFactory(previous_factory)
    records = [r for r in caplog.records if r.name in {"media_connection_errors", f"{name}_client"}]
    assert records
    assert all(r.exc_info is None for r in records)
    persisted = (tmp_path / "ecm.log").read_text()
    for output in (caplog.text, persisted):
        if len(secret) >= 4:
            assert value not in output
            assert secret not in output
        assert f"X-Emby-Token: {value}" not in output
        assert "X-Emby-Token: ***REDACTED***" in output
        assert "X-Plex-Token: ***REDACTED***" in output
        assert "url-user" not in output
        assert "url-pass" not in output
        assert "useful transport context" in output
    if routed:
        assert "ValueError" in persisted
        assert "get_sessions" in persisted
