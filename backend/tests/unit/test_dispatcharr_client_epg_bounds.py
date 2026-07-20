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
    client = _client(
        lambda request: httpx.Response(
            200, json=[{"id": 1, "tvg_id": "101"}, {"id": 2, "tvg_id": "102"}]
        )
    )
    try:
        assert await client.get_epg_data(max_results=2) == [
            {"id": 1, "tvg_id": "101"},
            {"id": 2, "tvg_id": "102"},
        ]
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
