"""
Unit tests for DispatcharrClient auth-method branching.

Covers the outbound service-to-service auth introduced for option A:
  - auth_method="password" (legacy JWT flow)
  - auth_method="api_key"  (X-API-Key header, no token lifecycle)
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient, _settings_hash


def _response(status_code: int, json_body=None):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body or {}
    return resp


@pytest.mark.asyncio
async def test_api_key_mode_sends_x_api_key_header_and_skips_login():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        api_key="key-abc",
    )
    client = DispatcharrClient(settings)
    try:
        fake_resp = _response(200, {"ok": True})

        request_mock = AsyncMock(return_value=fake_resp)
        login_mock = AsyncMock()

        with patch.object(client._client, "request", request_mock), \
             patch.object(client, "_login", login_mock):
            result = await client._request("GET", "/api/accounts/users/me/")

        assert result is fake_resp
        # _login must NOT be called in api_key mode.
        login_mock.assert_not_awaited()
        # The outbound call must carry X-API-Key, not Authorization.
        sent_headers = request_mock.await_args.kwargs["headers"]
        assert sent_headers["X-API-Key"] == "key-abc"
        assert "Authorization" not in sent_headers
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_api_key_mode_does_not_retry_on_401():
    """In api_key mode a 401 is terminal — no token refresh attempts."""
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        api_key="bad-key",
    )
    client = DispatcharrClient(settings)
    try:
        fake_resp = _response(401)

        request_mock = AsyncMock(return_value=fake_resp)
        refresh_mock = AsyncMock()

        with patch.object(client._client, "request", request_mock), \
             patch.object(client, "_refresh_access_token", refresh_mock):
            result = await client._request("GET", "/api/channels/")

        assert result.status_code == 401
        refresh_mock.assert_not_awaited()
        assert request_mock.await_count == 1
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_password_mode_still_sends_bearer_token():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password="secret",
    )
    client = DispatcharrClient(settings)
    try:
        fake_resp = _response(200, {"ok": True})

        request_mock = AsyncMock(return_value=fake_resp)

        async def fake_login():
            client.access_token = "token-xyz"

        with patch.object(client._client, "request", request_mock), \
             patch.object(client, "_login", side_effect=fake_login):
            await client._request("GET", "/api/channels/")

        sent_headers = request_mock.await_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer token-xyz"
        assert "X-API-Key" not in sent_headers
    finally:
        await client._client.aclose()


def test_settings_hash_differs_across_auth_methods():
    """Flipping auth_method must force the singleton client to reset."""
    password_settings = DispatcharrSettings(
        url="http://d", auth_method="password", username="u", password="p"
    )
    api_key_settings = DispatcharrSettings(
        url="http://d", auth_method="api_key", api_key="k"
    )
    assert _settings_hash(password_settings) != _settings_hash(api_key_settings)


def test_settings_is_configured_api_key_mode():
    assert DispatcharrSettings(url="http://d", auth_method="api_key", api_key="k").is_configured()
    assert not DispatcharrSettings(url="http://d", auth_method="api_key", api_key="").is_configured()
    # Missing url is never configured regardless of mode.
    assert not DispatcharrSettings(url="", auth_method="api_key", api_key="k").is_configured()


def test_settings_is_configured_password_mode_unchanged():
    assert DispatcharrSettings(url="http://d", username="u", password="p").is_configured()
    assert not DispatcharrSettings(url="http://d", username="u", password="").is_configured()
    assert not DispatcharrSettings(url="http://d", username="", password="p").is_configured()


# =====================================================================
# bd-jmi1c P1-2 / bd-46g4t — explicit guards for the legacy api_key
# fallback in is_configured() and the X-API-Key header path. Production
# code goes through load_settings() (which migrates legacy → canonical)
# so these fallbacks shouldn't fire on a real install — but the legacy
# field exists on the model until bd-ewm4h removes it in v0.19.0, and
# without these tests a future refactor could silently drop the fallback
# (breaking direct-construction callers like ad-hoc scripts).
# =====================================================================


def test_is_configured_falls_back_to_legacy_api_key_when_canonical_empty():
    """``DispatcharrSettings(api_key="...")`` with no canonical value still
    reports ``is_configured()=True`` so direct-construction callers don't
    silently report disconnected during the back-compat window."""
    settings = DispatcharrSettings(
        url="http://d", auth_method="api_key", dispatcharr_api_key="", api_key="legacy-only"
    )
    assert settings.is_configured() is True


@pytest.mark.asyncio
async def test_x_api_key_header_uses_legacy_field_when_canonical_empty():
    """The X-API-Key header path falls back to the legacy ``api_key`` when
    the canonical field is empty — mirrors ``is_configured()``'s contract
    and keeps direct-construction callers functional."""
    settings = DispatcharrSettings(
        url="http://d", auth_method="api_key", dispatcharr_api_key="", api_key="legacy-key-only"
    )
    client = DispatcharrClient(settings)
    try:
        fake_resp = _response(200, {"ok": True})
        request_mock = AsyncMock(return_value=fake_resp)
        with patch.object(client._client, "request", request_mock):
            await client._request("GET", "/api/channels/")
        sent_headers = request_mock.await_args.kwargs["headers"]
        assert sent_headers["X-API-Key"] == "legacy-key-only"
    finally:
        await client._client.aclose()


# =====================================================================
# bd-jmi1c P2-3 / bd-46g4t — ``_settings_hash`` returns sha256 hex
# rather than the raw plaintext concat. The function's job is opaque
# equality (cache key for the client singleton); hashing keeps that
# semantics while removing the risk that a stray ``logger.info(hash)``
# leaks password + api_key + canonical credential in one string.
# =====================================================================


def test_settings_hash_is_sha256_hex_string():
    """``_settings_hash`` returns a 64-character lowercase-hex string —
    the canonical sha256 hexdigest length sanity check."""
    settings = DispatcharrSettings(
        url="http://d", auth_method="api_key", api_key="k"
    )
    h = _settings_hash(settings)
    assert isinstance(h, str)
    assert len(h) == 64, f"sha256 hexdigest should be 64 chars; got {len(h)}: {h}"
    assert all(c in "0123456789abcdef" for c in h), (
        f"sha256 hexdigest should be lowercase hex; got {h}"
    )


def test_settings_hash_does_not_leak_plaintext_credentials():
    """The returned hash must not contain any raw credential value as a
    substring — the whole point of P2-3 is that a leaked hash doesn't
    expose the inputs."""
    sentinel_password = "PASSWORD-SHOULD-NOT-LEAK-1234567890"
    sentinel_canonical = "CANONICAL-KEY-SHOULD-NOT-LEAK-XYZ"
    sentinel_legacy = "LEGACY-KEY-SHOULD-NOT-LEAK-ABC"
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password=sentinel_password,
        dispatcharr_api_key=sentinel_canonical,
        api_key=sentinel_legacy,
    )
    h = _settings_hash(settings)
    assert sentinel_password not in h
    assert sentinel_canonical not in h
    assert sentinel_legacy not in h


def test_settings_hash_is_deterministic_for_identical_settings():
    """Two ``DispatcharrSettings`` with identical fields produce identical
    hashes — the equality contract ``get_client()`` relies on."""
    a = DispatcharrSettings(
        url="http://d", auth_method="api_key",
        username="u", password="p",
        dispatcharr_api_key="canon", api_key="leg",
    )
    b = DispatcharrSettings(
        url="http://d", auth_method="api_key",
        username="u", password="p",
        dispatcharr_api_key="canon", api_key="leg",
    )
    assert _settings_hash(a) == _settings_hash(b)


def test_settings_hash_changes_when_any_field_changes():
    """Flipping any one field flips the hash — confirms every input
    participates in the cache-key calculation. Without this guard, a
    refactor that drops one field from the concat would silently break
    the client-reset behavior in ``get_client()``."""
    base_kwargs = dict(
        url="http://d", auth_method="api_key",
        username="u", password="p",
        dispatcharr_api_key="canon", api_key="leg",
    )
    base = DispatcharrSettings(**base_kwargs)
    base_hash = _settings_hash(base)

    for field in ("url", "auth_method", "username", "password",
                  "dispatcharr_api_key", "api_key"):
        mutated_kwargs = dict(base_kwargs)
        # auth_method has a restricted vocabulary — flip to the other one;
        # all other fields take any string.
        if field == "auth_method":
            mutated_kwargs[field] = "password"
        else:
            mutated_kwargs[field] = base_kwargs[field] + "-mutated"
        mutated = DispatcharrSettings(**mutated_kwargs)
        assert _settings_hash(mutated) != base_hash, (
            f"hash did not change when {field} mutated"
        )
