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


# ---------------------------------------------------------------------------
# PR #1010 review items 4 and 5: finite, validated Retry-After handling under
# an explicit total wait budget, in both the delay-seconds and HTTP-date forms.
# ---------------------------------------------------------------------------

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

from dispatcharr_client import RATE_LIMIT_MAX_TOTAL_WAIT, _parse_retry_after, _rate_limit_delay

_FIXED_NOW = 1_800_000_000.0


def _http_date(offset_seconds: float) -> str:
    when = datetime.fromtimestamp(_FIXED_NOW + offset_seconds, tz=timezone.utc)
    return format_datetime(when, usegmt=True)


class TestParseRetryAfter:
    def test_delay_seconds(self):
        assert _parse_retry_after("7") == 7.0
        assert _parse_retry_after(" 0 ") == 0.0
        assert _parse_retry_after("2.5") == 2.5

    @pytest.mark.parametrize("value", ["inf", "1e309", "nan", "-5", "abc", "", None])
    def test_non_finite_negative_and_garbage_are_unusable(self, value):
        assert _parse_retry_after(value) is None

    def test_http_date_in_the_future_is_the_remaining_interval(self):
        with patch.object(dispatcharr_client, "_now", lambda: _FIXED_NOW):
            remaining = _parse_retry_after(_http_date(20))
        assert 19.0 <= remaining <= 20.0  # HTTP dates have 1s resolution

    def test_http_date_in_the_past_means_retry_now(self):
        with patch.object(dispatcharr_client, "_now", lambda: _FIXED_NOW):
            assert _parse_retry_after(_http_date(-90)) == 0.0

    def test_delay_over_budget_is_reported_as_no_admissible_wait(self):
        response = _response(429, headers={"Retry-After": "86400"})
        assert _rate_limit_delay(response, 0, 0.0) is None

    def test_cumulative_waits_are_bounded_by_the_budget(self):
        response = _response(429, headers={"Retry-After": "20"})
        assert _rate_limit_delay(response, 0, 0.0) == 20.0
        assert _rate_limit_delay(response, 1, 20.0) is None  # 40 > 30

    def test_backoff_fallback_is_also_subject_to_the_budget(self):
        response = _response(429)
        assert _rate_limit_delay(response, 0, RATE_LIMIT_MAX_TOTAL_WAIT - 0.5) is None


@pytest.mark.asyncio
async def test_request_gives_up_immediately_on_a_retry_after_beyond_the_budget():
    client = _api_key_client()
    sleeper = AsyncMock()
    try:
        client._client.request = AsyncMock(return_value=_response(429, headers={"Retry-After": "86400"}))
        with patch.object(dispatcharr_client, "_sleep", sleeper):
            with pytest.raises(httpx.HTTPStatusError) as error:
                await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    sleeper.assert_not_awaited()
    assert client._client.request.await_count == 1
    assert error.value.response.status_code == 429
    assert "wait budget" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["inf", "nan", "-5", "garbage"])
async def test_request_falls_back_to_backoff_for_invalid_retry_after(header):
    client = _api_key_client()
    sleeps: list[float] = []
    try:
        client._client.request = AsyncMock(side_effect=[
            _response(429, headers={"Retry-After": header}), _response(200),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)):
            await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_request_honours_http_date_retry_after():
    client = _api_key_client()
    sleeps: list[float] = []
    try:
        client._client.request = AsyncMock(side_effect=[
            _response(429, headers={"Retry-After": _http_date(20)}), _response(200),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)), \
             patch.object(dispatcharr_client, "_now", lambda: _FIXED_NOW):
            await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert len(sleeps) == 1 and 19.0 <= sleeps[0] <= 20.0


@pytest.mark.asyncio
async def test_request_http_date_beyond_budget_is_terminal_not_an_early_retry():
    client = _api_key_client()
    sleeper = AsyncMock()
    try:
        client._client.request = AsyncMock(return_value=_response(429, headers={"Retry-After": _http_date(600)}))
        with patch.object(dispatcharr_client, "_sleep", sleeper), \
             patch.object(dispatcharr_client, "_now", lambda: _FIXED_NOW):
            with pytest.raises(httpx.HTTPStatusError) as error:
                await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    sleeper.assert_not_awaited()
    assert client._client.request.await_count == 1
    assert error.value.response.status_code == 429


@pytest.mark.asyncio
async def test_request_cumulative_retry_after_waits_stop_at_the_budget():
    client = _api_key_client()
    sleeps: list[float] = []
    try:
        client._client.request = AsyncMock(return_value=_response(429, headers={"Retry-After": "20"}))
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)):
            with pytest.raises(httpx.HTTPStatusError):
                await client._request("GET", "/api/x/")
    finally:
        await client._client.aclose()
    assert sleeps == [20.0]  # a second 20s wait would exceed the 30s budget
    assert client._client.request.await_count == 2


@pytest.mark.asyncio
async def test_login_over_budget_retry_after_releases_the_auth_lock():
    """The login path waits while holding ``_auth_lock``; an unbounded wait
    would stall every request behind it. Over budget must raise promptly and
    leave the lock free."""
    client = _jwt_client()
    sleeper = AsyncMock()
    try:
        client._client.post = AsyncMock(return_value=_response(429, headers={"Retry-After": "inf"}))
        # "inf" is invalid -> backoff; make the budget already exhausted so
        # the first backoff is refused, then check the same for a huge number.
        with patch.object(dispatcharr_client, "_sleep", sleeper), \
             patch.object(dispatcharr_client, "RATE_LIMIT_MAX_TOTAL_WAIT", 0.5):
            with pytest.raises(httpx.HTTPStatusError) as error:
                await client._ensure_authenticated()
        assert not client._auth_lock.locked()
        assert client.access_token is None
        assert error.value.response.status_code == 429

        client._client.post = AsyncMock(return_value=_response(429, headers={"Retry-After": "86400"}))
        with patch.object(dispatcharr_client, "_sleep", sleeper):
            with pytest.raises(httpx.HTTPStatusError):
                await client._ensure_authenticated()
        assert not client._auth_lock.locked()
    finally:
        await client._client.aclose()
    sleeper.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_honours_http_date_retry_after_within_budget():
    client = _jwt_client()
    sleeps: list[float] = []
    try:
        client._client.post = AsyncMock(side_effect=[
            _response(429, headers={"Retry-After": _http_date(5)}),
            _response(200, json_body={"access": "A", "refresh": "R"}),
        ])
        with patch.object(dispatcharr_client, "_sleep", AsyncMock(side_effect=sleeps.append)), \
             patch.object(dispatcharr_client, "_now", lambda: _FIXED_NOW):
            await client._login()
    finally:
        await client._client.aclose()
    assert len(sleeps) == 1 and 4.0 <= sleeps[0] <= 5.0
    assert client.access_token == "A"
