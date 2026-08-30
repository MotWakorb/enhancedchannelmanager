"""A 2xx with no body is a SUCCESS for every Dispatcharr call that declares one.

Stated as a property, because the reproduction that found it is an example of
the property and not its specification: **no client method whose upstream
contract declares no response body may treat a bodyless 2xx as a failure.**

The example. ``POST /api/channels/channels/assign/`` declares no response body
beyond the string "Channels have been auto-assigned!" (``swagger.json``), which
is precisely why ``routers/channels.py`` reads the assigned numbers back one
channel at a time. The client nevertheless did::

    response.raise_for_status()
    return response.json()

so ``httpx.Response(200, content=b"")`` — the documented success — raised
``json.JSONDecodeError`` inside the client. The router's ``except Exception``
turned that into an HTTP 500 **before the read-back loop was ever entered**, so
``pending_rows`` was still empty when the ``finally`` flushed and the landed
renumber left no Journal row at all: not the numbers, and not even the honest
"ECM has not read it back". Both sentences ``docs/api.md`` had just written for
this fix ("ECM therefore issues one read-back ``GET`` per channel", "The row is
still written") were false in exactly the case the fix exists to handle.

The other methods in the sweep below already carried the
``if response.content`` guard. They are exercised here anyway: a guard that is
present in the source is not a guard that is proven, and this module is what
makes the claim behavioural. The end-to-end consequence — read-back runs and
rows are journalled on a bodyless assign — is pinned separately in
``tests/routers/test_bodyless_assign_still_journals.py``.

**Scope of the sweep, honestly stated.** The set below is every client method
that issues a *write* verb to a path whose ``swagger.json`` 2xx response
carries no schema. It deliberately excludes the ``GET`` methods that also lack
a response schema (``get_version``, ``get_system_events``, ``get_channel_stats``,
``get_channel_stats_detail``, ``get_channel_streams``): a missing schema on a
DRF-generated ``GET`` is a documentation gap, not a no-body contract, and those
endpoints exist only to return the body ECM then reads. Making them tolerate an
empty body would convert a real upstream fault into a silent empty result.
"""
from __future__ import annotations

import httpx
import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _client(handler) -> DispatcharrClient:
    client = DispatcharrClient(
        DispatcharrSettings(
            url="http://dispatcharr", auth_method="api_key", api_key="k"
        )
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_assign_channel_numbers_survives_the_bodyless_success():
    """The reviewer's reproduction, verbatim.

    ``DispatcharrClient.assign_channel_numbers([1], None)`` against
    ``httpx.Response(200, content=b"")`` raised
    ``JSONDecodeError: Expecting value: line 1 column 1 (char 0)``.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"")

    client = _client(handler)
    try:
        result = await client.assign_channel_numbers([1], None)
    finally:
        await client._client.aclose()

    assert isinstance(result, dict), result
    assert seen[0].url.path == "/api/channels/channels/assign/"


@pytest.mark.asyncio
async def test_the_bodyless_success_carries_no_channel_numbers():
    """The synthetic envelope must not look like it answered the question.

    Dispatcharr chose the numbers and did not report them. A stand-in body that
    named or implied any is the same false claim the read-back exists to avoid,
    so the returned dict says only that the call succeeded.
    """
    client = _client(lambda request: httpx.Response(200, content=b""))
    try:
        result = await client.assign_channel_numbers([1, 2, 3], None)
    finally:
        await client._client.aclose()

    assert "channel_number" not in repr(result), result
    assert not any(
        isinstance(value, (list, dict)) for value in result.values()
    ), result


@pytest.mark.asyncio
async def test_a_body_that_is_present_is_still_returned_unchanged():
    """The guard must not swallow a body Dispatcharr does send.

    ``swagger.json`` is a contract, not a promise about every deployment, and
    the router reads the returned object (it stamps ``journalRowsUnwritten``
    onto it). A version that starts returning something must still be seen.
    """
    client = _client(lambda request: httpx.Response(200, json={"status": "ok"}))
    try:
        result = await client.assign_channel_numbers([1], 100.0)
    finally:
        await client._client.aclose()

    assert result == {"status": "ok"}


#: ``(method name, positional args)`` for every client method that issues a
#: write verb to a path whose ``swagger.json`` 2xx response declares no schema.
#: Derived by cross-referencing ``swagger.json`` against the ``self._request``
#: call sites in ``dispatcharr_client.py``; see the module docstring for what is
#: deliberately excluded and why.
_BODYLESS_CONTRACT_WRITES = [
    ("assign_channel_numbers", ([1], None)),
    ("refresh_m3u_account", (1,)),
    ("refresh_all_m3u_accounts", ()),
    ("refresh_epg_source", (1,)),
    ("bulk_delete_logos", ([1, 2],)),
    ("update_profile_channel", (1, 2, {"enabled": True})),
    ("stop_channel", (1,)),
    ("stop_client", (1,)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name,args",
    _BODYLESS_CONTRACT_WRITES,
    ids=[name for name, _ in _BODYLESS_CONTRACT_WRITES],
)
async def test_every_bodyless_contract_write_survives_an_empty_2xx(
    method_name, args
):
    """The property itself, one method at a time."""
    client = _client(lambda request: httpx.Response(204, content=b""))
    try:
        result = await getattr(client, method_name)(*args)
    finally:
        await client._client.aclose()

    assert isinstance(result, dict), (method_name, result)
