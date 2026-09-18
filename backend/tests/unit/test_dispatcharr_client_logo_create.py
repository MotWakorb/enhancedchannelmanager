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
from dispatcharr_client import DispatcharrClient, logo_was_reused, strip_logo_reuse_marker

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

    assert strip_logo_reuse_marker(result) == EXISTING
    assert logo_was_reused(result) is True
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

    assert strip_logo_reuse_marker(result) == EXISTING
    assert logo_was_reused(result) is True


@pytest.mark.asyncio
async def test_create_logo_raises_with_status_and_classification_when_400_has_no_matching_row():
    """PR #1014 review item 6: the status and a fixed classification, never the
    upstream body (it can echo a credentialed logo URL)."""
    client = _make_client()

    async def fake_request(method, path, **kwargs):
        if method == "GET":
            return _response(200, _page([]))
        return _response(400, {"name": ["This field is required."]},
                         text='{"name":["This field is required."]}')

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        with pytest.raises(Exception, match=r"400 \(validation\)") as error:
            await client.create_logo({"name": "", "url": URL})
    assert "This field is required" not in str(error.value)


@pytest.mark.asyncio
async def test_create_logo_failure_log_never_carries_the_response_body(caplog):
    """A reflected token in the upstream body must not reach any log handler."""
    import logging
    client = _make_client()
    canary = "CANARY-TOKEN-51c0ffee"
    body = {"url": [f"Invalid URL http://p.example/logo.png?token={canary}"]}

    async def fake_request(method, path, **kwargs):
        if method == "GET":
            return _response(200, _page([]))
        return _response(400, body, text=str(body))

    with caplog.at_level(logging.DEBUG), \
         patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        with pytest.raises(Exception) as error:
            await client.create_logo({"name": "x", "url": f"http://p.example/logo.png?token={canary}"})
    assert canary not in caplog.text
    assert canary not in str(error.value)
    assert "http://p.example" not in caplog.text


@pytest.mark.asyncio
async def test_create_logo_classifies_a_duplicate_url_400_without_a_row_to_reuse():
    client = _make_client()

    async def fake_request(method, path, **kwargs):
        if method == "GET":
            return _response(200, _page([]))
        return _response(400, {"url": ["logo with this url already exists."]})

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        with pytest.raises(Exception, match=r"400 \(duplicate_url\)"):
            await client.create_logo({"name": "Snooker", "url": URL})


@pytest.mark.asyncio
async def test_reused_rows_carry_the_marker_and_created_rows_do_not():
    """PR #1014 review item 1: created-vs-reused must be distinguishable."""
    from dispatcharr_client import LOGO_REUSED_KEY, logo_was_reused, strip_logo_reuse_marker
    client = _make_client()

    # Pre-check hit -> reused.
    with patch.object(client, "_request", AsyncMock(
        return_value=_response(200, _page([EXISTING]))
    )):
        reused = await client.create_logo({"name": "Snooker", "url": URL})
    assert logo_was_reused(reused) is True
    assert reused["id"] == 765
    assert strip_logo_reuse_marker(reused) == EXISTING
    assert LOGO_REUSED_KEY not in strip_logo_reuse_marker(reused)

    # Post-400 reconciliation -> reused.
    pages = iter([_response(200, _page([])), _response(200, _page([EXISTING]))])

    async def race(method, path, **kwargs):
        if method == "GET":
            return next(pages)
        return _response(400, {"url": ["logo with this url already exists."]})

    with patch.object(client, "_request", AsyncMock(side_effect=race)):
        reused_after_race = await client.create_logo({"name": "Snooker", "url": URL})
    assert logo_was_reused(reused_after_race) is True

    # Genuine create -> no marker.
    created = {"id": 2257, "name": "Snooker", "url": URL}

    async def create(method, path, **kwargs):
        if method == "GET":
            return _response(200, _page([]))
        return _response(201, created)

    with patch.object(client, "_request", AsyncMock(side_effect=create)):
        fresh = await client.create_logo({"name": "Snooker", "url": URL})
    assert fresh == created
    assert logo_was_reused(fresh) is False


@pytest.mark.asyncio
async def test_precheck_false_posts_directly_but_still_reconciles_a_400():
    """PR #1014 review item 5: a caller that already scanned the catalog gets
    no second scan before the POST; the post-400 race path is retained."""
    client = _make_client()
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append(method)
        if method == "GET":
            return _response(200, _page([EXISTING]))
        return _response(400, {"url": ["logo with this url already exists."]})

    with patch.object(client, "_request", AsyncMock(side_effect=fake_request)):
        result = await client.create_logo({"name": "Snooker", "url": URL}, precheck=False)
    assert calls == ["POST", "GET"]
    assert result["id"] == 765

    calls.clear()

    async def created(method, path, **kwargs):
        calls.append(method)
        return _response(201, {"id": 2257, "name": "Snooker", "url": URL})

    with patch.object(client, "_request", AsyncMock(side_effect=created)):
        await client.create_logo({"name": "Snooker", "url": URL}, precheck=False)
    assert calls == ["POST"]


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
