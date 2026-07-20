"""Bounded EPG-data response handling for migration callers."""

from unittest.mock import patch

import httpx
import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _client(handler) -> DispatcharrClient:
    client = DispatcharrClient(
        DispatcharrSettings(url="http://dispatcharr", auth_method="api_key", api_key="k")
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes):
        self.content = content
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        yield self.content


@pytest.mark.asyncio
async def test_bounded_flat_response_rejected_before_json_decode():
    payload = b"[" + (b" " * (1024 * 1024)) + b"]"
    client = _client(lambda request: httpx.Response(200, content=payload))
    try:
        with patch(
            "dispatcharr_client.json.loads",
            side_effect=AssertionError("oversized body must not be decoded"),
        ):
            with pytest.raises(ValueError, match="EPG response exceeds"):
                await client.get_epg_data(max_results=1)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_bounded_flat_response_returns_normal_rows():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json=[{"id": 1, "tvg_id": "101"}, {"id": 2, "tvg_id": "102"}]
        )

    client = _client(handler)
    try:
        assert await client.get_epg_data(max_results=2) == [
            {"id": 1, "tvg_id": "101"},
            {"id": 2, "tvg_id": "102"},
        ]
        assert requests[0].headers["Accept-Encoding"] == "identity"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate"])
async def test_encoded_response_rejected_before_stream_or_json_decode(encoding):
    stream = _TrackingStream(b'[{"id": 1}]')
    client = _client(
        lambda request: httpx.Response(
            200,
            headers={"Content-Encoding": encoding},
            stream=stream,
        )
    )
    try:
        with patch(
            "dispatcharr_client.json.loads",
            side_effect=AssertionError("encoded body must not be decoded"),
        ):
            with pytest.raises(ValueError, match="unexpected Content-Encoding"):
                await client.get_epg_data(max_results=1)
        assert stream.iterated is False
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Content-Encoding": "identity"}])
async def test_absent_or_identity_encoding_is_accepted(headers):
    client = _client(
        lambda request: httpx.Response(200, headers=headers, json=[{"id": 1}])
    )
    try:
        assert await client.get_epg_data(max_results=1) == [{"id": 1}]
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_bounded_paginated_response_stops_at_exact_limit():
    def handler(request: httpx.Request):
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={"results": [{"id": 1}], "next": "page-2"},
            )
        return httpx.Response(
            200,
            json={"results": [{"id": 2}, {"id": 3}], "next": None},
        )

    client = _client(handler)
    try:
        assert await client.get_epg_data(max_results=2) == [{"id": 1}, {"id": 2}]
    finally:
        await client._client.aclose()
