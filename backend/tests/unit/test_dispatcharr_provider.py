"""
Unit tests for DispatcharrClient authentication status-code handling.

Covers Dispatcharr 0.23.0 behavior:
  - HTTP 429 -> DispatcharrRateLimitError (login throttle)
  - HTTP 403 -> DispatcharrNetworkPolicyError (UI network allow-list)
  - /me endpoint moved to /api/accounts/users/me/ with /api/accounts/me/ fallback.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from auth.providers.dispatcharr import (
    DispatcharrAuthenticationError,
    DispatcharrClient,
    DispatcharrNetworkPolicyError,
    DispatcharrRateLimitError,
)


def _mock_response(status_code: int, json_body=None):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body or {}
    return resp


@pytest.mark.asyncio
async def test_authenticate_429_raises_rate_limit_error():
    client = DispatcharrClient(base_url="http://dispatcharr.local")
    try:
        with patch.object(client._client, "post", AsyncMock(return_value=_mock_response(429))):
            with pytest.raises(DispatcharrRateLimitError) as exc:
                await client.authenticate("user", "pass")
            assert "rate-limit" in str(exc.value).lower() or "rate limit" in str(exc.value).lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticate_403_raises_network_policy_error():
    client = DispatcharrClient(base_url="http://dispatcharr.local")
    try:
        with patch.object(client._client, "post", AsyncMock(return_value=_mock_response(403))):
            with pytest.raises(DispatcharrNetworkPolicyError) as exc:
                await client.authenticate("user", "pass")
            assert "network policy" in str(exc.value).lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticate_401_still_raises_auth_error():
    client = DispatcharrClient(base_url="http://dispatcharr.local")
    try:
        with patch.object(client._client, "post", AsyncMock(return_value=_mock_response(401))):
            with pytest.raises(DispatcharrAuthenticationError):
                await client.authenticate("user", "pass")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticate_success_tries_users_me_first():
    """On success the client hits /api/accounts/users/me/ (Dispatcharr 0.23.0+)."""
    client = DispatcharrClient(base_url="http://dispatcharr.local")
    try:
        token_resp = _mock_response(200, {"access": "tok-abc"})
        me_resp = _mock_response(
            200,
            {"id": 42, "username": "alice", "email": "a@b.c", "display_name": "Alice"},
        )

        post_mock = AsyncMock(return_value=token_resp)
        get_mock = AsyncMock(return_value=me_resp)

        with patch.object(client._client, "post", post_mock), \
             patch.object(client._client, "get", get_mock):
            result = await client.authenticate("alice", "pw")

        assert result.username == "alice"
        assert result.email == "a@b.c"
        # First GET should hit the new users/me URL.
        first_call_url = get_mock.await_args_list[0].args[0]
        assert first_call_url.endswith("/api/accounts/users/me/")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticate_users_me_404_falls_back_to_legacy_me():
    """If /users/me/ 404s, the client tries the legacy /api/accounts/me/ path."""
    client = DispatcharrClient(base_url="http://dispatcharr.local")
    try:
        token_resp = _mock_response(200, {"access": "tok-abc"})
        users_me_resp = _mock_response(404)
        legacy_me_resp = _mock_response(
            200,
            {"id": 7, "username": "bob", "email": "b@c.d"},
        )

        post_mock = AsyncMock(return_value=token_resp)
        get_mock = AsyncMock(side_effect=[users_me_resp, legacy_me_resp])

        with patch.object(client._client, "post", post_mock), \
             patch.object(client._client, "get", get_mock):
            result = await client.authenticate("bob", "pw")

        assert result.email == "b@c.d"
        assert get_mock.await_count == 2
        assert get_mock.await_args_list[0].args[0].endswith("/api/accounts/users/me/")
        assert get_mock.await_args_list[1].args[0].endswith("/api/accounts/me/")
    finally:
        await client.close()
