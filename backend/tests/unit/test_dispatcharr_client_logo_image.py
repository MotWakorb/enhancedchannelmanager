"""Unit tests for ``DispatcharrClient.fetch_logo_image``
(bead enhancedchannelmanager-xb58a).

Dispatcharr is ECM's source of truth for logo images, including the ones ECM's
own Logo Manager uploads (those land in Dispatcharr's ``/data/logos/``, never in
ECM's config volume). The DBAS backup builder archives those bytes by asking
Dispatcharr for them at gather time, and this is the read it makes.

Mocking pattern follows the sibling direct-client unit tests: construct a real
``DispatcharrClient`` and patch ``_request``, so the endpoint, the return
parsing, and the error handling are exercised without a live HTTP round-trip.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _response(status_code: int, content: bytes = b""):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = content
    return resp


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        dispatcharr_api_key="k",
    )
    return DispatcharrClient(settings)


@pytest.mark.asyncio
async def test_fetches_the_bytes_from_the_logo_cache_endpoint():
    client = _make_client()
    with patch.object(
        client, "_request", AsyncMock(return_value=_response(200, PNG))
    ) as request:
        data = await client.fetch_logo_image(13)

    assert data == PNG
    request.assert_awaited_once_with("GET", "/api/channels/logos/13/cache/")


@pytest.mark.asyncio
async def test_upstream_error_status_returns_none_rather_than_raising():
    """A deleted logo or an unfillable cache is data, not a crash.

    The backup builder treats ``None`` as "these bytes are not archivable",
    counts a miss, and completes the backup.
    """
    client = _make_client()
    with patch.object(client, "_request", AsyncMock(return_value=_response(404))):
        assert await client.fetch_logo_image(13) is None


@pytest.mark.asyncio
async def test_upstream_error_log_carries_no_url_or_body(caplog):
    client = _make_client()
    with caplog.at_level("WARNING"), \
         patch.object(client, "_request", AsyncMock(return_value=_response(500))):
        await client.fetch_logo_image(13)

    assert "id=13" in caplog.text
    assert "http://" not in caplog.text
    assert "/api/channels/logos/" not in caplog.text


@pytest.mark.asyncio
async def test_transport_failure_propagates_to_the_caller():
    """A transport error is NOT swallowed here: the caller decides whether it is
    fatal. The backup builder wraps this call and degrades to a counted miss."""
    client = _make_client()
    with patch.object(
        client, "_request", AsyncMock(side_effect=httpx.ConnectError("down"))
    ):
        with pytest.raises(httpx.ConnectError):
            await client.fetch_logo_image(13)
