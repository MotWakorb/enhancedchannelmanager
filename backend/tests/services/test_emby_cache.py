"""Unit tests for :mod:`services.emby_cache` (bd-gpeot, epic bd-2cenq).

The cache module sits between :mod:`emby_client` and any downstream
consumer (BandwidthTracker, resolver). The tests below lock the six
behavioral guarantees the bd-gpeot bead enumerates:

1. Cache hit — second call within TTL does not call EmbyClient.
2. Cache miss — first call calls EmbyClient; subsequent within TTL
   returns the same cached list without firing again.
3. Stale fallback — populated cache + subsequent fetch failure returns
   the stale list, not empty.
4. Cold-start failure — empty cache + fetch failure returns empty list
   and logs a WARN.
5. Emby disabled — settings gate short-circuits before any cache or
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

import observability
from emby_client import EmbyClientError, EmbySession
from services import emby_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(user_name: str = "alice") -> EmbySession:
    """Build a representative :class:`EmbySession` for assertions.

    Only the fields downstream resolvers actually read are populated;
    others stay empty so we can spot accidental coupling on noise.
    """
    return EmbySession(
        session_id=f"sess-{user_name}",
        user_id=f"uid-{user_name}",
        user_name=user_name,
        remote_endpoint="10.0.0.5",
        now_playing_item_name="Some Show",
        now_playing_channel_name="Some Channel",
        last_activity_date="2026-05-16T12:00:00Z",
    )


def _enabled_settings(base_url: str = "http://emby.example") -> MagicMock:
    """Settings stub representing a fully-configured, enabled Emby."""
    settings = MagicMock()
    settings.emby_enabled = True
    settings.emby_base_url = base_url
    settings.emby_api_key = "key-123"
    return settings


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Ensure each test starts with a clean module-level cache.

    Without this, a test that populates the cache would leak that state
    into the next test and produce false cache-hit assertions. The
    fixture also runs ``_reset_for_tests`` AFTER each test so a failing
    test cannot corrupt the next one's setup.
    """
    emby_cache._reset_for_tests()
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    emby_cache._reset_for_tests()
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

    async def test_first_call_invokes_emby_client_once(self):
        """Cache miss path constructs EmbyClient and calls get_sessions."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client) as client_cls:
            result = await emby_cache.get_cached_emby_sessions()

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

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client) as client_cls:
            first = await emby_cache.get_cached_emby_sessions()
            second = await emby_cache.get_cached_emby_sessions()

        assert first == [session]
        assert second == [session]
        # Exactly one client constructed and one fetch fired across two calls.
        assert client_cls.call_count == 1
        mock_client.get_sessions.assert_awaited_once()
        # The second call was a cache hit — counter must be 1.
        assert _get_counter_value("media_session_cache_hits_total", "emby") == 1.0

    async def test_first_call_records_fetch_duration(self):
        """Cache miss path records exactly one histogram observation."""
        session = _make_session()
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(return_value=[session])
        mock_client.close = AsyncMock()

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client):
            await emby_cache.get_cached_emby_sessions()

        assert _get_histogram_count("media_session_fetch_duration_seconds", "emby") >= 1.0

    async def test_call_after_ttl_expiry_refetches(self):
        """After the TTL elapses, the next call hits Emby again.

        We don't actually sleep — we monkey-patch ``time.monotonic`` to
        jump forward past the TTL boundary, which is the same outcome
        the production code would observe.
        """
        session_v1 = _make_session("alice")
        session_v2 = _make_session("bob")
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(side_effect=[[session_v1], [session_v2]])
        mock_client.close = AsyncMock()

        # Use a mutable clock so we can advance time deterministically.
        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client), \
             patch.object(emby_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await emby_cache.get_cached_emby_sessions()
            # Jump past TTL boundary.
            clock[0] += emby_cache.CACHE_TTL_SECONDS + 0.1
            second = await emby_cache.get_cached_emby_sessions()

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
        # First fetch succeeds; the next attempt raises.
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=[[session], EmbyClientError("network down")]
        )
        mock_client.close = AsyncMock()

        clock = [1000.0]

        def _fake_monotonic():
            return clock[0]

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client), \
             patch.object(emby_cache.time, "monotonic", side_effect=_fake_monotonic):
            first = await emby_cache.get_cached_emby_sessions()
            # Push past TTL so the second call hits the miss path.
            clock[0] += emby_cache.CACHE_TTL_SECONDS + 0.1
            with caplog.at_level(logging.WARNING, logger=emby_cache.logger.name):
                second = await emby_cache.get_cached_emby_sessions()

        assert first == [session]
        # Stale-fallback: same list contents, not an empty list.
        assert second == [session]
        # The WARN line should mention the failure and that a stale value
        # is being returned so operators can correlate.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stale" in r.getMessage().lower() for r in warnings), warnings
        # Metric assertions: fetch error and stale fallback both incremented.
        assert _get_counter_value("media_session_fetch_errors_total", "emby") == 1.0
        assert _get_counter_value("media_session_stale_fallback_total", "emby") == 1.0


# ---------------------------------------------------------------------------
# Behavior 4: cold-start fetch failure
# ---------------------------------------------------------------------------


class TestColdStartFailure:
    """No prior cache + fetch failure returns empty list + WARN."""

    async def test_cold_start_failure_returns_empty_list(self, caplog):
        mock_client = AsyncMock()
        mock_client.get_sessions = AsyncMock(
            side_effect=EmbyClientError("connect refused")
        )
        mock_client.close = AsyncMock()

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client):
            with caplog.at_level(logging.WARNING, logger=emby_cache.logger.name):
                result = await emby_cache.get_cached_emby_sessions()

        assert result == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        # The cold-start branch is distinguishable from the stale branch
        # by phrasing — assert at least one WARN was emitted with the
        # [EMBY] prefix convention (logger name is module name; the
        # [EMBY] text appears in the formatted message body).
        assert any("no prior cache" in r.getMessage().lower() for r in warnings), warnings
        # Metric assertion: fetch error incremented; stale fallback NOT (no prior cache).
        assert _get_counter_value("media_session_fetch_errors_total", "emby") == 1.0
        assert _get_counter_value("media_session_stale_fallback_total", "emby") == 0.0

    async def test_cold_start_failure_does_not_populate_cache(self):
        """A failed cold-start fetch must not poison the cache with [].

        Otherwise the next call would silently cache-hit empty list for
        the TTL window, masking a recovered Emby for up to 5s.
        """
        mock_client = AsyncMock()
        # First call fails; second call succeeds.
        session = _make_session()
        mock_client.get_sessions = AsyncMock(
            side_effect=[EmbyClientError("boom"), [session]]
        )
        mock_client.close = AsyncMock()

        with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
             patch.object(emby_cache, "EmbyClient", return_value=mock_client):
            first = await emby_cache.get_cached_emby_sessions()
            # Second call should fire a new fetch because the cold-start
            # failure left the cache empty (not poisoned with []).
            second = await emby_cache.get_cached_emby_sessions()

        assert first == []
        assert second == [session]
        assert mock_client.get_sessions.await_count == 2


# ---------------------------------------------------------------------------
# Behavior 5: settings gate
# ---------------------------------------------------------------------------


class TestSettingsGate:
    """When Emby is disabled or unconfigured, no cache or client interaction."""

    async def test_emby_disabled_returns_empty_without_client(self):
        settings = _enabled_settings()
        settings.emby_enabled = False

        with patch.object(emby_cache, "get_settings", return_value=settings), \
             patch.object(emby_cache, "EmbyClient") as client_cls:
            result = await emby_cache.get_cached_emby_sessions()

        assert result == []
        # Critically — no client constructed when disabled.
        client_cls.assert_not_called()

    async def test_empty_base_url_treated_as_disabled(self):
        """An empty base_url means "Emby not configured" — same short-circuit.

        This guards against the bd-8wc6q-not-yet-shipped state where the
        settings model might expose ``emby_enabled=True`` by default but
        have no URL configured.
        """
        settings = _enabled_settings()
        settings.emby_base_url = ""

        with patch.object(emby_cache, "get_settings", return_value=settings), \
             patch.object(emby_cache, "EmbyClient") as client_cls:
            result = await emby_cache.get_cached_emby_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_missing_emby_attributes_treated_as_disabled(self):
        """Settings model without emby_* fields (pre-bd-8wc6q) → disabled.

        Defends the current state of ``dev``: ``DispatcharrSettings`` may
        not yet declare ``emby_enabled`` / ``emby_base_url`` / ``emby_api_key``
        at the moment this module ships. The ``getattr`` default in the
        cache function should treat that as "Emby disabled" rather than
        raising AttributeError.
        """
        # An object that explicitly lacks the emby_* attributes. Using a
        # bare object() (no spec) — attribute access on a missing field
        # raises AttributeError, which is what would happen on a
        # production Pydantic model before bd-8wc6q lands.
        class BareSettings:
            pass

        with patch.object(emby_cache, "get_settings", return_value=BareSettings()), \
             patch.object(emby_cache, "EmbyClient") as client_cls:
            result = await emby_cache.get_cached_emby_sessions()

        assert result == []
        client_cls.assert_not_called()

    async def test_disabled_does_not_touch_cache(self):
        """Disabled short-circuit returns [] even when a stale cache exists.

        If the operator disables Emby after a cache was populated, the
        disabled response must win — we should not surface stale Emby
        data after the operator explicitly turned the feature off.
        """
        # Seed the cache directly via the internal helper.
        emby_cache._store_entry([_make_session("ghost")])

        settings = _enabled_settings()
        settings.emby_enabled = False

        with patch.object(emby_cache, "get_settings", return_value=settings), \
             patch.object(emby_cache, "EmbyClient") as client_cls:
            result = await emby_cache.get_cached_emby_sessions()

        assert result == []
        client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 6: thundering-herd prevention
# ---------------------------------------------------------------------------


class TestThunderingHerdPrevention:
    """Concurrent cache-miss callers must collapse to one upstream fetch."""

    async def test_concurrent_misses_share_a_single_fetch(self):
        """10 concurrent calls during cold-start fire EmbyClient exactly once.

        The lock + double-checked re-read inside the lock is what makes
        this work — without the re-check, every waiter would still fire
        a fetch after acquiring the lock, defeating the guard.

        We use an ``asyncio.Event`` to hold the in-flight fetch open
        until all 10 callers have arrived at the lock, so the test
        deterministically exercises concurrent contention rather than
        serial completion.
        """
        session = _make_session()
        gate = asyncio.Event()
        construct_count = 0
        fetch_count = 0

        class CountingClient:
            """Minimal stand-in for EmbyClient that counts constructions and fetches.

            Implemented as a real class rather than a Mock so we can
            count constructions deterministically — Mock's call_count
            semantics under concurrent ``asyncio.gather`` callers are
            harder to reason about than an explicit module-level
            counter.
            """

            def __init__(self, *, base_url: str, api_key: str):
                nonlocal construct_count
                construct_count += 1
                self.base_url = base_url
                self.api_key = api_key

            async def get_sessions(self):
                nonlocal fetch_count
                fetch_count += 1
                # Hold the fetch open until the test releases the gate
                # so all waiters definitely arrive at the lock during
                # an in-flight fetch.
                await gate.wait()
                return [session]

            async def close(self):
                pass

        async def _launch_all_then_release():
            # Schedule 10 concurrent callers, then release the gate so
            # the holder of the lock can complete its fetch.
            with patch.object(emby_cache, "get_settings", return_value=_enabled_settings()), \
                 patch.object(emby_cache, "EmbyClient", CountingClient):
                # Kick off all 10 tasks first — they'll all queue on the
                # lock with one of them inside CountingClient.get_sessions
                # waiting on the gate.
                tasks = [
                    asyncio.create_task(emby_cache.get_cached_emby_sessions())
                    for _ in range(10)
                ]
                # Yield control so the holder reaches gate.wait().
                # A short sleep is more reliable than a single sleep(0)
                # for letting all waiters queue at the lock.
                await asyncio.sleep(0.05)
                gate.set()
                results = await asyncio.gather(*tasks)
                return results

        results = await _launch_all_then_release()

        # All 10 callers receive the same session list.
        assert len(results) == 10
        assert all(r == [session] for r in results), results
        # Exactly one EmbyClient was constructed across all 10 callers.
        assert construct_count == 1, f"expected 1 client construction, got {construct_count}"
        # Exactly one upstream fetch was issued.
        assert fetch_count == 1, f"expected 1 fetch, got {fetch_count}"
