"""
Unit tests for DispatcharrClient stream create/delete helpers
(enhancedchannelmanager-nav0c, Phase 2 Channels import 1/4).

The Channels importer (0i2vt.14 + splits 4vouz/ahygg) restores channels whose
streams are embedded (not standalone). To recreate those streams it needs a
``create_stream`` client method, and the restore rollback ledger
(restore_contracts kxuj2) needs a ``delete_stream`` compensation to undo a
created stream if a later step in the same restore fails.

Mocking pattern follows the existing direct-client unit tests
(test_dispatcharr_client_group_filter.py): construct a real ``DispatcharrClient``
and ``patch.object(client, "_request", AsyncMock(...))`` so the helper's
endpoint, payload, return parsing, and error handling are exercised without a
live HTTP round-trip.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _response(status_code: int, json_body=None, text: str = ""):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else {}
    resp.text = text

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=None, response=resp
            )

    resp.raise_for_status = _raise_for_status
    return resp


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password="secret",
    )
    return DispatcharrClient(settings)


# ---------------------------------------------------------------------------
# create_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_stream_posts_to_streams_endpoint_and_returns_created():
    """create_stream POSTs the payload to /api/channels/streams/ and returns
    the parsed created-stream body."""
    client = _make_client()
    try:
        created = {"id": 555, "name": "ESPN HD", "url": "http://p/espn.ts"}
        request_mock = AsyncMock(return_value=_response(201, created))
        data = {
            "name": "ESPN HD",
            "url": "http://p/espn.ts",
            "m3u_account": 7,
        }

        with patch.object(client, "_request", request_mock):
            result = await client.create_stream(data)

        assert result == created
        method, path = request_mock.await_args.args
        assert method == "POST"
        assert path == "/api/channels/streams/"
        assert request_mock.await_args.kwargs["json"] == data
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_stream_accepts_200_status():
    """Some DRF endpoints return 200 rather than 201 on create; both are
    treated as success (the helper keys off status >= 400 for failure, matching
    create_channel/create_channel_group)."""
    client = _make_client()
    try:
        created = {"id": 1, "name": "S"}
        request_mock = AsyncMock(return_value=_response(200, created))
        with patch.object(client, "_request", request_mock):
            result = await client.create_stream({"name": "S"})
        assert result == created
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_stream_raises_with_status_and_body_on_4xx():
    """A 4xx upstream response raises an Exception whose message matches the
    '<thing> failed: <status> - <body>' shape that upstream_http_exception
    (restore_contracts mapper) parses to surface the actionable detail."""
    client = _make_client()
    try:
        body = '{"url": ["This field is required."]}'
        request_mock = AsyncMock(return_value=_response(400, text=body))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(Exception) as exc_info:
                await client.create_stream({"name": "no-url"})

        msg = str(exc_info.value)
        assert "Stream creation failed" in msg
        assert "400" in msg
        assert body in msg
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_stream_raises_on_5xx():
    """A 5xx upstream response also raises (created stream not returned)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(503, text="upstream down"))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(Exception) as exc_info:
                await client.create_stream({"name": "x"})
        assert "503" in str(exc_info.value)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_stream_rejects_non_dict_payload():
    """Input validation: create_stream requires a dict payload; a non-dict
    (e.g. None, list) raises before any HTTP request is issued."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(201, {"id": 1}))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(ValueError):
                await client.create_stream(None)  # type: ignore[arg-type]
            with pytest.raises(ValueError):
                await client.create_stream([{"name": "x"}])  # type: ignore[arg-type]
        request_mock.assert_not_awaited()
    finally:
        await client._client.aclose()


# ---------------------------------------------------------------------------
# delete_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_stream_issues_delete_to_id_endpoint():
    """delete_stream DELETEs /api/channels/streams/{id}/ and returns None."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(204))
        with patch.object(client, "_request", request_mock):
            result = await client.delete_stream(555)

        assert result is None
        method, path = request_mock.await_args.args
        assert method == "DELETE"
        assert path == "/api/channels/streams/555/"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_delete_stream_raises_on_error_status():
    """delete_stream surfaces an upstream error via raise_for_status (matches
    delete_channel/delete_channel_group convention)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(500, text="boom"))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(httpx.HTTPStatusError):
                await client.delete_stream(999)
    finally:
        await client._client.aclose()
