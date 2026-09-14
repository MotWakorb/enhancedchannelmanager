"""Dispatcharr 429 handling in ``DispatcharrClient`` (GH #1009).

Before this, no code path in the client handled ``429 Too Many Requests``:
every write called ``raise_for_status`` and a single rate-limited response
aborted a planned pipeline replay. The client now retries a 429 with
bounded backoff, honouring ``Retry-After`` when Dispatcharr sends one, and
raises an ``HTTPStatusError`` carrying the 429 once the budget is spent so
callers can tell a rate-limit rejection from every other failure.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import dispatcharr_client
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _api_key_client() -> DispatcharrClient:
    return DispatcharrClient(DispatcharrSettings(
        url="http://dispatcharr:8000", auth_method="api_key", dispatcharr_api_key="k",
    ))


def _jwt_client() -> DispatcharrClient:
    return DispatcharrClient(DispatcharrSettings(
        url="http://dispatcharr:8000", auth_method="password", username="u", password="p",
    ))


def _response(status_code: int, headers: dict | None = None, json_body=None) -> httpx.Response:
    return httpx.Response(
        status_code, headers=headers or {}, json=json_body if json_body is not None else {},
        request=httpx.Request("GET", "http://dispatcharr:8000/api/x/"),
    )


@pytest.mark.asyncio
async def test_request_retries_429_with_exponential_backoff_then_succeeds():
    client = _api_key_client()
    sleeps: list[float] = []
    try:
        client._client.request = AsyncMock(side_effect=[
            _response(429), _response(429), _response(200, json_body={"ok": True}),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)):
            response = await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert response.status_code == 200
    assert client._client.request.await_count == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_request_honours_retry_after_header():
    client = _api_key_client()
    sleeps: list[float] = []
    try:
        client._client.request = AsyncMock(side_effect=[
            _response(429, headers={"Retry-After": "7"}), _response(200),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)):
            await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_request_raises_429_status_error_once_retry_budget_is_spent():
    client = _api_key_client()
    try:
        client._client.request = AsyncMock(return_value=_response(429))
        with patch.object(dispatcharr_client, "_sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError) as error:
                await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert error.value.response.status_code == 429
    assert "rate limited" in str(error.value)
    assert client._client.request.await_count == dispatcharr_client.RATE_LIMIT_MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_login_retries_429_then_stores_tokens():
    client = _jwt_client()
    sleeps: list[float] = []
    try:
        client._client.post = AsyncMock(side_effect=[
            _response(429), _response(200, json_body={"access": "A", "refresh": "R"}),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)):
            await client._login()
    finally:
        await client._client.aclose()
    assert (client.access_token, client.refresh_token) == ("A", "R")
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_login_raises_429_status_error_once_retry_budget_is_spent():
    client = _jwt_client()
    try:
        client._client.post = AsyncMock(return_value=_response(429))
        with patch.object(dispatcharr_client, "_sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError) as error:
                await client._login()
    finally:
        await client._client.aclose()
    assert error.value.response.status_code == 429
    assert client.access_token is None


@pytest.mark.asyncio
async def test_login_budget_failure_leaves_no_token():
    client = _jwt_client()
    try:
        client._client.post = AsyncMock(return_value=_response(429))
        with patch.object(dispatcharr_client, "_sleep", AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError):
                await client._login()
    finally:
        await client._client.aclose()
    assert client.access_token is None
