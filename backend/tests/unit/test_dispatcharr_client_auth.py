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
