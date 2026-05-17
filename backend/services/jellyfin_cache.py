"""Jellyfin session cache (bd-r5f0c.3, epic bd-r5f0c).

Thin async wrapper around :class:`jellyfin_client.JellyfinClient.get_sessions`
that satisfies one specific operational requirement: the
:mod:`bandwidth_tracker` polls Dispatcharr every ~5s and (once W4 lands the
Jellyfin-attribution enrichment) needs the live Jellyfin session list on the
same cadence. Hitting Jellyfin's ``/Sessions`` endpoint on every poll would
both pound the operator's Jellyfin server with redundant traffic AND amplify
the user-visible latency of every poll cycle by a network round-trip.

This module solves that with a small in-memory TTL cache keyed on a single
constant (``"jellyfin:sessions"``) and a 5-second TTL chosen to match the
Dispatcharr poll cadence — the next poll sees a fresh value, the intermediate
polls all hit cache.

Three deliberate departures from the generic :mod:`cache` helper (mirroring
the design of :mod:`services.emby_cache`):

* **Stale-fallback.** The generic cache returns ``None`` once the TTL
  expires. Here, if the upstream fetch fails AFTER a value was previously
  cached, we return the stale value rather than empty list — a momentary
  Jellyfin outage should not erase the operator's "who is watching" picture.
  Cold-start failure (no cache + fetch fails) still returns empty list so
  callers always get a list, never ``None``.
* **Thundering-herd guard.** Concurrent callers during a cache-miss could
  each independently fire ``JellyfinClient.get_sessions()`` and stampede the
  upstream Jellyfin server. An ``asyncio.Lock`` collapses concurrent misses
  into exactly one upstream call; the other waiters receive the freshly
  cached value once the holder finishes.
* **Settings gate.** When the operator has not configured Jellyfin (the
  ``jellyfin_enabled`` setting is False, or no base URL is set) we return
  ``[]`` immediately without touching the cache or constructing a
  ``JellyfinClient`` — there is no upstream to call, and the empty list is
  the correct "no Jellyfin sessions" answer for downstream resolvers.

This module is intentionally narrow and stateless across imports — the
cache lives in module-level globals so a single process shares one cache
across all callers (BandwidthTracker, resolver, future surfaces).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from config import get_settings
from jellyfin_client import JellyfinClient, JellyfinClientError, JellyfinSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Cache key. Single-string namespace per design — no per-user or
# per-server fan-out (Jellyfin is a single configured upstream per ECM install).
CACHE_KEY = "jellyfin:sessions"

# Time-to-live in seconds. Matches the Dispatcharr poll cadence so the
# BandwidthTracker enrichment path sees a fresh Jellyfin snapshot each cycle
# but intermediate callers (resolver, future MCP tools) hit cache.
CACHE_TTL_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    """Cached Jellyfin session list with the wall-clock time it was fetched.

    Kept module-private — callers should not introspect cache internals.
    """

    sessions: list[JellyfinSession]
    cached_at: float


# Single-slot cache (one key, so a dict would be overkill — keep the
# storage shape obvious). ``None`` means "no value ever cached"; a populated
# entry with ``cached_at`` older than ``CACHE_TTL_SECONDS`` is "stale" and
# eligible for refresh but still returnable on fetch failure.
_cached_entry: _Entry | None = None

# Thundering-herd guard. Constructed lazily on first call so importing
# this module does not bind to the (possibly not-yet-running) event loop.
# A single module-level lock is sufficient because the cache is single-key.
_fetch_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return the module-level fetch lock, constructing it on first use.

    Lazy construction matters because ``asyncio.Lock()`` binds to the
    running event loop on first ``acquire()``. Constructing the lock at
    import time would either bind to a loop that no longer exists (test
    isolation: pytest-asyncio creates a fresh loop per function) or fail
    outright when this module is imported outside a running loop.
    """
    global _fetch_lock
    if _fetch_lock is None:
        _fetch_lock = asyncio.Lock()
    return _fetch_lock


def _reset_for_tests() -> None:
    """Clear the module-level cache and lock — tests only.

    Tests share the same module instance across test functions; without
    this reset, a test that populates the cache would leak that state into
    subsequent tests and produce false cache-hit assertions. Production
    code paths do not call this.
    """
    global _cached_entry, _fetch_lock
    _cached_entry = None
    _fetch_lock = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_cached_jellyfin_sessions() -> list[JellyfinSession]:
    """Return the live Jellyfin session list, served from cache when fresh.

    Behavior matrix:

    * **Jellyfin disabled** (``settings.jellyfin_enabled`` is False OR
      ``settings.jellyfin_base_url`` is empty) → return ``[]`` immediately;
      no cache access, no client construction, no log line beyond the
      DEBUG-level "disabled" trace.
    * **Cache hit** (entry exists AND age < TTL) → return the cached
      sessions; no upstream call.
    * **Cache miss** (no entry OR entry expired) → acquire the fetch
      lock, re-check the cache under the lock (another coroutine may
      have populated it while we waited), then call
      ``JellyfinClient.get_sessions()``, store the result, return.
    * **Cache miss + fetch failure with prior cache** → log a WARN and
      return the stale cached value rather than the empty list. A
      transient Jellyfin blip shouldn't erase the "who is watching"
      picture mid-resolver.
    * **Cache miss + fetch failure + no prior cache** (cold-start
      failure) → log a WARN and return ``[]`` so callers always receive
      a list.

    Always returns a list — never raises. Upstream errors are absorbed
    and logged so the BandwidthTracker poll loop can continue under all
    failure modes.
    """
    settings = get_settings()

    # Settings gate — Jellyfin disabled or unconfigured short-circuits
    # before any cache or client interaction. Use ``getattr`` defensively
    # because the ``jellyfin_*`` settings fields are owned by W4 which may
    # not be on the ``dev`` tip at the moment this module ships. Once W4
    # lands the ``DispatcharrSettings`` model will expose these as real
    # fields and the ``getattr`` defaults will become inert defensive code.
    jellyfin_enabled = getattr(settings, "jellyfin_enabled", False)
    jellyfin_base_url = getattr(settings, "jellyfin_base_url", "") or ""
    jellyfin_api_key = getattr(settings, "jellyfin_api_key", "") or ""

    if not jellyfin_enabled or not jellyfin_base_url:
        logger.debug("[JELLYFIN] Jellyfin disabled or unconfigured; returning []")
        return []

    # Fast-path: cache hit without lock acquisition. ``time.monotonic`` is
    # used over ``time.time`` because we only care about elapsed time
    # (TTL comparison) and monotonic is wall-clock-jump safe.
    entry = _cached_entry
    if entry is not None:
        age = time.monotonic() - entry.cached_at
        if age < CACHE_TTL_SECONDS:
            logger.debug(
                "[JELLYFIN] Cache hit for %s (age=%.2fs, ttl=%.1fs)",
                CACHE_KEY, age, CACHE_TTL_SECONDS,
            )
            return entry.sessions

    # Cache miss path. Acquire the lock so concurrent misses collapse to
    # one upstream call. The double-checked locking pattern below is
    # essential — without the re-check inside the lock, every waiter
    # that arrived during the miss would still fire its own fetch once
    # it got the lock, defeating the thundering-herd guard entirely.
    lock = _get_lock()
    async with lock:
        # Re-check under lock. The first waiter to acquire the lock does
        # the fetch; subsequent waiters see the just-populated cache and
        # return without firing their own upstream call.
        entry = _cached_entry
        if entry is not None:
            age = time.monotonic() - entry.cached_at
            if age < CACHE_TTL_SECONDS:
                logger.debug(
                    "[JELLYFIN] Cache hit under lock for %s (age=%.2fs); "
                    "another waiter populated it",
                    CACHE_KEY,
                )
                return entry.sessions

        # Holder of the lock — do the upstream fetch.
        client = JellyfinClient(base_url=jellyfin_base_url, api_key=jellyfin_api_key)
        try:
            try:
                sessions = await client.get_sessions()
            finally:
                # Always release the httpx connection pool even on
                # error. Without this every failure leaks a socket pool
                # for the lifetime of the process.
                await client.close()
        except JellyfinClientError as exc:
            # Stale-fallback decision branches on whether a prior cache
            # value exists.
            if _cached_entry is not None:
                logger.warning(
                    "[JELLYFIN] Fetch failed (%s); returning stale cached "
                    "sessions (age=%.2fs)",
                    exc,
                    time.monotonic() - _cached_entry.cached_at,
                )
                return _cached_entry.sessions
            logger.warning(
                "[JELLYFIN] Fetch failed with no prior cache (%s); "
                "returning empty list",
                exc,
            )
            return []

        # Success — populate the cache and return.
        _store_entry(sessions)
        logger.debug(
            "[JELLYFIN] Cache miss for %s; fetched %d sessions and stored",
            CACHE_KEY, len(sessions),
        )
        return sessions


def _store_entry(sessions: list[JellyfinSession]) -> None:
    """Replace the module-level cache entry with a fresh fetch result.

    Pulled out so the test ``_reset_for_tests`` helper can share the
    same "globals are write-protected behind one assignment" discipline.
    """
    global _cached_entry
    _cached_entry = _Entry(sessions=sessions, cached_at=time.monotonic())
