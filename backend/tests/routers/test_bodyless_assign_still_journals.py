"""A bodyless assign still reads the numbers back and still writes the rows.

The seam these tests cross is the one the unit tests on either side of it
cannot: **the real ``DispatcharrClient``** talking to an
``httpx.MockTransport`` that behaves the way ``swagger.json`` says
``POST /api/channels/channels/assign/`` behaves — 200, empty body. Every other
test of this route substitutes an ``AsyncMock`` for the client and hands the
handler a dict, so none of them could see that the client raised
``JSONDecodeError`` on the documented success and that the router's
``except Exception`` turned it into a 500 with the read-back loop never entered
and the Journal queue never flushed.

``docs/api.md`` §"``POST /api/channels/assign-numbers``" makes two claims that
only this layer can prove: "ECM therefore issues one read-back ``GET`` per
channel" and "The row is still written". Both were false on the bodyless path.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

#: What Dispatcharr chose, observable ONLY by reading each channel back.
_CHOSEN = {42: 7.0, 43: 8.5}
_BEFORE = {42: 900.0, 43: 901.0}


def _journal_double():
    double = MagicMock()
    double.log_entries.return_value = True
    double.log_entry.return_value = MagicMock()
    double.get_request_batch_id.return_value = "batch-bodyless-assign"
    return double


def _rows(journal_double):
    rows = []
    for call in journal_double.log_entries.call_args_list:
        rows.extend(call.args[0])
    for call in journal_double.log_entry.call_args_list:
        rows.append(call.kwargs)
    return rows


def _settings():
    settings = MagicMock()
    settings.auto_rename_channel_number = False
    return settings


def _dispatcharr(record: list[tuple[str, str]]):
    """A real client over a transport that answers the way the contract says.

    ``assign`` returns 200 with an EMPTY BODY, which is the whole point. The
    per-channel ``GET`` answers with the old number until the assignment has
    landed and the chosen one afterwards, so a row carrying a chosen number can
    only have come from a read-back that actually happened.
    """
    state = {"assigned": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        record.append((request.method, path))
        if path == "/api/channels/channels/assign/":
            state["assigned"] = True
            return httpx.Response(200, content=b"")
        if path.startswith("/api/channels/channels/"):
            channel_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            numbers = _CHOSEN if state["assigned"] else _BEFORE
            return httpx.Response(
                200,
                json={
                    "id": channel_id,
                    "name": f"Channel {channel_id}",
                    "channel_number": numbers.get(channel_id),
                    "streams": [],
                },
            )
        raise AssertionError(f"unexpected upstream call: {request.method} {path}")

    client = DispatcharrClient(
        DispatcharrSettings(
            url="http://dispatcharr", auth_method="api_key", api_key="k"
        )
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def _post(async_client, client, journal_double, body):
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.get_settings", return_value=_settings()), \
         patch("routers.channels.journal", journal_double):
        return await async_client.post("/api/channels/assign-numbers", json=body)


@pytest.mark.asyncio
async def test_a_bodyless_assign_is_not_a_500(async_client):
    """The documented success must not surface as an internal error."""
    record: list[tuple[str, str]] = []
    client = _dispatcharr(record)
    journal_double = _journal_double()
    try:
        response = await _post(
            async_client, client, journal_double, {"channel_ids": [42, 43]},
        )
    finally:
        await client._client.aclose()

    assert response.status_code == 200, response.text
    assert ("POST", "/api/channels/channels/assign/") in record


@pytest.mark.asyncio
async def test_the_read_back_runs_and_the_rows_carry_what_it_observed(async_client):
    """One read-back ``GET`` per channel AFTER the assign, and rows to match.

    This is ``docs/api.md``'s "ECM therefore issues one read-back ``GET`` per
    channel" as an executable assertion. The numbers asserted are unobtainable
    from the request and absent from the response, so they can only have come
    from the reads that follow the assign.
    """
    record: list[tuple[str, str]] = []
    client = _dispatcharr(record)
    journal_double = _journal_double()
    try:
        response = await _post(
            async_client, client, journal_double, {"channel_ids": [42, 43]},
        )
    finally:
        await client._client.aclose()

    assert response.status_code == 200, response.text

    assign_at = record.index(("POST", "/api/channels/channels/assign/"))
    after = record[assign_at + 1:]
    assert after == [
        ("GET", "/api/channels/channels/42/"),
        ("GET", "/api/channels/channels/43/"),
    ], record

    rows = _rows(journal_double)
    assert [row["entity_id"] for row in rows] == [42, 43], rows
    assert [row["after_value"]["channel_number"] for row in rows] == [7.0, 8.5], rows
    for row in rows:
        assert "has not read it back" not in row["description"], row


@pytest.mark.asyncio
async def test_the_row_is_still_written_when_the_read_back_fails(async_client):
    """``docs/api.md``'s "The row is still written", on the bodyless path.

    The assignment landed and only the read failed, so the row says the number
    has not been read back rather than naming one nobody observed — but it is
    written. Before the fix nothing reached this branch at all: the client
    raised on the empty assign response and the queue was flushed empty.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/channels/channels/assign/":
            return httpx.Response(200, content=b"")
        if request.method == "GET" and path.endswith("/42/"):
            # The pre-assign read succeeds once, the read-back is refused.
            if handler.reads:
                return httpx.Response(503, json={"detail": "upstream is busy"})
            handler.reads += 1
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Channel 42",
                    "channel_number": 900.0,
                    "streams": [],
                },
            )
        raise AssertionError(f"unexpected upstream call: {request.method} {path}")

    handler.reads = 0
    client = DispatcharrClient(
        DispatcharrSettings(
            url="http://dispatcharr", auth_method="api_key", api_key="k"
        )
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    journal_double = _journal_double()
    try:
        response = await _post(
            async_client, client, journal_double, {"channel_ids": [42]},
        )
    finally:
        await client._client.aclose()

    assert response.status_code == 200, response.text
    rows = _rows(journal_double)
    assert [row["entity_id"] for row in rows] == [42], rows
    assert rows[0]["after_value"]["channel_number"] is None, rows
    assert "has not read it back" in rows[0]["description"], rows


@pytest.mark.asyncio
async def test_the_caller_is_still_told_about_unwritten_rows(async_client):
    """``journalRowsUnwritten`` needs an object to be stamped onto.

    The router stamps the count onto the upstream response and logs an error if
    it cannot. A bodyless success therefore has to produce a dict, not ``None``
    — otherwise a caller whose rows were lost is never told.
    """
    record: list[tuple[str, str]] = []
    client = _dispatcharr(record)
    journal_double = _journal_double()
    journal_double.log_entries.return_value = False
    journal_double.log_entry.return_value = None
    try:
        response = await _post(
            async_client, client, journal_double, {"channel_ids": [42, 43]},
        )
    finally:
        await client._client.aclose()

    assert response.status_code == 200, response.text
    body = json.loads(response.text)
    assert isinstance(body, dict), body
    assert body["journalRowsUnwritten"] == 2, body
