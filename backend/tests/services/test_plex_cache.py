"""Unit tests for :mod:`services.plex_cache` (bd-r5f0c.2, epic bd-r5f0c).

The cache module sits between :mod:`plex_client` and any downstream
consumer (BandwidthTracker, resolver). The tests below lock the six
behavioral guarantees the bead enumerates:

1. Cache hit — second call within TTL does not call PlexClient.
2. Cache miss — first call calls PlexClient; subsequent within TTL
   returns the same cached list without firing again.
3. Stale fallback — populated cache + subsequent fetch failure returns
   the stale list, not empty.
4. Cold-start failure — empty cache + fetch failure returns empty list
   and logs a WARN.
5. Plex disabled — settings gate short-circuits before any cache or
   client interaction.
6. Thundering-herd — concurrent cache-miss callers result in exactly
   one upstream call thanks to the ``asyncio.Lock``.

All tests reset the module-level cache via ``_reset_for_tests`` so state
does not bleed between tests in the same process.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import observability
from plex_client import PlexClientError, PlexSession
from services import plex_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(user_name: str = "alice") -> PlexSession:
    """Build a representative :class:`PlexSession` for assertions."""
    return PlexSession(
        session_id=f"sess-{user_name}",
        user_id=f"uid-{user_name}",
        user_name=user_name,
        remote_endpoint="10.0.0.5",
        now_playing_item_name="408 | ESPN",
        last_activity_date=datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
    )


def _enabled_settings(base_url: str = "http://plex.example:32400") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Plex."""
    settings = MagicMock()
    settings.plex_enabled = True
    settings.plex_base_url = base_url
    settings.plex_token = "token-123"
    return settings


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Ensure each test starts with a clean module-level cache.

    Without this, a test that populates the cache would leak that state
    into the next test and produce false cache-hit assertions.
    """
    plex_cache._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    plex_cache._reset_for_tests()
    observability.reset_for_tests()


def _get_counter_value(metric_key: str, source: str) -> float:
    """Return the current value of a labeled counter from the live registry.

    ``metric_key`` is the key in observability._METRICS (e.g.
    ``"media_session_cache_hits_total"``). The prometheus metric name is
    ``"ecm_" + metric_key``.
    """
    metric = observability.get_metric(metric_key)
    prom_name = f"ecm_{metric_key}"
    for mf in metric.collect():
        for sample in mf.samples:
            if sample.name == prom_name and sample.labels.get("source") == source:
                return sample.value
    return 0.0


def _get_histogram_count(metric_key: str, source: str) -> float:
    """Return the _count value of a labeled histogram from the live registry.

    ``metric_key`` is the key in observability._METRICS (e.g.
    ``"media_session_fetch_duration_seconds"``).
    """
    metric = observability.get_metric(metric_key)
    prom_count_name = f"ecm_{metric_key}_count"
    for mf in metric.collect():
        for sample in mf.samples:
            if sample.name == prom_count_name and sample.labels.get("source") == source:
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Behavior 1 + 2: cache hit / cache miss
# ---------------------------------------------------------------------------


class TestCacheHitAndMiss:
    """First call fetches; subsequent calls within the TTL window do not."""

    async def test_first_call_invokes_plex_client_once(self):
        """Cache miss path constructs PlexClient and calls get_sessions."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client) as client_cls:
            result = await plex_cache.get_cached_plex_sessions()

        assert result == [session]
        assert client_cls.call_count == 1
        mock_client.get_sessions.assert_awaited_once()
        # The client pool must always be closed, including on success.
        mock_client.close.assert_awaited_once()

    async def test_second_call_within_ttl_hits_cache(self):
        """Within TTL, a second call returns cached data without refetching."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client) as client_cls:
            first = await plex_cache.get_cached_plex_sessions()
            second = await plex_cache.get_cached_plex_sessions()

        assert first == [session]
        assert second == [session]
        # Exactly one client constructed and one fetch fired across two calls.
        assert client_cls.call_count == 1
        mock_client.get_sessions.assert_awaited_once()
        # The second call was a cache hit — counter must be 1.
        assert _get_counter_value("media_session_cache_hits_total", "plex") == 1.0

    async def test_first_call_records_fetch_duration(self):
        """Cache miss path records exactly one histogram observation."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client):
            await plex_cache.get_cached_plex_sessions()

        assert _get_histogram_count("media_session_fetch_duration_seconds", "plex") >= 1.0

    async def test_call_after_ttl_expiry_refetches(self):
        """After the TTL elapses, the next call hits Plex again.

        We monkey-patch ``time.monotonic`` to jump forward past the TTL
        boundary, which is the same outcome the production code observes.
        """
        session_v1 = _make_session("alice")
        session_v2 = _make_session("bob")
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=[[session_v1], [session_v2]])
        mock_client.close = AsyncMock()

        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client), \
             patch.object(plex_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await plex_cache.get_cached_plex_sessions()
            clock[0] += plex_cache.CACHE_TTL_SECONDS + 0.1
            second = await plex_cache.get_cached_plex_sessions()

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
            side_effect=[[session], PlexClientError("network down")]
        )
        mock_client.close = AsyncMock()

        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client), \
             patch.object(plex_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await plex_cache.get_cached_plex_sessions()
            clock[0] += plex_cache.CACHE_TTL_SECONDS + 0.1
            with caplog.at_level(logging.WARNING, logger=plex_cache.logger.name):
                second = await plex_cache.get_cached_plex_sessions()

        assert first == [session]
        # Stale-fallback: same list contents, not an empty list.
        assert second == [session]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stale" in r.getMessage().lower() for r in warnings), warnings
        # Metric assertions: fetch error and stale fallback both incremented.
        assert _get_counter_value("media_session_fetch_errors_total", "plex") == 1.0
        assert _get_counter_value("media_session_stale_fallback_total", "plex") == 1.0


# ---------------------------------------------------------------------------
# Behavior 4: cold-start fetch failure
# ---------------------------------------------------------------------------


class TestColdStartFailure:
    """No prior cache + fetch failure returns empty list + WARN."""

    async def test_cold_start_failure_returns_empty_list(self, caplog):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=PlexClientError("connect refused")
        )
        mock_client.close = AsyncMock()

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client):
            with caplog.at_level(logging.WARNING, logger=plex_cache.logger.name):
                result = await plex_cache.get_cached_plex_sessions()

        assert result == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("no prior cache" in r.getMessage().lower() for r in warnings), warnings
        # Metric assertion: fetch error incremented; stale fallback NOT (no prior cache).
        assert _get_counter_value("media_session_fetch_errors_total", "plex") == 1.0
        assert _get_counter_value("media_session_stale_fallback_total", "plex") == 0.0

    async def test_cold_start_failure_does_not_populate_cache(self):
        """A failed cold-start fetch must not poison the cache with [].

        Otherwise the next call would silently cache-hit empty list for
        the TTL window, masking a recovered Plex for up to 5s.
        """
        mock_client = AsyncMock()
        session = _make_session()
        mock_client.get_sessions = AsyncMock(
            side_effect=[PlexClientError("boom"), [session]]
        )
        mock_client.close = AsyncMock()

        with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(plex_cache, "PlexClient", return_value=mock_client):
            first = await plex_cache.get_cached_plex_sessions()
            # Second call should fire a new fetch because the cold-start
            # failure left the cache empty.
            second = await plex_cache.get_cached_plex_sessions()

        assert first == []
        assert second == [session]
        assert mock_client.get_sessions.await_count == 2


# ---------------------------------------------------------------------------
# Behavior 5: settings gate
# ---------------------------------------------------------------------------


class TestSettingsGate:
    """When Plex is disabled or unconfigured, no cache or client interaction."""

    async def test_plex_disabled_returns_empty_without_client(self):
        settings = _enabled_settings()
        settings.plex_enabled = False

        with patch.object(plex_cache, "get_settings", return_value=settings), \
             patch.object(plex_cache, "PlexClient") as client_cls:
            result = await plex_cache.get_cached_plex_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_empty_base_url_treated_as_disabled(self):
        """An empty base_url means "Plex not configured" — same short-circuit."""
        settings = _enabled_settings()
        settings.plex_base_url = ""

        with patch.object(plex_cache, "get_settings", return_value=settings), \
             patch.object(plex_cache, "PlexClient") as client_cls:
            result = await plex_cache.get_cached_plex_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_missing_plex_attributes_treated_as_disabled(self):
        """Settings model without plex_* fields (pre-W4) → disabled.

        Defends the current state of ``dev``: ``DispatcharrSettings`` may
        not yet declare ``plex_enabled`` / ``plex_base_url`` / ``plex_token``
        at the moment this module ships. The ``getattr`` default in the
        cache function should treat that as "Plex disabled".
        """
        class BareSettings:
            pass

        with patch.object(plex_cache, "get_settings", return_value=BareSettings()), \
             patch.object(plex_cache, "PlexClient") as client_cls:
            result = await plex_cache.get_cached_plex_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_disabled_does_not_touch_cache(self):
        """Disabled short-circuit returns [] even when a stale cache exists.

        If the operator disables Plex after a cache was populated, the
        disabled response must win — we should not surface stale Plex
        data after the operator explicitly turned the feature off.
        """
        # Seed the cache directly via the internal helper.
        plex_cache._store_entry([_make_session("ghost")])

        settings = _enabled_settings()
        settings.plex_enabled = False

        with patch.object(plex_cache, "get_settings", return_value=settings), \
             patch.object(plex_cache, "PlexClient") as client_cls:
            result = await plex_cache.get_cached_plex_sessions()

        assert result == []
        client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 6: thundering-herd prevention
# ---------------------------------------------------------------------------


class TestThunderingHerdPrevention:
    """Concurrent cache-miss callers must collapse to one upstream fetch."""

    async def test_concurrent_misses_share_a_single_fetch(self):
        """10 concurrent calls during cold-start fire PlexClient exactly once.

        The lock + double-checked re-read inside the lock is what makes
        this work — without the re-check, every waiter would still fire
        a fetch after acquiring the lock, defeating the guard.
        """
        session = _make_session()
        gate = asyncio.Event()
        construct_count = 0
        fetch_count = 0

        class CountingClient:
            """Minimal stand-in for PlexClient that counts constructions and fetches."""

            def __init__(self, *, base_url: str, api_key: str):
                nonlocal construct_count
                construct_count += 1

            async def get_sessions(self):
                nonlocal fetch_count
                fetch_count += 1
                await gate.wait()
                return [session]

            async def close(self):
                pass

        async def _launch_all_then_release():
            with patch.object(plex_cache, "get_settings", return_value=_enabled_settings()), \
                 patch.object(plex_cache, "PlexClient", CountingClient):
                tasks = [
                    asyncio.create_task(plex_cache.get_cached_plex_sessions())
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
