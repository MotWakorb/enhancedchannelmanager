"""Unit tests for ADR-013 §D3/§D4 caches (bead 312nk.4).

Two caches collapse the remaining per-write Dispatcharr round-trips to rare:

* §D3 stream_id -> provider cache: ``resolve_active_channel_streams`` serves
  cache HITS for free, batches only cache-MISS stream ids into ONE
  ``get_streams_by_ids`` call, populates the cache from the response, and is
  invalidated (whole-map clear) by the WS ``stream_rehash`` /
  ``channels_created`` events plus a coarse safety TTL. The bd-gy5nd
  URL-hostname fallback MUST still run when the stream-id path yields nothing.
* §D4 user_id -> username TTL cache: replaces the inline per-write
  ``get_users()`` fetch; refreshes only on TTL expiry or a miss for an unknown
  user_id, and fetches NOTHING when user resolution isn't needed.

A controllable clock (``now_fn``) drives every TTL assertion — no sleeping.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bandwidth_tracker import (
    BandwidthTracker,
    StreamProviderCache,
    UserUsernameCache,
    resolve_active_channel_streams,
)


# ----------------------------------------------------------------------
# StreamProviderCache (§D3) — the cache primitive in isolation
# ----------------------------------------------------------------------


class TestStreamProviderCache:
    def test_get_batch_separates_hits_from_misses(self):
        clock = {"t": 0.0}
        cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: clock["t"])
        cache.put(10, provider_id=6, stream_name="US: TNT")
        cache.put(11, provider_id=7, stream_name="US: TBS")

        hits, misses = cache.get_batch([10, 11, 12])

        assert hits == {10: (6, "US: TNT"), 11: (7, "US: TBS")}
        assert misses == [12]

    def test_expired_entries_become_misses(self):
        clock = {"t": 0.0}
        cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: clock["t"])
        cache.put(10, provider_id=6, stream_name="US: TNT")

        clock["t"] = 301.0  # past the TTL
        hits, misses = cache.get_batch([10])

        assert hits == {}
        assert misses == [10]

    def test_clear_empties_the_whole_map(self):
        cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
        cache.put(10, provider_id=6, stream_name="US: TNT")
        cache.put(11, provider_id=7, stream_name="US: TBS")

        cache.clear()

        hits, misses = cache.get_batch([10, 11])
        assert hits == {}
        assert sorted(misses) == [10, 11]


# ----------------------------------------------------------------------
# resolve_active_channel_streams with the §D3 cache wired in
# ----------------------------------------------------------------------


def _client_returning(streams):
    client = AsyncMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_streams_by_ids = AsyncMock(return_value=streams)
    return client


@pytest.mark.asyncio
async def test_cache_hit_serves_without_calling_get_streams_by_ids():
    """(a) A fully-cached snapshot resolves WITHOUT any by-ids round-trip."""
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
    cache.put(85796, provider_id=6, stream_name="US: TNT")

    client = _client_returning([])  # would explain a miss; must not be called
    snapshot = [{"channel_uuid": "u1", "stream_id": 85796, "url": None}]
    accounts = [{"id": 6, "name": "Infinity TV", "server_url": "https://infinity.gives/x.m3u"}]

    result = await resolve_active_channel_streams(
        client,
        snapshot,
        emit_metrics=False,
        m3u_accounts=accounts,
        stream_provider_cache=cache,
    )

    client.get_streams_by_ids.assert_not_awaited()
    assert result["u1"].provider_id == 6
    assert result["u1"].stream_id == 85796
    assert result["u1"].stream_name == "US: TNT"
    assert result["u1"].provider_name == "Infinity TV"


@pytest.mark.asyncio
async def test_only_miss_ids_are_batched():
    """(b) With one cached id and one new id, ONLY the miss id is batched."""
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
    cache.put(10, provider_id=6, stream_name="Cached Stream")

    client = _client_returning([{"id": 11, "m3u_account": 7, "name": "Fresh Stream"}])
    snapshot = [
        {"channel_uuid": "u1", "stream_id": 10, "url": None},
        {"channel_uuid": "u2", "stream_id": 11, "url": None},
    ]
    accounts = [
        {"id": 6, "name": "Six", "server_url": "https://six.example/x.m3u"},
        {"id": 7, "name": "Seven", "server_url": "https://seven.example/x.m3u"},
    ]

    result = await resolve_active_channel_streams(
        client,
        snapshot,
        emit_metrics=False,
        m3u_accounts=accounts,
        stream_provider_cache=cache,
    )

    # Only the missing id (11) is sent to the batch call — not 10.
    client.get_streams_by_ids.assert_awaited_once_with([11])
    assert result["u1"].provider_id == 6  # served from cache
    assert result["u1"].stream_name == "Cached Stream"
    assert result["u2"].provider_id == 7  # freshly fetched
    assert result["u2"].stream_name == "Fresh Stream"
    # The fetched stream is now cached.
    hits, misses = cache.get_batch([11])
    assert hits == {11: (7, "Fresh Stream")}
    assert misses == []


@pytest.mark.asyncio
async def test_clear_forces_refetch():
    """(c) After ``clear()`` (the stream_rehash/channels_created hook), the
    next resolve re-fetches the previously-cached id."""
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
    client = _client_returning([{"id": 10, "m3u_account": 6, "name": "Stream X"}])
    snapshot = [{"channel_uuid": "u1", "stream_id": 10, "url": None}]
    accounts = [{"id": 6, "name": "Six", "server_url": "https://six.example/x.m3u"}]

    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 1

    # Second resolve: served from cache, no new fetch.
    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 1

    # Invalidate (whole-map clear) → next resolve re-fetches.
    cache.clear()
    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 2


@pytest.mark.asyncio
async def test_ttl_expiry_forces_refresh():
    """(d) TTL expiry forces a refresh even without an invalidation event
    (the degraded poll-fallback / missed-event safety net)."""
    clock = {"t": 0.0}
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: clock["t"])
    client = _client_returning([{"id": 10, "m3u_account": 6, "name": "Stream X"}])
    snapshot = [{"channel_uuid": "u1", "stream_id": 10, "url": None}]
    accounts = [{"id": 6, "name": "Six", "server_url": "https://six.example/x.m3u"}]

    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 1

    # Within TTL — no refetch.
    clock["t"] = 299.0
    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 1

    # Past TTL — refetch.
    clock["t"] = 301.0
    await resolve_active_channel_streams(
        client, snapshot, emit_metrics=False, m3u_accounts=accounts,
        stream_provider_cache=cache,
    )
    assert client.get_streams_by_ids.await_count == 2


@pytest.mark.asyncio
async def test_url_hostname_fallback_still_runs_with_cache_present():
    """(e) CRITICAL bd-gy5nd regression test: when the stream-id path yields
    NOTHING (no stream_id, no URL-derived id, but a parseable URL), the
    URL-hostname fallback STILL runs and attributes the provider — the §D3
    cache sits in front of the stream-id lookup ONLY and must not
    short-circuit the fall-through.
    """
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
    client = _client_returning([])
    # No stream_id and a URL whose trailing token is NOT a positive int, so
    # the bd-kbgey URL-derived stream-id parse also yields nothing → the only
    # path left is the bd-gy5nd URL-hostname match.
    snapshot = [
        {
            "channel_uuid": "u1",
            "stream_id": None,
            "url": "https://infinity.gives/live/mot/playlist.m3u8",
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
        stream_provider_cache=cache,
    )

    # Stream-id lookup never fired (no stream ids to resolve).
    client.get_streams_by_ids.assert_not_awaited()
    # The bd-gy5nd URL-hostname fallback attributed the provider.
    assert result["u1"].provider_id == 6
    assert result["u1"].provider_name == "Infinity TV"
    assert result["u1"].hostname == "infinity.gives"
    assert result["u1"].stream_id is None  # stream-row identity stays NULL


@pytest.mark.asyncio
async def test_url_hostname_fallback_runs_when_cached_miss_then_by_ids_misses():
    """bd-gy5nd fall-through when a stream_id IS present but the by-ids call
    returns no matching row — control must still fall to URL-hostname match
    exactly as today (the cache does not bypass this)."""
    cache = StreamProviderCache(ttl_seconds=300, now_fn=lambda: 0.0)
    # by-ids returns an unrelated stream (id 999) so stream 10 is "not found".
    client = _client_returning([{"id": 999, "m3u_account": 1, "name": "Other"}])
    snapshot = [
        {
            "channel_uuid": "u1",
            "stream_id": 10,
            "url": "https://infinity.gives/live/x/y.ts",
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
        stream_provider_cache=cache,
    )

    client.get_streams_by_ids.assert_awaited_once_with([10])
    # Stream-id path missed (10 not in response) → URL-hostname fallback ran.
    assert result["u1"].provider_id == 6
    assert result["u1"].provider_name == "Infinity TV"


# ----------------------------------------------------------------------
# UserUsernameCache (§D4)
# ----------------------------------------------------------------------


class TestUserUsernameCache:
    def test_hit_within_ttl(self):
        clock = {"t": 0.0}
        cache = UserUsernameCache(ttl_seconds=300, now_fn=lambda: clock["t"])
        cache.replace({"1": "alice", "2": "bob"})
        assert not cache.is_expired()
        assert cache.get("1") == "alice"
        assert cache.contains("2")
        assert not cache.contains("3")

    def test_expiry(self):
        clock = {"t": 0.0}
        cache = UserUsernameCache(ttl_seconds=300, now_fn=lambda: clock["t"])
        cache.replace({"1": "alice"})
        assert not cache.is_expired()
        clock["t"] = 301.0
        assert cache.is_expired()

    def test_empty_cache_is_expired(self):
        cache = UserUsernameCache(ttl_seconds=300, now_fn=lambda: 0.0)
        assert cache.is_expired()  # never populated → must refresh on first use


def _user_resolution_tracker(now_fn):
    from config import DispatcharrSettings

    client = AsyncMock()
    # A real settings model so the cache TTL readers see the int defaults (300)
    # instead of a MagicMock attribute (whose ``__float__`` is 1.0).
    client.settings = DispatcharrSettings(url="http://disp", username="u", password="p")
    # The §D4 user cache reads ``cache_now_fn`` (independent of the
    # telemetry-throttle ``now_fn``), so drive TTL through that.
    tracker = BandwidthTracker(client, poll_interval=10, cache_now_fn=now_fn)
    return tracker, client


@pytest.mark.asyncio
async def test_user_cache_hit_serves_without_get_users():
    """(f.1) A warm, unexpired user cache covering the snapshot's user_ids
    resolves usernames WITHOUT calling ``get_users()``."""
    clock = {"t": 0.0}
    tracker, client = _user_resolution_tracker(lambda: clock["t"])
    client.get_users = AsyncMock(return_value=[{"id": 1, "username": "alice"}])
    tracker._user_username_cache.replace({"1": "alice"})

    user_map = await tracker._resolve_usernames({"1"})

    client.get_users.assert_not_awaited()
    assert user_map == {"1": "alice"}


@pytest.mark.asyncio
async def test_user_cache_miss_triggers_one_refresh():
    """(f.2) A user_id absent from the cache triggers ONE refresh, then the
    refreshed map is served for the rest of the TTL."""
    clock = {"t": 0.0}
    tracker, client = _user_resolution_tracker(lambda: clock["t"])
    client.get_users = AsyncMock(
        return_value=[{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]
    )

    # Cold cache → miss → one refresh.
    user_map = await tracker._resolve_usernames({"1", "2"})
    assert client.get_users.await_count == 1
    assert user_map == {"1": "alice", "2": "bob"}

    # Subsequent resolves within TTL with known ids serve from cache.
    await tracker._resolve_usernames({"1"})
    assert client.get_users.await_count == 1


@pytest.mark.asyncio
async def test_user_cache_ttl_expiry_triggers_refresh():
    """(f.3) TTL expiry forces a fresh ``get_users()`` even for known ids."""
    clock = {"t": 0.0}
    tracker, client = _user_resolution_tracker(lambda: clock["t"])
    client.get_users = AsyncMock(return_value=[{"id": 1, "username": "alice"}])

    await tracker._resolve_usernames({"1"})
    assert client.get_users.await_count == 1

    clock["t"] = 299.0
    await tracker._resolve_usernames({"1"})
    assert client.get_users.await_count == 1

    clock["t"] = 301.0
    await tracker._resolve_usernames({"1"})
    assert client.get_users.await_count == 2


@pytest.mark.asyncio
async def test_user_cache_miss_for_unknown_id_refreshes_once():
    """(f.4) A miss for a NEW user_id not in the map triggers one refresh."""
    clock = {"t": 0.0}
    tracker, client = _user_resolution_tracker(lambda: clock["t"])
    client.get_users = AsyncMock(
        return_value=[{"id": 1, "username": "alice"}, {"id": 9, "username": "ninth"}]
    )
    tracker._user_username_cache.replace({"1": "alice"})

    # user_id 9 is unknown → one refresh.
    user_map = await tracker._resolve_usernames({"1", "9"})
    assert client.get_users.await_count == 1
    assert user_map == {"1": "alice", "9": "ninth"}


@pytest.mark.asyncio
async def test_user_resolution_not_needed_fetches_nothing():
    """(f.5) ``need_user_resolution=False`` (no user ids) fetches nothing —
    the ``_resolve_usernames`` helper is never even called with an empty set
    in the write path; assert the helper short-circuits an empty request."""
    clock = {"t": 0.0}
    tracker, client = _user_resolution_tracker(lambda: clock["t"])
    client.get_users = AsyncMock(return_value=[{"id": 1, "username": "alice"}])

    user_map = await tracker._resolve_usernames(set())

    client.get_users.assert_not_awaited()
    assert user_map == {}


# ----------------------------------------------------------------------
# Subscriber -> tracker invalidation hook
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_stream_rehash_clears_provider_cache():
    """The WS ``stream_rehash`` event clears the tracker's stream->provider
    cache via the invalidation hook; ``channels_created`` does the same;
    unknown events do NOT clear it."""
    from channel_stats_subscriber import ChannelStatsSubscriber

    client = AsyncMock()
    tracker = BandwidthTracker(client, poll_interval=10)
    tracker._stream_provider_cache.put(10, provider_id=6, stream_name="X")

    sub = ChannelStatsSubscriber(client, tracker)

    await sub._handle_message('{"type": "stream_rehash"}')
    hits, misses = tracker._stream_provider_cache.get_batch([10])
    assert hits == {}  # cleared

    # Repopulate, then channels_created also clears.
    tracker._stream_provider_cache.put(10, provider_id=6, stream_name="X")
    await sub._handle_message('{"type": "channels_created"}')
    hits, _ = tracker._stream_provider_cache.get_batch([10])
    assert hits == {}

    # An unknown event must NOT clear the cache.
    tracker._stream_provider_cache.put(10, provider_id=6, stream_name="X")
    await sub._handle_message('{"type": "something_else"}')
    hits, _ = tracker._stream_provider_cache.get_batch([10])
    assert hits == {10: (6, "X")}
