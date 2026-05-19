"""Plex EPG-airings cache (bd-ma6r3).

Sibling to :mod:`services.plex_cache`. The Plex Live TV resolver tier
(see :mod:`services.plex_resolver`) cross-references a session's
``grandparentTitle`` against the current EPG airing on each
configured DVR section to recover the channel ``channelCallSign`` —
the field Plex's ``/status/sessions`` does NOT carry for Live TV but
which IS the operator-visible channel name (e.g.
``"8.1 | NBC: KGW Portland"``).

Why a sibling cache, not an extension of :mod:`services.plex_cache`:

* **Different TTL.** The session cache TTL (5 s) tracks the
  Dispatcharr poll cadence so per-poll Plex attribution stays fresh.
  EPG airings are 30+ minutes long; refreshing them every 5 s would
  hammer the operator's Plex server with a ~3 MB Shows-section payload
  for zero practical benefit. 30 s is the bead's recommendation and
  trades off (a) once-per-half-minute upstream cost (low) against (b)
  worst-case 30 s lag between a viewer changing channel and ECM
  picking up the new ``channelCallSign`` (acceptable — that's still an
  order of magnitude faster than the EPG entries themselves change).
* **Different fetch shape.** The session cache fires one
  ``GET /status/sessions`` per refresh. The EPG cache fires one fetch
  per DVR section (typically 4) and walks them sequentially.
  Co-housing both behind one ``get_cached_plex_sessions()`` entry
  point would conflate two fundamentally different upstream contracts.
* **Different idle-state behavior.** :mod:`services.plex_cache` is
  unconditionally consulted by the resolver hot path — it's cheap and
  cached. The EPG cache populates ONLY when the resolver has unmatched
  live sessions to disambiguate, gated by
  :func:`maybe_get_cached_plex_epg`. Bundling this gate inside the
  session-cache would muddy that cache's "always queryable" contract.

The bead's "rate-limit short-circuits when no Plex live sessions
exist" acceptance criterion is enforced at the resolver layer
(it calls :func:`maybe_get_cached_plex_epg` only when it has a live
session that earlier tiers couldn't match). This module's responsibility
is the TTL + thundering-herd + stale-fallback envelope; the
gate-on-demand discipline lives in the resolver.

Three deliberate behaviors mirror :mod:`services.plex_cache`:

* **Stale-fallback.** Upstream failure after a successful prior fetch
  returns the stale entry rather than ``[]`` so a momentary Plex blip
  does not erase the EPG cross-reference picture mid-resolver.
* **Thundering-herd guard.** An ``asyncio.Lock`` collapses concurrent
  cache-misses into exactly one upstream sweep.
* **Settings gate.** Plex disabled / unconfigured → ``[]`` immediately,
  no cache access, no client construction.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from config import get_settings
from plex_client import PlexClient, PlexClientError, PlexEpgEntry
import observability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


# bd-ma6r3: single cache key (same single-upstream design as
# :mod:`services.plex_cache`). The cached value is the FULL union of
# current Live TV airings across all DVRs / sections — partial returns
# from a single section are not useful to the resolver.
CACHE_KEY = "plex_epg:current_live"

# Time-to-live in seconds. EPG airings change on broadcast boundaries
# (typically 30+ minutes). A 30 s TTL means:
#
#   * Worst-case lag between a viewer changing channel and ECM picking
#     up the new ``channelCallSign`` is bounded at TTL.
#   * Steady-state upstream cost is one DVR/section sweep per 30 s
#     (the bead's probe measured ~0.43 s for PO's 4-section setup).
#   * Idle Plex deployments incur ZERO cost — the resolver's
#     gate-on-demand discipline never invokes this cache when there
#     are no unmatched live sessions to disambiguate.
CACHE_TTL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    """Cached EPG snapshot with the wall-clock time it was fetched.

    Kept module-private — callers should not introspect cache internals.
    """

    entries: list[PlexEpgEntry]
    cached_at: float


# Single-slot cache (same shape as :mod:`services.plex_cache`).
_cached_entry: _Entry | None = None

# Thundering-herd guard — constructed lazily for the same event-loop
# binding reasons as :mod:`services.plex_cache`.
_fetch_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return the module-level fetch lock, constructing it on first use.

    See :func:`services.plex_cache._get_lock` for the lazy-binding
    rationale (pytest-asyncio per-function event loop isolation).
    """
    global _fetch_lock
    if _fetch_lock is None:
        _fetch_lock = asyncio.Lock()
    return _fetch_lock


def _reset_for_tests() -> None:
    """Clear the module-level cache and lock — tests only.

    Tests share the same module instance across functions; without this
    reset, a test that populates the EPG cache would leak into the next
    and produce false cache-hit assertions.
    """
    global _cached_entry, _fetch_lock
    _cached_entry = None
    _fetch_lock = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def maybe_get_cached_plex_epg() -> list[PlexEpgEntry]:
    """Return the current Live TV EPG snapshot, served from cache when fresh.

    Behavior matrix (same shape as
    :func:`services.plex_cache.get_cached_plex_sessions`):

    * **Plex disabled** (``settings.plex_enabled`` False OR
      ``settings.plex_base_url`` empty) → return ``[]`` immediately;
      no cache access, no client construction.
    * **Cache hit** (entry exists AND age < TTL) → return the cached
      entries; no upstream call.
    * **Cache miss** (no entry OR entry expired) → acquire fetch lock,
      re-check under lock, call
      :meth:`PlexClient.get_current_live_epg_channels`, store and
      return.
    * **Cache miss + fetch failure with prior cache** → log WARN and
      return the stale cached entries rather than the empty list.
    * **Cache miss + fetch failure + no prior cache** → log WARN and
      return ``[]`` so callers always receive a list.

    Always returns a list — never raises. Upstream errors are absorbed
    and logged so the resolver's poll loop can continue under all
    failure modes.

    bd-ma6r3 metric note: the existing
    ``media_session_cache_hits_total`` / ``_fetch_duration_seconds`` /
    ``_fetch_errors_total`` / ``_stale_fallback_total`` family is reused
    here with the new ``source`` label value ``"plex_epg"``. This keeps
    the cache-layer observability symmetry across the three sources +
    new EPG surface (one dashboard, one alert family). The label enum
    descriptive text in :mod:`observability` is extended to mention
    ``plex_epg`` alongside the existing ``emby / plex / jellyfin``.
    """
    settings = get_settings()

    plex_enabled = getattr(settings, "plex_enabled", False)
    plex_base_url = getattr(settings, "plex_base_url", "") or ""
    plex_token = getattr(settings, "plex_token", "") or ""

    if not plex_enabled or not plex_base_url:
        logger.debug("[PLEX-EPG] Plex disabled or unconfigured; returning []")
        return []

    # Fast-path: cache hit without lock acquisition.
    entry = _cached_entry
    if entry is not None:
        age = time.monotonic() - entry.cached_at
        if age < CACHE_TTL_SECONDS:
            logger.debug(
                "[PLEX-EPG] Cache hit for %s (age=%.2fs, ttl=%.1fs)",
                CACHE_KEY, age, CACHE_TTL_SECONDS,
            )
            observability.get_metric(
                "media_session_cache_hits_total",
            ).labels(source="plex_epg").inc()
            return entry.entries

    # Cache miss — acquire lock and double-check.
    lock = _get_lock()
    async with lock:
        entry = _cached_entry
        if entry is not None:
            age = time.monotonic() - entry.cached_at
            if age < CACHE_TTL_SECONDS:
                logger.debug(
                    "[PLEX-EPG] Cache hit under lock for %s "
                    "(age=%.2fs); another waiter populated it",
                    CACHE_KEY,
                )
                observability.get_metric(
                    "media_session_cache_hits_total",
                ).labels(source="plex_epg").inc()
                return entry.entries

        # Holder of the lock — do the upstream sweep.
        client = PlexClient(base_url=plex_base_url, api_key=plex_token)
        try:
            try:
                with observability.get_metric(
                    "media_session_fetch_duration_seconds",
                ).labels(source="plex_epg").time():
                    entries = await client.get_current_live_epg_channels()
            finally:
                # Always release the httpx connection pool, even on
                # error. Without this every failure leaks a socket
                # pool for the lifetime of the process.
                await client.close()
        except PlexClientError as exc:
            observability.get_metric(
                "media_session_fetch_errors_total",
            ).labels(source="plex_epg").inc()
            if _cached_entry is not None:
                logger.warning(
                    "[PLEX-EPG] Fetch failed (%s); returning stale "
                    "cached EPG entries (age=%.2fs)",
                    exc,
                    time.monotonic() - _cached_entry.cached_at,
                )
                observability.get_metric(
                    "media_session_stale_fallback_total",
                ).labels(source="plex_epg").inc()
                return _cached_entry.entries
            logger.warning(
                "[PLEX-EPG] Fetch failed with no prior cache (%s); "
                "returning empty list",
                exc,
            )
            return []

        _store_entry(entries)
        logger.debug(
            "[PLEX-EPG] Cache miss for %s; fetched %d current EPG "
            "airing(s) and stored",
            CACHE_KEY, len(entries),
        )
        return entries


def _store_entry(entries: list[PlexEpgEntry]) -> None:
    """Replace the module-level cache entry with a fresh fetch result.

    Pulled out for symmetry with :func:`services.plex_cache._store_entry`.
    """
    global _cached_entry
    _cached_entry = _Entry(entries=entries, cached_at=time.monotonic())
