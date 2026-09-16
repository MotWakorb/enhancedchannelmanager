"""``DispatcharrClient.create_logo`` is idempotent on the logo URL (GitHub #1013).

Dispatcharr's ``POST /api/channels/logos/`` answers 400 when a logo row with
the same ``url`` already exists, and logo rows outlive the channels that used
them. A planned channel-pipeline commit replays the recorded ``create_logo``
write verbatim, so one stale row from an earlier event cycle turned into a
fail-fast ``PartialReplayError`` at write 0 and a 502 with zero writes landed.

Mocking pattern follows test_dispatcharr_client_stream_crud.py: construct a
real ``DispatcharrClient`` and ``patch.object(client, "_request", ...)``.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

URL = "http://logos.example/snooker.png"
EXISTING = {"id": 765, "name": "old", "url": URL, "channel_count": 0}


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


def _page(results, next_url=None):
    return {"count": len(results), "next": next_url, "previous": None, "results": results}


@pytest.mark.asyncio
async def test_create_logo_returns_existing_row_without_posting_when_url_is_known():
    client = _make_client()

    async def fake_request(method, path, **kwargs):
        assert method == "GET", f"unexpected {method} {path}"
        return _response(200, _page([{"id": 1, "url": "http://other"}, EXISTING]))

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)) as req:
        result = await client.create_logo({"name": "Snooker", "url": URL})

    assert result == EXISTING
    assert all(call.args[0] == "GET" for call in req.await_args_list)


@pytest.mark.asyncio
async def test_create_logo_posts_when_url_is_unknown():
    client = _make_client()
    created = {"id": 2257, "name": "Snooker", "url": URL}
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path))
        if method == "GET":
            return _response(200, _page([{"id": 1, "url": "http://other"}]))
        assert path == "/api/channels/logos/"
        assert kwargs["json"] == {"name": "Snooker", "url": URL}
        return _response(201, created)

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        result = await client.create_logo({"name": "Snooker", "url": URL})

    assert result == created
    assert ("POST", "/api/channels/logos/") in calls


@pytest.mark.asyncio
async def test_create_logo_resolves_by_url_when_post_returns_400_for_a_row_created_meanwhile():
    """Race between the pre-check and the POST: the row appears in between."""
    client = _make_client()
    pages = iter([
        _response(200, _page([])),            # pre-check: not there yet
        _response(200, _page([EXISTING])),    # after the 400: there now
    ])

    async def fake_request(method, path, **kwargs):
        if method == "GET":
            return next(pages)
        return _response(400, {"url": ["logo with this url already exists."]},
                         text='{"url":["logo with this url already exists."]}')

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        result = await client.create_logo({"name": "Snooker", "url": URL})

    assert result == EXISTING


@pytest.mark.asyncio
async def test_create_logo_raises_with_body_when_400_has_no_matching_row():
    client = _make_client()

    async def fake_request(method, path, **kwargs):
        if method == "GET":
            return _response(200, _page([]))
        return _response(400, {"name": ["This field is required."]},
                         text='{"name":["This field is required."]}')

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        with pytest.raises(Exception, match="400.*This field is required"):
            await client.create_logo({"name": "", "url": URL})


@pytest.mark.asyncio
async def test_create_logo_raises_on_non_400_error_without_resolving():
    client = _make_client()
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(method)
        if method == "GET":
            return _response(200, _page([]))
        return _response(500, text="boom")

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        with pytest.raises(Exception, match="500"):
            await client.create_logo({"name": "Snooker", "url": URL})

    # exactly one pre-check GET; no second lookup after a non-400 failure
    assert calls == ["GET", "POST"]


@pytest.mark.asyncio
async def test_create_logo_without_url_posts_directly():
    client = _make_client()
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(method)
        return _response(201, {"id": 9, "name": "x"})

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        result = await client.create_logo({"name": "x"})

    assert result == {"id": 9, "name": "x"}
    assert calls == ["POST"]
