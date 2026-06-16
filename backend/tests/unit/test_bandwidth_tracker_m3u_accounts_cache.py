"""Tests for enhancedchannelmanager-1qmn0 — decouple the heavy
``/api/m3u/accounts/`` fetch from the 10s bandwidth poll.

The provider resolver only needs each account's ``id``/``name``/
``server_url`` to build the {account_id: name} table and to URL-hostname
match providers — NOT the ~734KB ``channel_groups[]`` payload. Fetching
the full accounts list every poll was a likely driver of Dispatcharr
uWSGI worker memory creep, so ``BandwidthTracker`` now caches the list
and refreshes it on a 300s TTL (mirroring ``_maybe_refresh_channel_map``)
and threads the cached snapshot into ``resolve_active_channel_streams``.

These tests pin two contracts:

* ``_maybe_refresh_m3u_accounts`` fetches at most once per TTL window and
  re-fetches after the TTL boundary elapses.
* ``resolve_active_channel_streams`` uses a passed-in ``m3u_accounts``
  list WITHOUT calling ``client.get_m3u_accounts`` and falls back to a
  fetch when ``m3u_accounts`` is None (the on-demand endpoint path).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bandwidth_tracker import BandwidthTracker, resolve_active_channel_streams


def _make_tracker():
    client = AsyncMock()
    return BandwidthTracker(client, poll_interval=10), client


@pytest.mark.asyncio
async def test_refresh_fetches_once_within_ttl_window():
    """Multiple ``_maybe_refresh_m3u_accounts`` calls inside one TTL
    window fetch the accounts list from Dispatcharr exactly once."""
    tracker, client = _make_tracker()
    client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 1, "name": "A", "server_url": "http://a/list.m3u"}]
    )

    # Pin time to a fixed instant well past the 0.0 init so the first
    # call refreshes, and every subsequent call within the window is a
    # no-op.
    with patch("bandwidth_tracker.time.time", return_value=1000.0):
        await tracker._maybe_refresh_m3u_accounts()
        await tracker._maybe_refresh_m3u_accounts()
        await tracker._maybe_refresh_m3u_accounts()

    assert client.get_m3u_accounts.await_count == 1
    assert tracker._m3u_accounts_cache == [
        {"id": 1, "name": "A", "server_url": "http://a/list.m3u"}
    ]


@pytest.mark.asyncio
async def test_refresh_refetches_after_ttl_boundary():
    """Advancing ``time.time`` past the 300s TTL boundary triggers a
    second fetch; calls before the boundary do not."""
    tracker, client = _make_tracker()
    client.get_m3u_accounts = AsyncMock(
        side_effect=[
            [{"id": 1, "name": "first"}],
            [{"id": 2, "name": "second"}],
        ]
    )

    # First refresh at t=1000.
    with patch("bandwidth_tracker.time.time", return_value=1000.0):
        await tracker._maybe_refresh_m3u_accounts()
    assert client.get_m3u_accounts.await_count == 1

    # Still inside the 300s window — no refetch.
    with patch("bandwidth_tracker.time.time", return_value=1000.0 + 299):
        await tracker._maybe_refresh_m3u_accounts()
    assert client.get_m3u_accounts.await_count == 1
    assert tracker._m3u_accounts_cache == [{"id": 1, "name": "first"}]

    # Past the boundary — refetch and replace the cache.
    with patch("bandwidth_tracker.time.time", return_value=1000.0 + 301):
        await tracker._maybe_refresh_m3u_accounts()
    assert client.get_m3u_accounts.await_count == 2
    assert tracker._m3u_accounts_cache == [{"id": 2, "name": "second"}]


@pytest.mark.asyncio
async def test_refresh_keeps_prior_cache_on_error():
    """A Dispatcharr error during refresh is best-effort: the prior
    cache is retained and the TTL timestamp is NOT advanced (so the next
    poll retries)."""
    tracker, client = _make_tracker()
    tracker._m3u_accounts_cache = [{"id": 9, "name": "prior"}]
    client.get_m3u_accounts = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("bandwidth_tracker.time.time", return_value=1000.0):
        await tracker._maybe_refresh_m3u_accounts()

    assert tracker._m3u_accounts_cache == [{"id": 9, "name": "prior"}]
    assert tracker._last_m3u_accounts_refresh == 0.0


@pytest.mark.asyncio
async def test_resolver_uses_passed_in_accounts_without_fetch():
    """When ``m3u_accounts`` is supplied, the resolver never calls
    ``client.get_m3u_accounts`` and uses the provided list for provider
    enrichment / hostname matching."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])

    snapshot = [
        {
            "channel_uuid": "u1",
            "stream_id": None,
            "url": "https://infinity.gives/live/85796.ts",
        }
    ]
    accounts = [
        {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/list.m3u"}
    ]

    result = await resolve_active_channel_streams(
        client,
        snapshot,
        emit_metrics=False,
        m3u_accounts=accounts,
    )

    client.get_m3u_accounts.assert_not_awaited()
    # Hostname match resolved the provider name from the passed-in list.
    assert result["u1"].provider_name == "Infinity TV"
    assert result["u1"].provider_id == 6


@pytest.mark.asyncio
async def test_resolver_falls_back_to_fetch_when_accounts_none():
    """When ``m3u_accounts`` is None (the on-demand endpoint path), the
    resolver fetches the accounts list itself — preserving that code
    path."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[
            {"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/x.m3u"}
        ]
    )

    snapshot = [
        {
            "channel_uuid": "u1",
            "stream_id": None,
            "url": "https://infinity.gives/live/85796.ts",
        }
    ]

    result = await resolve_active_channel_streams(
        client,
        snapshot,
        emit_metrics=False,
        m3u_accounts=None,
    )

    client.get_m3u_accounts.assert_awaited_once()
    assert result["u1"].provider_name == "Infinity TV"
    assert result["u1"].provider_id == 6


@pytest.mark.asyncio
async def test_resolver_empty_passed_list_degrades_to_bare_hostname():
    """An empty passed-in list (cache not yet warmed) degrades to the
    bare-hostname provider_name without fetching."""
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])

    snapshot = [
        {
            "channel_uuid": "u1",
            "stream_id": None,
            "url": "https://infinity.gives/live/85796.ts",
        }
    ]

    result = await resolve_active_channel_streams(
        client,
        snapshot,
        emit_metrics=False,
        m3u_accounts=[],
    )

    client.get_m3u_accounts.assert_not_awaited()
    assert result["u1"].provider_name == "infinity.gives"
    assert result["u1"].provider_id is None
