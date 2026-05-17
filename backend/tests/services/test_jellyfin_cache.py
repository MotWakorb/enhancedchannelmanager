"""Unit tests for :mod:`services.jellyfin_cache` (bd-r5f0c.3, epic bd-r5f0c).

The cache module sits between :mod:`jellyfin_client` and any downstream
consumer (BandwidthTracker, resolver). The tests below lock the six
behavioral guarantees mirroring :mod:`services.emby_cache`'s contract:

1. Cache hit — second call within TTL does not call JellyfinClient.
2. Cache miss — first call calls JellyfinClient; subsequent within TTL
   returns the same cached list without firing again.
3. Stale fallback — populated cache + subsequent fetch failure returns
   the stale list, not empty.
4. Cold-start failure — empty cache + fetch failure returns empty list
   and logs a WARN.
5. Jellyfin disabled — settings gate short-circuits before any cache or
   client interaction.
6. Thundering-herd — concurrent cache-miss callers result in exactly
   one upstream call thanks to the ``asyncio.Lock``.

All tests reset the module-level cache via ``_reset_for_tests`` so state
does not bleed between tests in the same process.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jellyfin_client import JellyfinClientError, JellyfinSession
from services import jellyfin_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(user_name: str = "alice") -> JellyfinSession:
    """Build a representative :class:`JellyfinSession` for assertions."""
    return JellyfinSession(
        session_id=f"jf-sess-{user_name}",
        user_id=f"jf-uid-{user_name}",
        user_name=user_name,
        remote_endpoint="10.0.0.5",
        now_playing_item_name="ESPN",
        now_playing_channel_name=None,
        last_activity_date="2026-05-17T12:00:00Z",
    )


def _make_jellyfin_enabled_settings(base_url: str = "http://jellyfin.example") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Jellyfin."""
    settings = MagicMock()
    settings.jellyfin_enabled = True
    settings.jellyfin_base_url = base_url
    settings.jellyfin_api_key = "jf-key-123"
    return settings


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Ensure each test starts with a clean module-level cache."""
    jellyfin_cache._reset_for_tests()
    yield
    jellyfin_cache._reset_for_tests()


# ---------------------------------------------------------------------------
# Behavior 1 + 2: cache hit / cache miss
# ---------------------------------------------------------------------------


class TestCacheHitAndMiss:
    """First call fetches; subsequent calls within the TTL window do not."""

    async def test_first_call_invokes_jellyfin_client_once(self):
        """Cache miss path constructs JellyfinClient and calls get_sessions."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client) as client_cls:
            result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == [session]
        assert client_cls.call_count == 1
        mock_client.get_sessions.assert_awaited_once()
        mock_client.close.assert_awaited_once()

    async def test_second_call_within_ttl_hits_cache(self):
        """Within TTL, a second call returns cached data without refetching."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client) as client_cls:
            first = await jellyfin_cache.get_cached_jellyfin_sessions()
            second = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert first == [session]
        assert second == [session]
        assert client_cls.call_count == 1
        mock_client.get_sessions.assert_awaited_once()

    async def test_call_after_ttl_expiry_refetches(self):
        """After the TTL elapses, the next call hits Jellyfin again.

        We monkey-patch ``time.monotonic`` to jump forward past the TTL
        boundary rather than actually sleeping.
        """
        session_v1 = _make_session("alice")
        session_v2 = _make_session("bob")
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=[[session_v1], [session_v2]])
        mock_client.close = AsyncMock()

        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client), \
             patch.object(jellyfin_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await jellyfin_cache.get_cached_jellyfin_sessions()
            clock[0] += jellyfin_cache.CACHE_TTL_SECONDS + 0.1
            second = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert first == [session_v1]
        assert second == [session_v2]
        assert mock_client.get_sessions.await_count == 2


# ---------------------------------------------------------------------------
# Behavior 3: stale fallback on subsequent failure
# ---------------------------------------------------------------------------


class TestStaleFallback:
    """Once a value is cached, a later fetch failure surfaces the stale value."""

    async def test_subsequent_failure_returns_stale_cache(self, caplog):
        """A populated cache + later fetch failure returns the stale list."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=[[session], JellyfinClientError("network down")]
        )
        mock_client.close = AsyncMock()

        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client), \
             patch.object(jellyfin_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await jellyfin_cache.get_cached_jellyfin_sessions()
            clock[0] += jellyfin_cache.CACHE_TTL_SECONDS + 0.1
            with caplog.at_level(logging.WARNING, logger=jellyfin_cache.logger.name):
                second = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert first == [session]
        assert second == [session]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stale" in r.getMessage().lower() for r in warnings), warnings


# ---------------------------------------------------------------------------
# Behavior 4: cold-start fetch failure
# ---------------------------------------------------------------------------


class TestColdStartFailure:
    """No prior cache + fetch failure returns empty list + WARN."""

    async def test_cold_start_failure_returns_empty_list(self, caplog):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=JellyfinClientError("connect refused")
        )
        mock_client.close = AsyncMock()

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client):
            with caplog.at_level(logging.WARNING, logger=jellyfin_cache.logger.name):
                result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("no prior cache" in r.getMessage().lower() for r in warnings), warnings

    async def test_cold_start_failure_does_not_populate_cache(self):
        """A failed cold-start fetch must not poison the cache with []."""
        mock_client = AsyncMock()
        session = _make_session()
        mock_client.get_sessions = AsyncMock(
            side_effect=[JellyfinClientError("boom"), [session]]
        )
        mock_client.close = AsyncMock()

        with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
             patch.object(jellyfin_cache, "JellyfinClient", return_value=mock_client):
            first = await jellyfin_cache.get_cached_jellyfin_sessions()
            second = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert first == []
        assert second == [session]
        assert mock_client.get_sessions.await_count == 2


# ---------------------------------------------------------------------------
# Behavior 5: settings gate
# ---------------------------------------------------------------------------


class TestSettingsGate:
    """When Jellyfin is disabled or unconfigured, no cache or client interaction."""

    async def test_jellyfin_disabled_returns_empty_without_client(self):
        settings = _make_jellyfin_enabled_settings()
        settings.jellyfin_enabled = False

        with patch.object(jellyfin_cache, "get_settings", return_value=settings), \
             patch.object(jellyfin_cache, "JellyfinClient") as client_cls:
            result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_empty_base_url_treated_as_disabled(self):
        """An empty base_url means 'Jellyfin not configured' — same short-circuit."""
        settings = _make_jellyfin_enabled_settings()
        settings.jellyfin_base_url = ""

        with patch.object(jellyfin_cache, "get_settings", return_value=settings), \
             patch.object(jellyfin_cache, "JellyfinClient") as client_cls:
            result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_missing_jellyfin_attributes_treated_as_disabled(self):
        """Settings model without jellyfin_* fields (pre-W4) → disabled.

        The ``getattr`` default in the cache function should treat that as
        'Jellyfin disabled' rather than raising AttributeError.
        """
        class BareSettings:
            pass

        with patch.object(jellyfin_cache, "get_settings", return_value=BareSettings()), \
             patch.object(jellyfin_cache, "JellyfinClient") as client_cls:
            result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_disabled_does_not_touch_cache(self):
        """Disabled short-circuit returns [] even when a stale cache exists."""
        jellyfin_cache._store_entry([_make_session("ghost")])

        settings = _make_jellyfin_enabled_settings()
        settings.jellyfin_enabled = False

        with patch.object(jellyfin_cache, "get_settings", return_value=settings), \
             patch.object(jellyfin_cache, "JellyfinClient") as client_cls:
            result = await jellyfin_cache.get_cached_jellyfin_sessions()

        assert result == []
        client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 6: thundering-herd prevention
# ---------------------------------------------------------------------------


class TestThunderingHerdPrevention:
    """Concurrent cache-miss callers must collapse to one upstream fetch."""

    async def test_concurrent_misses_share_a_single_fetch(self):
        """10 concurrent calls during cold-start fire JellyfinClient exactly once."""
        session = _make_session()
        gate = asyncio.Event()
        construct_count = 0
        fetch_count = 0

        class CountingClient:
            def __init__(self, *, base_url: str, api_key: str, **kwargs):
                nonlocal construct_count
                construct_count += 1
                self.base_url = base_url
                self.api_key = api_key

            async def get_sessions(self):
                nonlocal fetch_count
                fetch_count += 1
                await gate.wait()
                return [session]

            async def close(self):
                pass

        async def _launch_all_then_release():
            with patch.object(jellyfin_cache, "get_settings", return_value=_make_jellyfin_enabled_settings()), \
                 patch.object(jellyfin_cache, "JellyfinClient", CountingClient):
                tasks = [
                    asyncio.create_task(jellyfin_cache.get_cached_jellyfin_sessions())
                    for _ in range(10)
                ]
                await asyncio.sleep(0.05)
                gate.set()
                results = await asyncio.gather(*tasks)
                return results

        results = await _launch_all_then_release()

        assert len(results) == 10
        assert all(r == [session] for r in results), results
        assert construct_count == 1, f"expected 1 client construction, got {construct_count}"
        assert fetch_count == 1, f"expected 1 fetch, got {fetch_count}"


# ---------------------------------------------------------------------------
# Extra: reset helper and TTL constant
# ---------------------------------------------------------------------------


class TestModuleInternals:
    """Verify the module-level constants and helpers are correctly exposed."""

    def test_cache_ttl_is_five_seconds(self):
        """TTL must be 5s to match the Dispatcharr poll cadence."""
        assert jellyfin_cache.CACHE_TTL_SECONDS == 5.0

    def test_cache_key_is_namespaced(self):
        """Cache key must include 'jellyfin' namespace."""
        assert "jellyfin" in jellyfin_cache.CACHE_KEY

    def test_reset_for_tests_clears_entry(self):
        """_reset_for_tests clears the cached entry."""
        jellyfin_cache._store_entry([_make_session()])
        assert jellyfin_cache._cached_entry is not None
        jellyfin_cache._reset_for_tests()
        assert jellyfin_cache._cached_entry is None

    def test_reset_for_tests_clears_lock(self):
        """_reset_for_tests clears the fetch lock."""
        jellyfin_cache._get_lock()  # constructs the lock
        assert jellyfin_cache._fetch_lock is not None
        jellyfin_cache._reset_for_tests()
        assert jellyfin_cache._fetch_lock is None
