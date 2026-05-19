"""Unit tests for :mod:`services.plex_epg_cache` (bd-ma6r3).

The EPG cache is a sibling of :mod:`services.plex_cache` and shares its
six behavioral guarantees verbatim, with one structural difference:
the upstream call is :meth:`PlexClient.get_current_live_epg_channels`
(a DVR/section sweep) rather than ``get_sessions``. TTL is 30 s vs
the session cache's 5 s — the bead's rationale for the longer TTL is
that broadcast airings change on 30+ minute boundaries so refreshing
faster is wasted upstream load.

The tests below mirror ``test_plex_cache.py`` test-for-test (cache hit,
cache miss, stale fallback, cold-start failure, settings gate,
thundering-herd) so deviation between the two caches surfaces as a
test gap rather than silent drift.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import observability
from plex_client import PlexClientError, PlexEpgEntry
from services import plex_epg_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    grandparent_title: str = "MLB Baseball",
    channel_call_sign: str = "8.1 | NBC: KGW Portland",
) -> PlexEpgEntry:
    """Build a representative :class:`PlexEpgEntry` for cache assertions."""
    return PlexEpgEntry(
        grandparent_title=grandparent_title,
        channel_call_sign=channel_call_sign,
        channel_identifier="8.1",
        channel_title="8.1 NBC",
        channel_vcn="8.1",
        begins_at=datetime(2026, 5, 18, 22, 0, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 19, 1, 0, 0, tzinfo=timezone.utc),
        protocol="livetv",
    )


def _enabled_settings(base_url: str = "http://plex.example:32400") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Plex."""
    settings = MagicMock()
    settings.plex_enabled = True
    settings.plex_base_url = base_url
    settings.plex_token = "token-123"
    return settings


@pytest.fixture(autouse=True)
def reset_epg_cache_state():
    """Ensure each test starts with a clean module-level cache."""
    plex_epg_cache._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    plex_epg_cache._reset_for_tests()
    observability.reset_for_tests()


def _get_counter_value(metric_key: str, source: str) -> float:
    """Return the current value of a labeled counter from the live registry."""
    metric = observability.get_metric(metric_key)
    prom_name = f"ecm_{metric_key}"
    for mf in metric.collect():
        for sample in mf.samples:
            if sample.name == prom_name and sample.labels.get("source") == source:
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Settings gate
# ---------------------------------------------------------------------------


class TestSettingsGate:
    """When Plex is disabled or unconfigured, the cache short-circuits."""

    async def test_plex_disabled_returns_empty_without_client_construction(self):
        """``plex_enabled=False`` short-circuits before constructing PlexClient."""
        settings = MagicMock()
        settings.plex_enabled = False
        settings.plex_base_url = "http://plex.example:32400"
        settings.plex_token = "token"
        with patch.object(plex_epg_cache, "get_settings", return_value=settings), \
             patch.object(plex_epg_cache, "PlexClient") as client_cls:
            result = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert result == []
        client_cls.assert_not_called()

    async def test_empty_base_url_returns_empty(self):
        """Empty ``plex_base_url`` also short-circuits — there's no upstream
        to call even if ``plex_enabled`` somehow ended up True."""
        settings = MagicMock()
        settings.plex_enabled = True
        settings.plex_base_url = ""
        settings.plex_token = "token"
        with patch.object(plex_epg_cache, "get_settings", return_value=settings), \
             patch.object(plex_epg_cache, "PlexClient") as client_cls:
            result = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert result == []
        client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Cache miss → upstream call → cache populated
# ---------------------------------------------------------------------------


class TestCacheMiss:
    """First call when the cache is empty fires the upstream sweep."""

    async def test_cold_call_fires_upstream(self):
        entries = [_make_entry()]
        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(
            return_value=entries,
        )
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            result = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert result == entries
        plex_client_mock.get_current_live_epg_channels.assert_awaited_once()
        plex_client_mock.close.assert_awaited_once()

    async def test_cold_call_returns_empty_list_when_plex_has_no_airings(self):
        """Empty upstream snapshot is a normal state — cache populates with
        ``[]`` and subsequent calls hit cache."""
        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(return_value=[])
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            result = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert result == []


# ---------------------------------------------------------------------------
# Cache hit — second call within TTL skips upstream
# ---------------------------------------------------------------------------


class TestCacheHit:
    """Once populated, the cache serves subsequent reads from memory."""

    async def test_second_call_within_ttl_does_not_call_upstream(self):
        entries = [_make_entry()]
        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(
            return_value=entries,
        )
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            first = await plex_epg_cache.maybe_get_cached_plex_epg()
            second = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert first == entries
        assert second == entries
        # Upstream fired exactly once across the two calls.
        plex_client_mock.get_current_live_epg_channels.assert_awaited_once()
        # Cache-hit counter incremented exactly once (the second call).
        assert _get_counter_value(
            "media_session_cache_hits_total", "plex_epg",
        ) == 1.0


# ---------------------------------------------------------------------------
# Stale fallback — fetch failure with prior cache returns stale
# ---------------------------------------------------------------------------


class TestStaleFallback:
    """When the upstream fails AFTER a successful prior fetch, the cache
    returns the stale entries rather than ``[]``."""

    async def test_stale_returned_after_upstream_failure(self):
        first_entries = [_make_entry()]
        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(
            side_effect=[first_entries, PlexClientError("network down")],
        )
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            first = await plex_epg_cache.maybe_get_cached_plex_epg()
            # Force TTL expiry by reaching into the module state.
            plex_epg_cache._cached_entry.cached_at -= plex_epg_cache.CACHE_TTL_SECONDS + 1
            second = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert first == first_entries
        # Stale fallback returns the SAME entries the first call cached.
        assert second == first_entries
        assert _get_counter_value(
            "media_session_stale_fallback_total", "plex_epg",
        ) == 1.0
        assert _get_counter_value(
            "media_session_fetch_errors_total", "plex_epg",
        ) == 1.0


# ---------------------------------------------------------------------------
# Cold-start failure — no prior cache, fetch fails, returns []
# ---------------------------------------------------------------------------


class TestColdStartFailure:
    """Cold-start fetch failure must return ``[]`` (never raise) and
    increment the fetch_errors counter without firing stale_fallback."""

    async def test_cold_failure_returns_empty(self):
        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(
            side_effect=PlexClientError("auth"),
        )
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            result = await plex_epg_cache.maybe_get_cached_plex_epg()
        assert result == []
        assert _get_counter_value(
            "media_session_fetch_errors_total", "plex_epg",
        ) == 1.0
        # Stale fallback did NOT fire — no prior cache to fall back to.
        assert _get_counter_value(
            "media_session_stale_fallback_total", "plex_epg",
        ) == 0.0


# ---------------------------------------------------------------------------
# Thundering-herd guard — concurrent misses collapse to one upstream call
# ---------------------------------------------------------------------------


class TestThunderingHerd:
    """Concurrent cache-miss waiters must produce exactly one upstream
    call thanks to the ``asyncio.Lock`` + double-check pattern."""

    async def test_concurrent_callers_share_one_fetch(self):
        entries = [_make_entry()]

        first_call_seen = asyncio.Event()
        release_first_call = asyncio.Event()

        async def slow_fetch():
            """Block on a barrier so concurrent waiters queue on the lock."""
            first_call_seen.set()
            await release_first_call.wait()
            return entries

        plex_client_mock = MagicMock()
        plex_client_mock.get_current_live_epg_channels = AsyncMock(
            side_effect=slow_fetch,
        )
        plex_client_mock.close = AsyncMock()
        with patch.object(plex_epg_cache, "get_settings",
                          return_value=_enabled_settings()), \
             patch.object(plex_epg_cache, "PlexClient",
                          return_value=plex_client_mock):
            # Launch 5 concurrent callers; only one should fetch.
            tasks = [
                asyncio.create_task(plex_epg_cache.maybe_get_cached_plex_epg())
                for _ in range(5)
            ]
            await first_call_seen.wait()
            release_first_call.set()
            results = await asyncio.gather(*tasks)
        for r in results:
            assert r == entries
        plex_client_mock.get_current_live_epg_channels.assert_awaited_once()
