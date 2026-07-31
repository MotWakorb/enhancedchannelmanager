"""Bounded EPG-data response handling for migration callers."""

import asyncio
import threading
import time
from unittest.mock import patch

import httpx
import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

# Safety bound for the off-loop handshake below. It is a deadlock guard, not an
# assertion threshold: the healthy path clears it in microseconds, and a value
# this generous (10x the worst event-loop stall ever observed on a saturated CI
# runner) cannot be reached by machine load alone.
_LOOP_LIVENESS_TIMEOUT = 10.0


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
async def test_large_json_decode_does_not_block_event_loop():
    """A slow EPG decode must run off the event loop thread.

    Asserted as a *mechanism*, not as a latency budget. The previous shape
    measured the wall-clock scheduling latency of a 10ms sleep against a 50ms
    budget, and that measurement cannot separate the two cases it needs to tell
    apart. Measured on this suite (bead enhancedchannelmanager-kcyiq): a
    genuinely broken inline decode costs ~0.10s, while the same *healthy* code
    on a saturated GitHub runner produced 0.79s and 0.97s. Every threshold that
    catches the regression is one the loaded runner blows through, so the old
    assertion reported an identical failure for a real regression and for a busy
    machine. Both assertions below are event-driven rather than clock-driven and
    are therefore immune to machine load.
    """
    client = _client(lambda request: httpx.Response(200, content=b'[{"id": 1}]'))
    real_loads = __import__("json").loads

    loop_thread_ident = threading.get_ident()
    decode_entered = threading.Event()
    loop_advanced = threading.Event()
    observed: dict = {}

    def slow_loads(payload):
        observed["thread_ident"] = threading.get_ident()
        observed["thread_name"] = threading.current_thread().name
        decode_entered.set()
        # Hold the decode open until the event loop proves it is still running.
        # This replaces a fixed sleep: the decode is guaranteed to still be in
        # flight when liveness is observed, instead of racing a timer.
        observed["loop_advanced_during_decode"] = loop_advanced.wait(
            timeout=_LOOP_LIVENESS_TIMEOUT
        )
        return real_loads(payload)

    try:
        with patch("dispatcharr_client.json.loads", side_effect=slow_loads):
            decode_task = asyncio.create_task(client.get_epg_data(max_results=1))
            # Reaching this poll at all means the loop was not blocked by the
            # decode. The deadline is a hang guard, not an assertion.
            poll_deadline = time.monotonic() + _LOOP_LIVENESS_TIMEOUT
            while not decode_entered.is_set() and time.monotonic() < poll_deadline:
                await asyncio.sleep(0.001)
            loop_advanced.set()
            result = await decode_task

        assert decode_entered.is_set(), "the patched json.loads was never called"
        assert observed["thread_ident"] != loop_thread_ident, (
            "json.loads ran on the event loop thread "
            f"({observed['thread_name']}) — the decode is blocking the loop"
        )
        assert observed["loop_advanced_during_decode"] is True, (
            "the event loop did not advance while the decode was in flight — "
            f"decode ran on {observed['thread_name']}"
        )
        assert result == [{"id": 1}]
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
