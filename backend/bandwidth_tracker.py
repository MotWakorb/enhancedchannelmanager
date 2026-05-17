"""
Background bandwidth tracking service.
Polls Dispatcharr stats periodically and accumulates bandwidth data.

v0.17.0 Stats v2 (bd-skqln.3 step (d)):
``_collect_stats`` writes one row per active viewing connection into
``session_telemetry`` unconditionally. The legacy ``channel_watch_stats``
write inside this module is gone — its readers (popularity calculator,
top-watched API) now derive their inputs from ``session_telemetry`` and
``unique_client_connections``. The transitional
``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` kill-switch is retired; its job
was the (a)→(d) transition gate and there is no off-state once legacy
writes are gone.
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from typing import Any, ClassVar, NamedTuple, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func
from sqlalchemy.exc import IntegrityError, OperationalError

from database import get_session
from models import (
    BandwidthDaily,
    ChannelBandwidth,
    SessionTelemetry,
    UniqueClientConnection,
)
from services.emby_resolver import (
    EmbyAttribution,
    resolve_emby_user,
    resolve_emby_users,
)
from services.jellyfin_resolver import (
    JellyfinAttribution,
    resolve_jellyfin_user,
    resolve_jellyfin_users,
)
from services.plex_resolver import (
    PlexAttribution,
    resolve_plex_user,
    resolve_plex_users,
)

logger = logging.getLogger(__name__)


# bd-gih6d: rate-limit window for the per-source resolver-failure WARN
# lines. The resolvers themselves document that they "never raise" —
# every upstream defensive path returns ``None`` and logs at the
# appropriate level. The per-source guards exist as a belt-and-braces
# wrapper around the resolver call so a future refactor that lets an
# exception escape (or any unforeseen runtime fault: cache failure,
# settings access raise, etc.) cannot block the telemetry write.
# WARN-ONCE-PER-WINDOW keeps the log honest: operators need a visible
# signal when attribution is silently failing, but not one line per
# (channel, ip) pair per poll cycle (which on a busy install would
# drown the log). 60 seconds matches the order-of-magnitude of the
# resolver cache TTL — transient failures self-heal inside one window.
#
# bd-r5f0c.4: per-source clocks (separate ``_emby_*``, ``_plex_*``,
# ``_jellyfin_*`` timestamps) so a sustained Plex outage cannot silence
# Jellyfin failure WARNs — failure isolation across sources is the SRE
# requirement that motivated the multi-resolver fan-out in
# ``_resolve_attributions``.
_EMBY_WARN_WINDOW_SECONDS: float = 60.0
_PLEX_WARN_WINDOW_SECONDS: float = 60.0
_JELLYFIN_WARN_WINDOW_SECONDS: float = 60.0


# Module-level monotonic timestamps of the last per-source resolver
# failure WARN. Initialised to ``None`` (never logged) so the first
# failure in a fresh process always surfaces. Module-level rather than
# instance-level so multi-tracker test seams (rare, but possible in
# parallel test runs) share the same rate-limit clock — duplicating the
# WARN across tracker instances would defeat the suppression.
_emby_resolver_last_warn_at: float | None = None
_plex_resolver_last_warn_at: float | None = None
_jellyfin_resolver_last_warn_at: float | None = None


@dataclass(frozen=True)
class AttributionResult:
    """Sparse per-(channel, ip) attribution from all three media sources.

    bd-r5f0c.4 (epic bd-r5f0c). Populated by
    :meth:`BandwidthTracker._resolve_attributions` for every active
    viewing connection in a poll. Each field is ``None`` (or empty list)
    when the corresponding source did not match this client — typical
    for the common case where the operator has only one media server
    enabled, or the stream is not media-server-mediated at all (direct
    Dispatcharr proxy traffic).

    bd-r5f0c.9 extension — multi-viewer attribution. The original
    bd-r5f0c.4 single-viewer fields (``*_user_id`` / ``*_user_name``)
    are retained for back-compat with Stats v2 aggregations and the
    pre-W5 frontend rendering. Three new fields carry the FULL list of
    viewers per source so multi-viewer scenarios (N upstream users on
    one channel via the same media-server transcoding proxy) are
    captured. The legacy singular slots are populated with position 0
    of the corresponding viewer list (most-recent viewer) so existing
    consumers see exactly the same shape as v0.17.1-0042.

    The Plex resolver currently surfaces only ``user_name`` (no Plex-
    side user UUID is exposed on the public ``/status/sessions``
    surface — Plex Web users are identified by their numeric
    ``User/@id``, which ``plex_resolver`` could surface but currently
    does not). The ``plex_user_id`` slot and the ``user_id`` keys
    inside ``plex_viewers`` dicts stay NULL today — the schema and the
    dataclass tolerate this; the column tolerates NULL via the
    nullable TEXT type.

    All three pairs ride together in one dataclass so the writer can
    look up one ``AttributionResult`` per (channel, ip) instead of
    consulting three separate dicts.
    """

    emby_user_id: Optional[str] = None
    emby_user_name: Optional[str] = None
    plex_user_id: Optional[str] = None
    plex_user_name: Optional[str] = None
    jellyfin_user_id: Optional[str] = None
    jellyfin_user_name: Optional[str] = None
    # bd-r5f0c.9 multi-viewer lists. Each is a list of
    # ``{"user_id": str | None, "user_name": str}`` dicts, sorted
    # ``last_activity_date`` descending so position 0 is the most-recent
    # viewer (the same viewer the legacy *_user_name field carries).
    # Empty list (default) when the corresponding source did not
    # match — semantically identical to "no viewers" / NULL in the DB.
    # ``field(default_factory=list)`` rather than a bare ``[]`` because
    # this dataclass is frozen and a mutable default would be a shared
    # class-level instance across every AttributionResult.
    emby_viewers: list[dict] = field(default_factory=list)
    plex_viewers: list[dict] = field(default_factory=list)
    jellyfin_viewers: list[dict] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when no source matched this client (every field empty/NULL)."""
        return not any(
            (
                self.emby_user_id, self.emby_user_name,
                self.plex_user_id, self.plex_user_name,
                self.jellyfin_user_id, self.jellyfin_user_name,
                self.emby_viewers, self.plex_viewers, self.jellyfin_viewers,
            )
        )


# bd-r5f0c.4: per-source resolver timeout for the asyncio.gather fan-out
# in ``_resolve_attributions``. SRE failure-isolation requirement —
# a slow Plex server cannot block the per-poll telemetry write past
# this threshold. 2.0 seconds is generous relative to the resolver's
# cache-fast-path (which is microseconds), but tight enough that a
# misconfigured server URL or a DNS hang fails forward to NULL
# attribution within one poll interval. The resolvers themselves
# already wrap their HTTP calls in shorter (5s/10s) timeouts; this is
# the outer envelope so the whole-source attempt completes inside one
# poll's budget.
_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS: float = 2.0


# Dispatcharr stream-URL convention: the last path segment before ``.ts`` is
# the integer Dispatcharr stream row id — the same value that ``stream_id``
# would have carried on the ``/proxy/ts/status`` payload had it been
# populated. The resolver falls back to this when ``stream_id`` is missing
# (bd-kbgey — 214 of 235 dev polls observed with stream_id=None on active
# channels). One capture group, defensive against query-string suffixes
# like ``.ts?session=abc123`` so the fallback survives Dispatcharr URL
# annotation conventions that the proxy may add for transcoding hints.
_STREAM_ID_URL_PATTERN = re.compile(r"/(\d+)\.ts(?:\?|$)")


# bd-8axhi defense-in-depth: substrings the SQLite driver puts into the
# ``OperationalError`` message when the live schema is missing a column or
# table that the SQLAlchemy model declares. Used by
# ``_is_schema_drift_error`` to escalate the runtime hot-deploy hazard to
# ERROR-level on first observation. Lowercase tokens — the matcher
# lowercases the candidate before comparing. Both shapes are emitted by
# SQLite as ``no such column: <name>`` / ``no such table: <name>``; pysqlite
# preserves that wording on its way through SQLAlchemy. Postgres / other
# backends would surface a different error class altogether (UndefinedColumn
# in psycopg2), so this is intentionally SQLite-shaped — ECM is SQLite-only
# (see ``database.py`` PRAGMA setup).
_SCHEMA_DRIFT_ERROR_TOKENS = ("no such column", "no such table")


def _is_schema_drift_error(exc: BaseException) -> bool:
    """True if ``exc`` looks like the bd-zaaey/bd-8axhi schema-drift signature.

    Matches the SQLite-driver wording for "model declares X, live DB
    doesn't have it" — the same disease bd-zaaey loud-fails on at boot,
    here detected at write time so an in-process hot-deploy that
    introduces drift after the boot guard has already passed gets a loud
    log line on the first failed write rather than blending into the
    routine WARN noise.

    Walks the exception chain (``__cause__`` and ``__context__``) so a
    SQLAlchemy ``OperationalError`` wrapping a sqlite3 ``OperationalError``
    is detected on either layer.
    """
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        message = str(cur).lower()
        for token in _SCHEMA_DRIFT_ERROR_TOKENS:
            if token in message:
                return True
        cur = cur.__cause__ or cur.__context__
    return False


def _extract_stream_id_from_url(url: Optional[str]) -> Optional[int]:
    """Pull the Dispatcharr stream row id from a stream URL.

    Returns the integer stream id if the URL matches the
    ``.../<id>.ts[?...]`` convention, otherwise ``None``. Defensive
    against malformed input (non-string, empty, no path, no integer
    segment); the caller treats ``None`` the same as a missing
    ``stream_id`` field and falls through to the NULL-write path.

    Validates the captured value parses as a positive int — guards
    against pathological inputs like ``/00000.ts`` mapping to id 0.

    NOTE (bd-5g7kx): the integer this returns is NOT guaranteed to be
    Dispatcharr's stream row id — empirically it is the *upstream M3U
    provider's* stream id (e.g. Infinity's ``85796``), which only
    coincidentally collides with a Dispatcharr id on some providers.
    The kbgey-era hot path (``get_streams_by_ids`` lookup) is preserved
    for the coincidental-collision case; ``_resolve_provider_ids`` then
    falls through to the channel-streams URL-match fallback when the
    direct lookup misses.
    """
    if not url or not isinstance(url, str):
        return None
    match = _STREAM_ID_URL_PATTERN.search(url)
    if match is None:
        return None
    try:
        parsed = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


class ProviderResolution(NamedTuple):
    """Per-channel resolver output (bd-kh23e).

    ``BandwidthTracker._resolve_provider_ids`` returns one
    ``ProviderResolution`` per channel in the snapshot. The three
    fields move together as a unit because they all come from the same
    ``get_streams_by_ids`` batch response and they all fail (NULL)
    together when resolution can't complete (same failure modes as the
    pre-kh23e provider-only path: ``no_stream_id`` /
    ``stream_not_found`` / ``stream_has_no_provider`` / ``lookup_raised``
    / channel-streams fallback raise/miss).

    * ``provider_id`` — the M3U-account id of the stream's upstream
      provider (``streams.m3u_account_id``). NULL when the resolver
      could not identify the stream's owner.
    * ``stream_id`` — the Dispatcharr stream row id (``streams.id``).
      NULL when the resolver could not identify the active stream at
      all (no stream id on the snapshot, no URL-derived match, etc.).
    * ``stream_name`` — the ``name`` field on the Dispatcharr stream
      record (e.g. ``"US: TNT"``). NULL when the stream record had no
      ``name`` field, or when the resolver could not identify the
      stream.

    Zero runtime overhead vs. a 3-tuple — ``typing.NamedTuple`` is a
    plain ``tuple`` subclass. Field access (``.provider_id``) is
    callsite documentation; iteration / equality / hashing behave
    identically to a tuple.

    The all-NULL sentinel is ``ProviderResolution(None, None, None)`` —
    use ``EMPTY_RESOLUTION`` below to avoid re-allocating it.
    """

    provider_id: Optional[int]
    stream_id: Optional[int]
    stream_name: Optional[str]


# Sentinel for the "resolution failed" case — same object reused across
# call sites so the dict[channel_uuid, ProviderResolution] map doesn't
# allocate a fresh tuple for every NULL row.
EMPTY_RESOLUTION = ProviderResolution(None, None, None)


# Default LRU sizing for the cross-poll channel-streams cache. Matches
# the historical instance defaults so behaviour is unchanged when the
# tracker swaps from instance state to an injected cache.
DEFAULT_CHANNEL_STREAMS_CACHE_CAP = 200
DEFAULT_CHANNEL_STREAMS_CACHE_TTL_POLLS = 30


class ChannelStreamsCache:
    """Cross-call cache for the channel-streams URL-match fallback
    (bd-5g7kx). Bounded LRU keyed by channel uuid; TTL anchored to a
    monotonically-increasing ``poll_count``-style integer the caller
    advances each invocation so a stream's failover hop / stream-list
    edit is picked up within ``ttl_polls`` cycles.

    Two callers share this cache shape:

    * ``BandwidthTracker._resolve_provider_ids`` — owns one instance for
      the life of the tracker; the tracker advances ``poll_count`` once
      per polling cycle.
    * ``routers.stats.get_channel_stats`` — the live ``/api/stats/channels``
      enrichment (bd-ox5q8) creates a transient instance per request.
      Per-request scope is deliberate: the endpoint is hit on operator
      UI refresh cadence (10s+), the cache reuse only matters within
      one resolver invocation, and a transient instance avoids
      cross-request state.

    Value shape: ``(poll_count_at_cache_time, streams_list)``. The poll
    count anchors TTL so the cache survives wall-clock jumps that
    ``time.time()``-based TTLs don't.
    """

    def __init__(
        self,
        cap: int = DEFAULT_CHANNEL_STREAMS_CACHE_CAP,
        ttl_polls: int = DEFAULT_CHANNEL_STREAMS_CACHE_TTL_POLLS,
    ) -> None:
        self._entries: "OrderedDict[str, tuple[int, list[dict]]]" = OrderedDict()
        self.cap = cap
        self.ttl_polls = ttl_polls

    def get(self, channel_uuid: str, current_poll: int) -> Optional[list[dict]]:
        """Return the cached stream list if present and within TTL,
        otherwise ``None``. Touch-on-read promotes the entry to MRU.
        """
        entry = self._entries.get(channel_uuid)
        if entry is None:
            return None
        cached_at_poll, streams = entry
        age_polls = current_poll - cached_at_poll
        if age_polls > self.ttl_polls:
            del self._entries[channel_uuid]
            return None
        self._entries.move_to_end(channel_uuid)
        return streams

    def put(
        self, channel_uuid: str, streams: list[dict], current_poll: int
    ) -> None:
        """Insert streams keyed by channel uuid, evicting the LRU
        entry when the cache hits its cap.
        """
        if channel_uuid in self._entries:
            del self._entries[channel_uuid]
        self._entries[channel_uuid] = (current_poll, streams)
        while len(self._entries) > self.cap:
            self._entries.popitem(last=False)

    def __contains__(self, channel_uuid: str) -> bool:
        return channel_uuid in self._entries

    def __len__(self) -> int:
        return len(self._entries)


async def resolve_active_channel_streams(
    client,
    channel_snapshot: list[dict],
    *,
    channel_streams_cache: Optional[ChannelStreamsCache] = None,
    poll_count: int = 0,
    emit_metrics: bool = True,
) -> dict[str, ProviderResolution]:
    """Resolve each active channel's stream identity (id + name + provider).

    Free-function entry point shared by:

    * ``BandwidthTracker._resolve_provider_ids`` — the polling cycle's
      hot path. Passes its instance ``ChannelStreamsCache`` so successive
      polls reuse channel-streams responses (bd-5g7kx) within TTL.
    * ``routers.stats.get_channel_stats`` — the live Stats v2 Active
      Channels endpoint (bd-ox5q8). Passes a fresh cache per request so
      operator-facing data is at most one Dispatcharr round-trip behind
      reality (no cross-request caching of channel-streams lookups —
      operators expect immediate accuracy).

    Snapshot entry shape (the union both callers feed in):

    * ``channel_uuid`` (str, required) — Dispatcharr channel UUID.
    * ``stream_id`` (int | None) — Dispatcharr stream row id from
      ``/proxy/ts/status``. Resolved first; absence triggers the
      URL-derived fallback below.
    * ``url`` (str | None) — Active stream URL. Used for the URL-derived
      stream-id parse (bd-kbgey) and for the channel-streams URL-match
      fallback (bd-5g7kx).

    Returns ``{channel_uuid: ProviderResolution}``. Resolution failures
    land ``EMPTY_RESOLUTION`` (the all-None NamedTuple) — same row still
    surfaces, all three identity fields NULL.

    Resolution paths (tried in order, first hit wins per channel):

    1. **Direct stream_id**: snapshot's ``stream_id`` → batched
       ``get_streams_by_ids`` → stream's ``m3u_account_id``.
    2. **URL-derived stream_id** (bd-kbgey): when ``stream_id`` is
       absent, parse the trailing ``<id>.ts`` integer from the active
       URL and route it through the SAME batched call. Wins when the
       URL's trailing id coincidentally collides with a Dispatcharr
       stream row id.
    3. **Channel-streams URL match** (bd-5g7kx): when path 2 misses —
       the URL's trailing id is the *upstream* M3U provider's id, not
       Dispatcharr's — fetch ``/api/channels/channels/<uuid>/streams/``
       and find the stream whose persisted ``url`` matches the active
       URL. Results cached (when a cache is provided) cross-call in a
       bounded LRU.

    Failure modes — all surface as ``EMPTY_RESOLUTION`` with a
    structured ``[STATS_V2] provider_resolution_failed`` log line.
    See ``BandwidthTracker._resolve_provider_ids`` docstring for the
    full reason taxonomy.

    Metrics: when ``emit_metrics`` is True (the default — the polling
    hot path) a per-call SLI line and Prometheus counter increment are
    emitted via ``_log_provider_resolution_sli``. When False (the
    on-demand endpoint path) the SLI line is suppressed so it doesn't
    drown the cyclic poll signal.
    """
    from stream_prober import extract_m3u_account_id

    provider_by_channel: dict[str, ProviderResolution] = {}
    unresolvable_channels: list[str] = []
    stream_id_by_channel: dict[str, int] = {}
    # Channels whose stream_id came from URL parsing (bd-kbgey fallback)
    # rather than the direct ``stream_id`` field. These are the
    # candidates for the channel-streams URL-match fallback (bd-5g7kx)
    # if the direct lookup misses.
    url_derived_channels: set[str] = set()
    url_by_channel: dict[str, str] = {}
    for entry in channel_snapshot:
        channel_uuid = entry["channel_uuid"]
        stream_id = entry.get("stream_id")
        url = entry.get("url")
        if isinstance(url, str) and url:
            url_by_channel[channel_uuid] = url
        if stream_id is None:
            derived = _extract_stream_id_from_url(url) if url else None
            if derived is None:
                unresolvable_channels.append(channel_uuid)
                provider_by_channel[channel_uuid] = EMPTY_RESOLUTION
                logger.warning(
                    "[STATS_V2] provider_resolution_failed channel=%s reason=no_stream_id",
                    channel_uuid,
                )
                continue
            stream_id_by_channel[channel_uuid] = derived
            url_derived_channels.add(channel_uuid)
            continue
        stream_id_by_channel[channel_uuid] = int(stream_id)

    if not stream_id_by_channel:
        if emit_metrics:
            _log_provider_resolution_sli(0, len(unresolvable_channels))
        return provider_by_channel

    unique_stream_ids = sorted(set(stream_id_by_channel.values()))
    try:
        streams = await client.get_streams_by_ids(unique_stream_ids)
    except Exception as e:
        logger.warning(
            "[STATS_V2] provider_resolution_failed reason=lookup_raised error=%s",
            e,
        )
        for channel_uuid in stream_id_by_channel:
            provider_by_channel[channel_uuid] = EMPTY_RESOLUTION
            logger.warning(
                "[STATS_V2] provider_resolution_failed channel=%s stream=%s reason=lookup_raised",
                channel_uuid,
                stream_id_by_channel[channel_uuid],
            )
        if emit_metrics:
            _log_provider_resolution_sli(
                0, len(unresolvable_channels) + len(stream_id_by_channel)
            )
        return provider_by_channel

    provider_by_stream: dict[int, Optional[int]] = {}
    name_by_stream: dict[int, Optional[str]] = {}
    for stream in streams:
        sid = stream.get("id", stream.get("stream_id"))
        if sid is None:
            continue
        sid_int = int(sid)
        provider_by_stream[sid_int] = extract_m3u_account_id(
            stream.get("m3u_account")
        )
        raw_name = stream.get("name")
        name_by_stream[sid_int] = (
            str(raw_name) if isinstance(raw_name, str) and raw_name else None
        )

    # Per-invocation cache for the channel-streams fallback. Multiple
    # unresolved channels sharing a channel_uuid in one call consult
    # Dispatcharr ONCE. Distinct from the cross-call LRU passed in;
    # this map drops when the function returns.
    per_call_channel_streams_cache: dict[str, Optional[list[dict]]] = {}

    resolved_count = 0
    unresolved_count = len(unresolvable_channels)
    for channel_uuid, stream_id in stream_id_by_channel.items():
        provider_id = provider_by_stream.get(stream_id)
        stream_in_response = stream_id in provider_by_stream
        stream_name = name_by_stream.get(stream_id)
        if (
            provider_id is None
            and not stream_in_response
            and channel_uuid in url_derived_channels
        ):
            fallback_result = await _resolve_via_channel_streams(
                client,
                channel_uuid,
                url_by_channel.get(channel_uuid),
                per_call_channel_streams_cache,
                channel_streams_cache,
                poll_count,
            )
            if fallback_result is not None:
                provider_by_channel[channel_uuid] = fallback_result
                resolved_count += 1
                continue
            provider_by_channel[channel_uuid] = EMPTY_RESOLUTION
            unresolved_count += 1
            continue
        if provider_id is None:
            provider_by_channel[channel_uuid] = EMPTY_RESOLUTION
            unresolved_count += 1
            if not stream_in_response:
                reason = "stream_not_found"
            else:
                reason = "stream_has_no_provider"
            logger.warning(
                "[STATS_V2] provider_resolution_failed channel=%s stream=%s reason=%s",
                channel_uuid,
                stream_id,
                reason,
            )
        else:
            provider_by_channel[channel_uuid] = ProviderResolution(
                provider_id=provider_id,
                stream_id=stream_id,
                stream_name=stream_name,
            )
            resolved_count += 1

    if emit_metrics:
        _log_provider_resolution_sli(resolved_count, unresolved_count)
    return provider_by_channel


async def _resolve_via_channel_streams(
    client,
    channel_uuid: str,
    active_url: Optional[str],
    per_call_cache: dict[str, Optional[list[dict]]],
    cross_call_cache: Optional[ChannelStreamsCache],
    current_poll: int,
) -> Optional[ProviderResolution]:
    """Channel-streams URL-match fallback (bd-5g7kx, extended by kh23e).

    Pulled out of ``BandwidthTracker._resolve_via_channel_streams`` as
    a free function so the live ``/api/stats/channels`` endpoint
    (bd-ox5q8) and the polling tracker share one implementation.

    Two cache layers compose:

    * ``per_call_cache`` (caller-owned dict): scoped to a single
      ``resolve_active_channel_streams`` invocation. ``None`` value
      means "we tried this call and it raised" — short-circuits
      subsequent attempts in the same call.
    * ``cross_call_cache`` (``ChannelStreamsCache`` | None): bounded
      LRU. When supplied, channel stream lists are reused across
      successive calls within the TTL. ``None`` means no cross-call
      caching (the endpoint passes ``None`` so operator-facing data
      stays at most one Dispatcharr round-trip stale).
    """
    from stream_prober import extract_m3u_account_id

    if channel_uuid in per_call_cache:
        streams = per_call_cache[channel_uuid]
    else:
        streams = (
            cross_call_cache.get(channel_uuid, current_poll)
            if cross_call_cache is not None
            else None
        )
        if streams is None:
            try:
                streams = await client.get_channel_streams(channel_uuid)
            except Exception as e:
                logger.warning(
                    "[STATS_V2] provider_resolution_failed channel=%s reason=channel_streams_lookup_raised error=%s",
                    channel_uuid,
                    e,
                )
                per_call_cache[channel_uuid] = None
                return None
            if not isinstance(streams, list):
                logger.warning(
                    "[STATS_V2] provider_resolution_failed channel=%s reason=channel_streams_lookup_raised error=non_list_response",
                    channel_uuid,
                )
                per_call_cache[channel_uuid] = None
                return None
            if cross_call_cache is not None:
                cross_call_cache.put(channel_uuid, streams, current_poll)
        per_call_cache[channel_uuid] = streams

    if streams is None:
        return None

    if not active_url:
        logger.warning(
            "[STATS_V2] provider_resolution_failed channel=%s reason=channel_streams_no_match detail=no_active_url",
            channel_uuid,
        )
        return None

    normalized_active = _normalize_stream_url_for_match(active_url)
    if normalized_active is None:
        logger.warning(
            "[STATS_V2] provider_resolution_failed channel=%s reason=channel_streams_no_match detail=no_active_url",
            channel_uuid,
        )
        return None

    matched_stream: Optional[dict] = None
    for stream in streams:
        stream_url = _normalize_stream_url_for_match(stream.get("url"))
        if stream_url is None:
            continue
        if stream_url == normalized_active:
            matched_stream = stream
            break
    if matched_stream is None:
        for stream in streams:
            stream_url = _normalize_stream_url_for_match(stream.get("url"))
            if stream_url is None:
                continue
            if stream_url in normalized_active or normalized_active in stream_url:
                matched_stream = stream
                break

    if matched_stream is None:
        logger.warning(
            "[STATS_V2] provider_resolution_failed channel=%s reason=channel_streams_no_match",
            channel_uuid,
        )
        return None

    provider_id = extract_m3u_account_id(matched_stream.get("m3u_account"))
    if provider_id is None:
        logger.warning(
            "[STATS_V2] provider_resolution_failed channel=%s stream=%s reason=stream_has_no_provider",
            channel_uuid,
            matched_stream.get("id"),
        )
        return None

    matched_id = matched_stream.get("id")
    try:
        matched_stream_id: Optional[int] = (
            int(matched_id) if matched_id is not None else None
        )
    except (TypeError, ValueError):
        matched_stream_id = None
    raw_name = matched_stream.get("name")
    matched_stream_name: Optional[str] = (
        str(raw_name) if isinstance(raw_name, str) and raw_name else None
    )
    return ProviderResolution(
        provider_id=int(provider_id),
        stream_id=matched_stream_id,
        stream_name=matched_stream_name,
    )


def _log_provider_resolution_sli(
    resolved_count: int,
    unresolved_count: int,
) -> None:
    """Emit the per-call provider-resolution SLI line + metric.

    Free-function shared by ``resolve_active_channel_streams`` and the
    legacy ``BandwidthTracker._log_provider_resolution_sli`` instance
    method (kept as a back-compat wrapper for existing tests).

    Format: ``[STATS_V2] provider_resolution resolved=X unresolved=Y``.
    Stable substring shape — operators grep on this prefix. The
    Prometheus counter ``ecm_provider_resolution_total`` is incremented
    with a bounded ``result`` label (resolved/unresolved).
    """
    logger.info(
        "[STATS_V2] provider_resolution resolved=%s unresolved=%s",
        resolved_count,
        unresolved_count,
    )
    try:
        from observability import get_metric

        counter = get_metric("provider_resolution_total")
        if resolved_count:
            counter.labels(result="resolved").inc(int(resolved_count))
        if unresolved_count:
            counter.labels(result="unresolved").inc(int(unresolved_count))
    except Exception:  # pragma: no cover — never break the resolver
        logger.debug(
            "[STATS_V2] failed to emit provider_resolution_total metric",
            exc_info=True,
        )


def _coerce_session_user_id(raw: Any) -> Optional[int]:
    """Coerce a Dispatcharr-supplied user id into a value safe for the
    ``session_telemetry.user_id`` FK (bead ``enhancedchannelmanager-gbxmj``).

    Anonymous viewers surface as ``0`` / ``"0"`` from Dispatcharr; that
    sentinel doesn't exist in ``users.id`` so a raw write trips the FK
    constraint at ``session.commit()`` and rolls back the entire poll
    batch. The helper normalizes anything that isn't a positive int (or
    a string that parses cleanly to one) to ``None``.

    * ``None`` / ``""`` / ``0`` / ``"0"`` → ``None`` (anonymous)
    * ``42`` / ``"42"``                  → ``42``
    * ``-1`` / ``"abc"``                 → ``None``
    * ``True`` / ``False``               → ``None`` (reject bool → int)
    * ``42.0``                           → ``None`` (strict: no float)
    """
    if raw is None or raw == "" or raw == 0 or raw == "0":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _normalize_stream_url_for_match(url: Optional[str]) -> Optional[str]:
    """Strip the query string off a stream URL for fallback matching.

    Used by the channel-streams URL-match fallback (bd-5g7kx): the
    active stream URL in ``/proxy/ts/status`` may carry a session token
    or transcode hint as a query string (``...85796.ts?session=abc``),
    while the URL persisted on Dispatcharr's ``/channels/<id>/streams``
    record is the bare ``...85796.ts``. Comparing the URLs verbatim
    would miss this case. Normalizing both sides to the path-only form
    catches the realistic mutations without coupling the matcher to a
    specific query-suffix convention.

    Returns the URL up to (but not including) the first ``?`` or ``#``.
    ``None`` inputs return ``None`` so callers can defer the empty-string
    guard to the comparison site.
    """
    if not url or not isinstance(url, str):
        return None
    cut = len(url)
    for sep in ("?", "#"):
        i = url.find(sep)
        if i != -1 and i < cut:
            cut = i
    return url[:cut] or None


def _telemetry_opt_out_enabled() -> bool:
    """Parse ``ECM_STATS_TELEMETRY_OPT_OUT``. Default-OFF (writes happen).

    Operator-facing opt-out for the Stats v2 data path
    (bead ``enhancedchannelmanager-tp1pd``). When the env var is set to
    a truthy value, ``_collect_stats`` short-circuits AFTER the legacy
    sibling writes (``ChannelBandwidth``, ``BandwidthDaily``,
    ``UniqueClientConnection``) and BEFORE the Stats v2 path
    (``_resolve_provider_ids`` + ``_collect_channel_events`` +
    ``_write_session_telemetry``). Net effect:

    * Zero rows land in ``session_telemetry``.
    * No Dispatcharr ``get_streams_by_ids`` round-trip per poll.
    * No Dispatcharr ``get_system_events`` round-trip per poll.
    * Legacy stats (existing since v0.11.0) continue to record.

    Read per-poll (not cached at import) so an operator who flips the
    env var at runtime (export + container restart) sees the new value
    on the next poll cycle without code awareness of when the flip
    happened. The string-compare cost is microseconds; the latency-
    sensitive surface is the network round-trip the flag elides.

    Truthy-value enum mirrors ``_confusables_fold_enabled`` in
    ``normalization_engine.py`` — ``true``, ``1``, ``yes``, ``on``
    (case-insensitive, whitespace-tolerant). Everything else (including
    empty string and unset) is OFF.
    """
    raw = os.environ.get("ECM_STATS_TELEMETRY_OPT_OUT", "false")
    return raw.strip().lower() in {"true", "1", "yes", "on"}


def _parse_telemetry_exclude_users() -> frozenset[str]:
    """Parse ``ECM_TELEMETRY_EXCLUDE_USERS`` into a normalized token set.

    Operator-facing filter (bead ``enhancedchannelmanager-uqbob``) for
    suppressing ``session_telemetry`` writes attributed to non-stream-
    consuming users. The motivating case: ECM's local ``users`` table
    and Dispatcharr's ``users`` table are separate namespaces with
    coincidentally-overlapping integer ids — ECM ``users.id=3`` is the
    local admin account "claude" while Dispatcharr ``users.id=3`` is a
    real human ("kmfelmer") whose viewing sessions are being attributed
    to "claude" in the Stats panel because the ``session_telemetry.
    user_id`` FK joins on the ECM table.

    The env var is a comma-separated list of tokens. Each token is
    matched against BOTH the Dispatcharr-side ``user_id`` (string-coerced)
    AND the Dispatcharr-side ``username`` (case-insensitive). A row is
    suppressed if EITHER axis matches.

    Examples:

    * ``ECM_TELEMETRY_EXCLUDE_USERS="claude"`` — drop rows whose
      Dispatcharr-side username is "claude" (case-insensitive).
    * ``ECM_TELEMETRY_EXCLUDE_USERS="3,claude"`` — drop rows where the
      raw Dispatcharr user_id is 3 OR the username is "claude". Either
      match is sufficient.
    * ``ECM_TELEMETRY_EXCLUDE_USERS=""`` (or unset) — no filtering;
      pre-uqbob behavior preserved.

    Read per-poll (not cached at import) so an operator who flips the
    env var at runtime (export + container restart) sees the new value
    on the next poll cycle without code awareness of when the flip
    happened — same posture as ``_telemetry_opt_out_enabled``.

    Returns a ``frozenset[str]`` of lower-cased, whitespace-stripped
    tokens. Empty tokens (from leading / trailing / repeated commas)
    are dropped. The empty set is the "no filtering" sentinel.
    """
    raw = os.environ.get("ECM_TELEMETRY_EXCLUDE_USERS", "")
    if not raw:
        return frozenset()
    tokens = (t.strip().lower() for t in raw.split(","))
    return frozenset(t for t in tokens if t)


def _is_excluded_telemetry_user(
    user_id: Optional[int],
    username: Optional[str],
    exclude_tokens: frozenset[str],
) -> bool:
    """Return True if a session_telemetry row should be suppressed.

    Companion to ``_parse_telemetry_exclude_users`` (bead
    ``enhancedchannelmanager-uqbob``). A row is suppressed when either
    the FK-coerced user_id or the resolved Dispatcharr username matches
    a token in the operator-configured exclude list.

    * ``user_id`` — the FK-safe coerced user_id (output of
      ``_coerce_session_user_id``). ``None`` for anonymous viewers; those
      can never match a numeric token because they have no id to compare.
    * ``username`` — the Dispatcharr-side username resolved via the
      per-poll users map. ``None`` or empty string when the lookup did
      not produce a value (Dispatcharr returned no user record, or the
      exclude env var is unset so the lookup was skipped to save a
      round-trip).
    * ``exclude_tokens`` — already lower-cased; the caller invokes
      ``_parse_telemetry_exclude_users`` once per poll.

    Returns ``False`` immediately when ``exclude_tokens`` is empty so
    the default-OFF posture has zero call overhead beyond a single
    truthiness check.
    """
    if not exclude_tokens:
        return False
    if user_id is not None and str(user_id) in exclude_tokens:
        return True
    if username:
        if username.strip().lower() in exclude_tokens:
            return True
    return False


def _log_emby_resolver_failure(exc: BaseException) -> None:
    """Emit a [BANDWIDTH] [EMBY] WARN at most once per ``_EMBY_WARN_WINDOW_SECONDS``.

    Companion to the per-(channel, ip) resolver wrapper in
    ``_resolve_emby_attributions`` (bd-gih6d). The resolver is documented
    to "never raise" — every defensive path returns ``None`` and logs at
    the appropriate level. This helper exists as a belt-and-braces guard
    so that if a future refactor (or any unforeseen runtime fault: cache
    failure, settings access raise, etc.) lets an exception escape, the
    BandwidthTracker poll loop still produces telemetry rows AND the
    operator sees a single visible signal per minute.

    The rate-limit window is module-level (``_emby_resolver_last_warn_at``)
    so multi-tracker test seams share the same clock — without that,
    parallel tracker instances would each log their own WARN inside the
    same window and drown the signal.

    The exception is included in the log line so operators can grep for
    the failure mode. ``exc_info`` is intentionally NOT passed: a full
    traceback on every poll cycle (until the window expires) is too
    noisy for the operational signal-to-noise ratio that motivated this
    suppression in the first place.
    """
    global _emby_resolver_last_warn_at
    now = time.monotonic()
    last = _emby_resolver_last_warn_at
    if last is not None and (now - last) < _EMBY_WARN_WINDOW_SECONDS:
        return
    _emby_resolver_last_warn_at = now
    logger.warning(
        "[BANDWIDTH] [EMBY] resolver failed; telemetry will be written "
        "without Emby attribution this poll cycle (rate-limited to one "
        "warning per %.0fs): %s",
        _EMBY_WARN_WINDOW_SECONDS, exc,
    )


def _reset_emby_warn_state_for_tests() -> None:
    """Clear the module-level WARN rate-limit timestamp — tests only.

    The rate-limit module-global persists across test functions; without
    this reset, a test that triggers the WARN once would suppress the
    WARN in a subsequent test that wants to assert it surfaces again.
    Production code paths do not call this.
    """
    global _emby_resolver_last_warn_at
    _emby_resolver_last_warn_at = None


def _log_plex_resolver_failure(exc: BaseException) -> None:
    """Per-source twin of :func:`_log_emby_resolver_failure` for Plex.

    Independent rate-limit clock (``_plex_resolver_last_warn_at``) so a
    sustained Emby or Jellyfin outage cannot silence Plex WARN lines —
    failure isolation across sources (bd-r5f0c.4, SRE requirement).
    """
    global _plex_resolver_last_warn_at
    now = time.monotonic()
    last = _plex_resolver_last_warn_at
    if last is not None and (now - last) < _PLEX_WARN_WINDOW_SECONDS:
        return
    _plex_resolver_last_warn_at = now
    logger.warning(
        "[BANDWIDTH] [PLEX] resolver failed; telemetry will be written "
        "without Plex attribution this poll cycle (rate-limited to one "
        "warning per %.0fs): %s",
        _PLEX_WARN_WINDOW_SECONDS, exc,
    )


def _log_jellyfin_resolver_failure(exc: BaseException) -> None:
    """Per-source twin of :func:`_log_emby_resolver_failure` for Jellyfin.

    Independent rate-limit clock (``_jellyfin_resolver_last_warn_at``)
    so a sustained Emby or Plex outage cannot silence Jellyfin WARN
    lines (bd-r5f0c.4, SRE requirement).
    """
    global _jellyfin_resolver_last_warn_at
    now = time.monotonic()
    last = _jellyfin_resolver_last_warn_at
    if last is not None and (now - last) < _JELLYFIN_WARN_WINDOW_SECONDS:
        return
    _jellyfin_resolver_last_warn_at = now
    logger.warning(
        "[BANDWIDTH] [JELLYFIN] resolver failed; telemetry will be written "
        "without Jellyfin attribution this poll cycle (rate-limited to one "
        "warning per %.0fs): %s",
        _JELLYFIN_WARN_WINDOW_SECONDS, exc,
    )


def _reset_plex_warn_state_for_tests() -> None:
    """Clear the module-level Plex WARN rate-limit timestamp — tests only."""
    global _plex_resolver_last_warn_at
    _plex_resolver_last_warn_at = None


def _reset_jellyfin_warn_state_for_tests() -> None:
    """Clear the module-level Jellyfin WARN rate-limit timestamp — tests only."""
    global _jellyfin_resolver_last_warn_at
    _jellyfin_resolver_last_warn_at = None


def _reset_attribution_warn_state_for_tests() -> None:
    """Clear all per-source resolver WARN rate-limit timestamps — tests only.

    Convenience for the attribution test suite which wants every source
    re-armed between cases. Sidesteps three separate reset calls in
    every fixture.
    """
    _reset_emby_warn_state_for_tests()
    _reset_plex_warn_state_for_tests()
    _reset_jellyfin_warn_state_for_tests()


def get_user_timezone() -> timezone:
    """Get the user's configured timezone, or UTC if not set/invalid."""
    try:
        from config import get_settings
        settings = get_settings()
        if settings.user_timezone:
            return ZoneInfo(settings.user_timezone)
    except Exception as e:
        logger.debug("[BANDWIDTH] Could not get user timezone: %s", e)
    return timezone.utc


def get_current_date() -> date:
    """Get current date in user's timezone."""
    tz = get_user_timezone()
    return datetime.now(tz).date()

# Default polling interval in seconds (used if not configured)
DEFAULT_POLL_INTERVAL = 10


class BandwidthTracker:
    """
    Background service that tracks bandwidth usage over time.
    Polls Dispatcharr's stats endpoint and stores daily aggregates.
    """

    def __init__(self, client, poll_interval: int = DEFAULT_POLL_INTERVAL):
        """
        Initialize the tracker.

        Args:
            client: DispatcharrClient instance for API calls
            poll_interval: Seconds between polls (default 10)
        """
        self.client = client
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_bytes: dict[str, int] = {}  # Track per-channel bytes to compute deltas
        self._last_active_channels: set[str] = set()  # Track which channels were active last poll (UUIDs)
        self._channel_names: dict[str, str] = {}  # Cache channel names for stop events
        self._ecm_channel_map: dict[str, str] = {}  # UUID -> name mapping from ECM channels
        self._ecm_channel_number_map: dict[int, str] = {}  # channel_number -> name mapping from ECM
        self._channel_map_refresh_interval = 300  # Refresh channel map every 5 minutes
        self._last_channel_map_refresh = 0.0
        # Enhanced stats tracking (v0.11.0)
        # Maps (channel_id, ip_address) -> connection_id in UniqueClientConnection table
        self._active_connections: dict[tuple[str, str], int] = {}
        # Track last known clients per channel for detecting new/disconnected clients
        self._last_channel_clients: dict[str, set[str]] = {}  # channel_id -> set of IPs
        # Buffer-event ingest (bd-skqln.15). Bounded LRU of Dispatcharr
        # ``system_event.id`` values already counted. Persists across polls
        # because Dispatcharr's ``/api/core/system-events/`` feed re-delivers
        # recent events on every fetch — without cross-poll dedup the same
        # event would be counted N times. The cap is intentionally generous
        # (10x the per-poll fetch limit) so a stable working set fits without
        # eviction; the LRU sheds the oldest entries first.
        self._seen_buffer_event_ids: OrderedDict[int, None] = OrderedDict()
        self._seen_buffer_event_ids_cap = 10_000

        # Provider resolver: channel-streams URL-match fallback cache
        # (bd-5g7kx, refactored bd-ox5q8). The cross-poll LRU now lives
        # in a ``ChannelStreamsCache`` instance owned by the tracker so
        # the same cache type can be reused by the on-demand
        # ``/api/stats/channels`` enrichment path (which constructs a
        # fresh cache per request).
        #
        # Default cap (200 channels) and TTL (30 polls — ~5 min at the
        # default 10s poll cadence) are surfaced as module-level
        # constants ``DEFAULT_CHANNEL_STREAMS_CACHE_CAP`` and
        # ``DEFAULT_CHANNEL_STREAMS_CACHE_TTL_POLLS``.
        self._provider_cache = ChannelStreamsCache()
        # Back-compat attributes — older tests introspect
        # ``_channel_streams_cache_ttl_polls`` directly. Mirror to the
        # cache instance values so the legacy attribute names continue
        # to resolve. The legacy ``_channel_streams_cache`` OrderedDict
        # is replaced by ``self._provider_cache._entries``; tests that
        # poked at the raw dict are migrated to the public API in the
        # same change.
        self._channel_streams_cache_cap = self._provider_cache.cap
        self._channel_streams_cache_ttl_polls = self._provider_cache.ttl_polls
        # Monotonically increasing poll counter, used as the TTL anchor
        # for ``_provider_cache``. Incremented on every
        # ``_collect_stats`` entry so the counter advances even when the
        # poll bails early (e.g. ``get_channel_stats`` raise).
        self._poll_count = 0

        # bd-8axhi defense-in-depth: one-shot flag tracking whether
        # ``_write_session_telemetry`` has already escalated a runtime
        # schema-drift error to ERROR-level. The bd-zaaey loud-fail at
        # ``init_db`` (``_assert_schema_matches_models``) catches drift at
        # boot, but cannot catch drift introduced AFTER the process is
        # already running — for example, the hot-deploy workflow where a
        # developer ``docker cp``'s new writer code + a new alembic
        # migration into a live container without ``docker restart``. The
        # writer's existing try/except already prevents user-facing damage,
        # but historically logged "no such column" / "no such table"
        # OperationalErrors at WARN, where they blended into ordinary
        # noise. The first such error escalates to ERROR with an
        # actionable recovery path; subsequent errors fall back to WARN
        # so the log is not flooded. Cleared by a successful write so
        # repaired environments re-arm the alarm if drift recurs.
        self._schema_drift_alarm_armed = True

    async def start(self):
        """Start the background polling task."""
        if self._running:
            logger.warning("[BANDWIDTH] BandwidthTracker already running")
            return

        # Initialize channel maps on startup
        await self._initialize_channel_maps()

        # Clean up stale connections from previous runs
        self._cleanup_stale_connections()

        # Operator-facing Stats v2 opt-out (bd-tp1pd). When the env var
        # is set at process start, announce it ONCE — operators reading
        # ``docker logs`` need a visible signal that Stats v2 is
        # silenced. Per-poll re-emission would be log spam (one line
        # every ``poll_interval`` seconds). Read at start() time
        # because the start log is the operator's single-pane-of-glass
        # check; ``_collect_stats`` rechecks per-poll so a runtime flip
        # still takes effect.
        if _telemetry_opt_out_enabled():
            logger.info(
                "[STATS_V2] telemetry opt-out is ENABLED — no session_telemetry data will be collected"
            )

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[BANDWIDTH] BandwidthTracker started (polling every %ss)", self.poll_interval)

    async def stop(self):
        """Stop the background polling task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("[BANDWIDTH] Polling task cancelled during shutdown")
            self._task = None
        logger.info("[BANDWIDTH] BandwidthTracker stopped")

    async def _initialize_channel_maps(self):
        """
        Initialize the ECM channel maps on startup.
        This fetches all channels from ECM and builds maps for UUID->name and channel_number->name lookups.
        """
        try:
            # Fetch all channels from ECM (paginated)
            uuid_map: dict[str, str] = {}
            number_map: dict[int, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    channel_number = ch.get("channel_number")
                    if uuid and name:
                        uuid_map[uuid] = name
                    if channel_number is not None and name:
                        number_map[int(channel_number)] = name

                if not result.get("next"):
                    break
                page += 1
                if page > 20:
                    break

            self._ecm_channel_map = uuid_map
            self._ecm_channel_number_map = number_map
            self._last_channel_map_refresh = time.time()

            logger.info("[BANDWIDTH] Loaded channel maps: %s by UUID, %s by channel number", len(uuid_map), len(number_map))

        except Exception as e:
            logger.exception("[BANDWIDTH] Failed to initialize channel maps: %s", e)

    def _cleanup_stale_connections(self):
        """
        Clean up stale connections from previous runs.
        Marks any connections with null disconnected_at as completed.
        Also updates channel names that look like UUIDs if we can resolve them.
        """
        import re
        from models import ChannelPopularityScore, ChannelWatchStats
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
        truncated_pattern = re.compile(r'^Channel [0-9a-f]{8}\.\.\.$', re.I)

        def needs_name_fix(name: str) -> bool:
            """Check if a channel name looks like a UUID or truncated UUID."""
            return bool(uuid_pattern.match(name) or truncated_pattern.match(name))

        session = get_session()
        try:
            # Find all connections with null disconnected_at (stale "watching" entries)
            stale_connections = session.query(UniqueClientConnection).filter(
                UniqueClientConnection.disconnected_at.is_(None)
            ).all()

            stale_count = 0
            name_updates = 0

            for conn in stale_connections:
                # Mark as disconnected - use connected_at + watch_seconds as approximate end time
                if conn.watch_seconds > 0:
                    conn.disconnected_at = conn.connected_at + timedelta(seconds=conn.watch_seconds)
                else:
                    conn.disconnected_at = conn.connected_at
                stale_count += 1

            # Fix channel names in UniqueClientConnection
            all_connections = session.query(UniqueClientConnection).all()
            for conn in all_connections:
                if needs_name_fix(conn.channel_name):
                    real_name = self._ecm_channel_map.get(conn.channel_id)
                    if real_name and real_name != conn.channel_name:
                        conn.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelPopularityScore
            popularity_scores = session.query(ChannelPopularityScore).all()
            for score in popularity_scores:
                if needs_name_fix(score.channel_name):
                    real_name = self._ecm_channel_map.get(score.channel_id)
                    if real_name and real_name != score.channel_name:
                        score.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelWatchStats
            watch_stats = session.query(ChannelWatchStats).all()
            for stats in watch_stats:
                if needs_name_fix(stats.channel_name):
                    real_name = self._ecm_channel_map.get(stats.channel_id)
                    if real_name and real_name != stats.channel_name:
                        stats.channel_name = real_name
                        name_updates += 1

            # Fix channel names in ChannelBandwidth
            bandwidth_records = session.query(ChannelBandwidth).all()
            for bw in bandwidth_records:
                if needs_name_fix(bw.channel_name):
                    real_name = self._ecm_channel_map.get(bw.channel_id)
                    if real_name and real_name != bw.channel_name:
                        bw.channel_name = real_name
                        name_updates += 1

            session.commit()
            if stale_count > 0 or name_updates > 0:
                logger.info("[BANDWIDTH] Cleaned up %s stale connections, updated %s channel names", stale_count, name_updates)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to cleanup stale connections: %s", e)
            session.rollback()
        finally:
            session.close()

    async def _poll_loop(self):
        """Main polling loop - runs until stopped."""
        while self._running:
            try:
                # Refresh channel name map periodically
                await self._maybe_refresh_channel_map()
                await self._collect_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("[BANDWIDTH] BandwidthTracker error: %s", e)

            # Wait for next poll interval
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _maybe_refresh_channel_map(self):
        """Refresh the ECM channel maps (UUID->name and channel_number->name)."""
        now = time.time()
        if now - self._last_channel_map_refresh < self._channel_map_refresh_interval:
            return

        try:
            # Fetch all channels from ECM (paginated)
            uuid_map: dict[str, str] = {}
            number_map: dict[int, str] = {}
            page = 1
            page_size = 500
            while True:
                result = await self.client.get_channels(page=page, page_size=page_size)
                channels = result.get("results", [])
                for ch in channels:
                    uuid = ch.get("uuid")
                    name = ch.get("name")
                    channel_number = ch.get("channel_number")
                    if uuid and name:
                        uuid_map[uuid] = name
                    if channel_number is not None and name:
                        # channel_number can be float or int, convert to int for lookup
                        number_map[int(channel_number)] = name

                # Check if there are more pages
                if not result.get("next"):
                    break
                page += 1
                # Safety limit
                if page > 20:
                    break

            self._ecm_channel_map = uuid_map
            self._ecm_channel_number_map = number_map
            self._last_channel_map_refresh = now
            logger.debug("[BANDWIDTH] Refreshed channel maps: %s by UUID, %s by number", len(uuid_map), len(number_map))
        except Exception as e:
            logger.debug("[BANDWIDTH] Failed to refresh channel map: %s", e)

    async def _collect_stats(self):
        """Fetch stats from Dispatcharr and update daily totals."""
        # Bump the poll counter first thing so the channel-streams
        # fallback's TTL is anchored against a monotonically-increasing
        # value even on polls that bail early below (bd-5g7kx). The
        # counter is a simple int — wraparound is not a concern in any
        # realistic operator timeline (>68 years at 10s polls on 32-bit
        # int, far longer on 64-bit Python ints).
        self._poll_count += 1
        try:
            stats = await self.client.get_channel_stats()
        except Exception as e:
            logger.warning("[BANDWIDTH] Failed to fetch stats from Dispatcharr: %s", e)
            return

        # Stamp the moment we observed this poll. Used by the Stats v2
        # session_telemetry write (skqln.3 step (a)). Held outside the
        # feature-flag check so the value is identical whether or not the
        # additive write is enabled — keeps observed_at semantics stable
        # if/when the flag flips at runtime mid-cycle.
        observed_at_ms = int(time.time() * 1000)

        channels = stats.get("channels", [])
        logger.debug("[BANDWIDTH] Collected stats for %s active channels", len(channels))

        # Calculate totals from all active channels
        total_bytes_delta = 0
        total_bytes_in_delta = 0  # Inbound from providers
        total_bytes_out_delta = 0  # Outbound to clients
        current_bitrate_in = 0  # Current inbound bitrate (bps)
        current_bitrate_out = 0  # Current outbound bitrate (bps)
        active_channels = len(channels)
        total_clients = 0

        current_bytes: dict[str, int] = {}
        current_active_channels: set[str] = set()
        current_channel_clients: dict[str, set[str]] = {}  # channel_id -> set of IPs
        newly_active_channels: list[dict] = []
        still_active_channels: list[dict] = []
        # Per-channel bandwidth tracking (v0.11.0)
        channel_bandwidth_updates: list[dict] = []
        # Per-channel snapshot for the Stats v2 session_telemetry helper
        # (skqln.3 step (a)). One entry per active channel this poll.
        telemetry_channel_snapshot: list[dict] = []

        for channel in channels:
            channel_id = str(channel.get("channel_id", ""))
            channel_number = channel.get("channel_number")
            # Get channel name - prefer ECM lookup by channel_number or UUID, fall back to Dispatcharr's response
            channel_name = None
            # Try channel_number lookup first (most reliable)
            if channel_number is not None:
                channel_name = self._ecm_channel_number_map.get(int(channel_number))
            # Fall back to UUID lookup
            if not channel_name:
                channel_name = self._ecm_channel_map.get(channel_id)
            # Fall back to Dispatcharr's response
            if not channel_name:
                channel_name = channel.get("channel_name") or channel.get("name")
            # Last resort: use partial UUID
            if not channel_name:
                channel_name = f"Channel {channel_id[:8]}..."

            bytes_now = channel.get("total_bytes", 0) or 0
            client_count = channel.get("client_count", 0) or 0
            avg_bitrate_kbps = channel.get("avg_bitrate_kbps", 0) or 0

            # Extract client IP addresses and user_id mappings
            clients = channel.get("clients", [])
            client_ips = [c.get("ip_address") for c in clients if c.get("ip_address")]
            client_user_map = {}
            for c in clients:
                ip = c.get("ip_address")
                uid = c.get("user_id")
                if ip and uid:
                    client_user_map[ip] = uid
            current_channel_clients[channel_id] = set(client_ips)

            current_bytes[channel_id] = bytes_now
            total_clients += client_count

            # Track current bitrate (for peak calculation)
            # Inbound: one stream per channel from provider
            # Outbound: stream × number of clients
            channel_bitrate_bps = int(avg_bitrate_kbps * 1000)  # Convert kbps to bps
            current_bitrate_in += channel_bitrate_bps  # One stream per channel
            current_bitrate_out += channel_bitrate_bps * max(client_count, 1)  # Stream × clients

            # Calculate per-channel byte delta
            channel_bytes_delta = 0
            if channel_id in self._last_bytes:
                prev_bytes = self._last_bytes[channel_id]
                if bytes_now > prev_bytes:
                    channel_bytes_delta = bytes_now - prev_bytes
                    total_bytes_delta += channel_bytes_delta
                    # Calculate in/out bytes
                    # Inbound = bytes from provider (one stream per channel)
                    total_bytes_in_delta += channel_bytes_delta
                    # Outbound = bytes fanned out to all clients (stream × clients)
                    total_bytes_out_delta += channel_bytes_delta * max(client_count, 1)

            # Track active channels for watch counting (use string ID for UUID support)
            if channel_id:
                current_active_channels.add(channel_id)
                self._channel_names[channel_id] = channel_name  # Cache name for stop events

                # Detect new and continuing client connections
                last_clients = self._last_channel_clients.get(channel_id, set())
                new_clients = set(client_ips) - last_clients
                continuing_clients = set(client_ips) & last_clients

                # Check if this channel just became active (wasn't in last poll)
                if channel_id not in self._last_active_channels:
                    newly_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                        "client_user_map": client_user_map,
                        "client_count": client_count,
                    })
                else:
                    # Channel was active last poll and still is - accumulate watch time
                    still_active_channels.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "client_ips": client_ips,
                        "client_user_map": client_user_map,
                        "new_clients": list(new_clients),
                        "continuing_clients": list(continuing_clients),
                        "client_count": client_count,
                    })

                # Track per-channel bandwidth data
                if channel_bytes_delta > 0 or client_count > 0:
                    channel_bandwidth_updates.append({
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "bytes_delta": channel_bytes_delta,
                        "client_count": client_count,
                    })

                # Capture the snapshot the Stats v2 session_telemetry helper
                # needs (skqln.3 step (a)). Built unconditionally so the data
                # shape is stable; the helper is a no-op when the feature
                # flag is OFF. ``stream_id`` is the Dispatcharr integer ID
                # of the stream currently being served — surfaced by
                # ``/proxy/ts/status``, consumed by the provider resolver
                # in ``_resolve_provider_ids`` (bd-skqln.14). May be missing
                # if Dispatcharr serves a degraded stats payload; the
                # resolver tolerates that and falls back to URL parsing
                # (bd-kbgey) when ``url`` carries the same id as the trailing
                # ``.../<stream_id>.ts`` path segment, then NULL.
                telemetry_channel_snapshot.append({
                    "channel_uuid": channel_id,
                    "channel_number": channel_number,
                    # bd-zldrq (fix-forward for v0.17.1-0033): pass the
                    # ECM-resolved channel name into _resolve_emby_attributions
                    # so the tiered resolver can match Emby's live-TV
                    # item.Name "<number> | <name>" against the channel
                    # name (the Dispatcharr stream name does not fuzzy
                    # match it above 0.85 for provider-prefixed verbose
                    # names like "US: ESPN FHD").
                    "channel_name": channel_name,
                    "client_ips": list(client_ips),
                    "client_user_map": dict(client_user_map),
                    "channel_bytes_delta": channel_bytes_delta,
                    "stream_id": channel.get("stream_id"),
                    "url": channel.get("url"),
                })

        # Check for channels that stopped being watched
        stopped_channels = self._last_active_channels - current_active_channels
        if stopped_channels:
            self._log_watch_stop_events(stopped_channels)
            self._close_client_connections(stopped_channels)

        # Update last bytes tracking
        self._last_bytes = current_bytes
        self._last_active_channels = current_active_channels
        self._last_channel_clients = current_channel_clients

        # Only record if there's actual data transfer
        if total_bytes_delta > 0 or active_channels > 0:
            self._update_daily_record(
                total_bytes_delta,
                active_channels,
                total_clients,
                bytes_in_delta=total_bytes_in_delta,
                bytes_out_delta=total_bytes_out_delta,
                current_bitrate_in=current_bitrate_in,
                current_bitrate_out=current_bitrate_out,
            )
            if total_bytes_delta > 0:
                bytes_mb = total_bytes_delta / (1024 * 1024)
                logger.debug("[BANDWIDTH] Bandwidth delta: %.2f MB (in: %.2f, out: %.2f), active channels: %s, clients: %s", bytes_mb, total_bytes_in_delta / (1024*1024), total_bytes_out_delta / (1024*1024), active_channels, total_clients)

        # Update per-channel bandwidth (v0.11.0)
        if channel_bandwidth_updates:
            self._update_channel_bandwidth(channel_bandwidth_updates)

        # Resolve user_id → username for any channels with user IDs
        all_channel_data = newly_active_channels + still_active_channels
        has_user_ids = any(
            ch.get("client_user_map") for ch in all_channel_data
        )
        # The Dispatcharr-side user_id → username map. Populated below
        # when (a) watch-history flow needs usernames, OR (b) the
        # bd-uqbob exclude filter is configured and the snapshot carries
        # user ids that need matching. Keyed by ``str(disp_user_id)`` to
        # match the watch-history convention.
        dispatcharr_user_map: dict[str, str] = {}
        snapshot_has_user_ids = any(
            entry.get("client_user_map") for entry in telemetry_channel_snapshot
        )
        # bd-uqbob: the exclude set means we may need usernames for the
        # filter even when the watch-history path doesn't need them.
        # bd-gsn3r: the writer ALSO needs the username unconditionally
        # to populate ``session_telemetry.dispatcharr_username`` (the
        # denormalized read-side replacement for the dropped ECM
        # ``users`` FK join). When the snapshot carries any user IDs,
        # pay the single get_users() round-trip per poll so the writer
        # can stamp the username — otherwise pre-migration-style NULL
        # rows leak through and the panel falls back to "Unknown
        # viewer". The cost is one Dispatcharr API call per poll which
        # the watch-history path already paid in the common case.
        exclude_user_tokens = _parse_telemetry_exclude_users()
        need_user_resolution = (
            has_user_ids
            or snapshot_has_user_ids
            or bool(exclude_user_tokens)
        )
        if need_user_resolution:
            try:
                users = await self.client.get_users()
                dispatcharr_user_map = {
                    str(u["id"]): u.get("username", "") for u in users
                }
                for ch in all_channel_data:
                    client_user_map = ch.get("client_user_map", {})
                    ch["client_username_map"] = {
                        ip: dispatcharr_user_map.get(str(uid), "")
                        for ip, uid in client_user_map.items()
                    }
            except Exception as e:
                logger.warning("[BANDWIDTH] Failed to resolve usernames for watch history: %s", e)

        # Update watch counts for newly active channels (and log start events)
        if newly_active_channels:
            logger.info("[BANDWIDTH] %s channel(s) started streaming", len(newly_active_channels))
            self._update_watch_counts(newly_active_channels)

        # Accumulate watch time for still-active channels
        if still_active_channels:
            self._update_watch_time(still_active_channels)

        # Log stopped channels
        if stopped_channels:
            logger.info("[BANDWIDTH] %s channel(s) stopped streaming", len(stopped_channels))

        # Operator-facing Stats v2 telemetry opt-out (bd-tp1pd). When the
        # ``ECM_STATS_TELEMETRY_OPT_OUT`` env var is truthy, the entire
        # Stats v2 path is short-circuited: the provider resolver is not
        # called (skips the Dispatcharr ``get_streams_by_ids`` round-trip),
        # the buffer-event ingest is not called (skips the
        # ``get_system_events`` round-trip), and no ``session_telemetry``
        # rows are written. The legacy sibling writes above
        # (``ChannelBandwidth``, ``BandwidthDaily``,
        # ``UniqueClientConnection``) are NOT affected — they pre-date
        # Stats v2 and are not part of the opt-out surface.
        #
        # The env var is read per-poll so a runtime flip takes effect on
        # the next cycle. Cost: one os.environ lookup + string compare
        # per poll (microseconds). The latency-sensitive surface is the
        # network round-trips this flag elides, not the parse itself.
        if _telemetry_opt_out_enabled():
            return

        # Stats v2 write (bd-skqln.3 step (d)). Runs LAST and is wrapped in
        # a defensive try/except inside the helper so a failure in this
        # path cannot disturb the legacy sibling writes above. Step (d)
        # made this unconditional — the ECM_SESSION_TELEMETRY_WRITE_ENABLED
        # kill-switch was retired along with the legacy
        # ``ChannelWatchStats`` writes that the gate used to protect.
        # (bd-tp1pd re-introduces an env var, but as an operator-facing
        # opt-out, not a transition gate — see the short-circuit above.)
        #
        # Provider resolution (bd-skqln.14): the snapshot already carries
        # the ``stream_id`` Dispatcharr surfaced per-channel. The resolver
        # batches those stream IDs into ONE ``get_streams_by_ids`` call
        # per poll and returns a ``{channel_uuid: provider_id}`` map. The
        # cache lives only for the duration of this invocation — next
        # poll re-resolves so a stream's failover hop is picked up
        # immediately. NULL on failure (network, missing stream, deleted
        # provider); the row still gets written.
        provider_by_channel = await self._resolve_provider_ids(
            telemetry_channel_snapshot
        )
        # Channel-event ingest (bd-ov5vb, broadens bd-skqln.15). Fetches
        # the channel-health subset of Dispatcharr's
        # ``/api/core/system-events/`` feed (no event_type filter —
        # buckets client-side into channel_buffering / channel_reconnect
        # / channel_error / stream_switch), de-duplicates against the
        # cross-poll LRU, and produces a
        # ``{channel_uuid: {event_type: deduped_count}}`` map. Failure is
        # non-fatal — the helper returns ``{}`` and the session_telemetry
        # rows still write with every per-type counter at 0.
        channel_events_by_channel = await self._collect_channel_events(
            telemetry_channel_snapshot
        )
        # bd-r5f0c.4 (extends bd-gih6d): cross-reference each active
        # (channel, ip) pair against ALL THREE media-source session
        # lists (Emby + Plex + Jellyfin) so the writer can stamp the
        # corresponding ``session_telemetry.*_user_id`` /
        # ``*_user_name`` columns. The helper fans out via
        # ``asyncio.gather`` with per-source timeout — one source's
        # outage CANNOT block the telemetry write or the other
        # sources' attributions. Returns a sparse
        # ``{(channel, ip): AttributionResult}`` map; pairs not in the
        # map land NULL across every source's column pair.
        attributions = await self._resolve_attributions(
            telemetry_channel_snapshot, provider_by_channel,
        )
        self._write_session_telemetry(
            telemetry_channel_snapshot,
            observed_at_ms,
            provider_by_channel,
            channel_events_by_channel,
            # bd-uqbob: pre-resolved Dispatcharr user_id → username map and
            # already-parsed exclude token set. Both default to empty on
            # the call-site signature so older test seams keep working;
            # an empty exclude set short-circuits the filter inside the
            # helper before any per-row work runs.
            dispatcharr_user_map=dispatcharr_user_map,
            exclude_user_tokens=exclude_user_tokens,
            # bd-r5f0c.4: sparse {(channel_uuid, ip): AttributionResult}
            # map — empty when all sources are disabled or no pair
            # resolved on any source.
            attributions=attributions,
        )

    def _update_daily_record(
        self,
        bytes_delta: int,
        active_channels: int,
        total_clients: int,
        bytes_in_delta: int = 0,
        bytes_out_delta: int = 0,
        current_bitrate_in: int = 0,
        current_bitrate_out: int = 0,
    ):
        """Update today's bandwidth record in the database (using user's timezone)."""
        today = get_current_date()

        session = get_session()
        try:
            # Get or create today's record
            record = session.query(BandwidthDaily).filter(
                BandwidthDaily.date == today
            ).first()

            if record is None:
                record = BandwidthDaily(
                    date=today,
                    bytes_transferred=0,
                    bytes_in=0,
                    bytes_out=0,
                    peak_channels=0,
                    peak_clients=0,
                    peak_bitrate_in=0,
                    peak_bitrate_out=0,
                )
                session.add(record)

            # Update totals
            record.bytes_transferred += bytes_delta
            record.bytes_in += bytes_in_delta
            record.bytes_out += bytes_out_delta
            record.peak_channels = max(record.peak_channels, active_channels)
            record.peak_clients = max(record.peak_clients, total_clients)
            # Update peak bitrates (track highest seen during the day)
            record.peak_bitrate_in = max(record.peak_bitrate_in, current_bitrate_in)
            record.peak_bitrate_out = max(record.peak_bitrate_out, current_bitrate_out)

            session.commit()
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update bandwidth record: %s", e)
            session.rollback()
        finally:
            session.close()

    def _update_channel_bandwidth(self, updates: list[dict]):
        """Update per-channel bandwidth records (v0.11.0)."""
        today = get_current_date()
        session = get_session()
        try:
            for upd in updates:
                channel_id = upd["channel_id"]
                channel_name = upd["channel_name"]
                bytes_delta = upd["bytes_delta"]
                client_count = upd["client_count"]

                # Get or create today's record for this channel
                record = session.query(ChannelBandwidth).filter(
                    ChannelBandwidth.channel_id == channel_id,
                    ChannelBandwidth.date == today
                ).first()

                if record is None:
                    record = ChannelBandwidth(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        date=today,
                        bytes_transferred=0,
                        peak_clients=0,
                        total_watch_seconds=0,
                        connection_count=0,
                    )
                    session.add(record)

                # Update record
                record.bytes_transferred += bytes_delta
                record.peak_clients = max(record.peak_clients, client_count)
                record.total_watch_seconds += self.poll_interval * client_count  # Each client adds poll_interval seconds
                record.channel_name = channel_name  # Update name in case it changed

            session.commit()
            logger.debug("[BANDWIDTH] Updated channel bandwidth for %s channels", len(updates))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update channel bandwidth: %s", e)
            session.rollback()
        finally:
            session.close()

    def _update_watch_counts(self, channels: list[dict]):
        """Record newly active channels: create UniqueClientConnection rows,
        bump per-channel connection_count, and emit the journal start event.

        bd-skqln.3 step (d): the legacy ``ChannelWatchStats`` write that
        used to live in this method is gone — the popularity calculator
        and the top-watched API now derive their inputs from
        ``session_telemetry`` and ``unique_client_connections``.
        """
        from journal import log_entry

        session = get_session()
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                client_ips = ch.get("client_ips", [])
                client_user_map = ch.get("client_user_map", {})
                client_username_map = ch.get("client_username_map", {})

                # Create UniqueClientConnection records for each client (v0.11.0)
                for ip in client_ips:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        user_id=client_user_map.get(ip),
                        username=client_username_map.get(ip) or None,
                        date=today,
                        connected_at=now,
                        watch_seconds=0,
                    )
                    session.add(connection)
                    session.flush()  # Get the ID
                    # Track active connection
                    self._active_connections[(channel_id, ip)] = connection.id

                # Update ChannelBandwidth connection count
                bw_record = session.query(ChannelBandwidth).filter(
                    ChannelBandwidth.channel_id == channel_id,
                    ChannelBandwidth.date == today
                ).first()
                if bw_record:
                    bw_record.connection_count += len(client_ips)

                # Build description with IP addresses
                ip_str = ", ".join(client_ips) if client_ips else "unknown"
                description = f"Started watching {channel_name} from {ip_str}"

                # Log journal entry for watch start
                log_entry(
                    category="watch",
                    action_type="start",
                    entity_name=channel_name,
                    description=description,
                    user_initiated=False,
                    after_value={
                        "channel_id": channel_id,
                        "client_ips": client_ips,
                    },
                )

            session.commit()
            logger.debug("[BANDWIDTH] Updated watch counts for %s channels", len(channels))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update watch counts: %s", e)
            session.rollback()
        finally:
            session.close()

    def _update_watch_time(self, channels: list[dict]):
        """Accumulate watch time for channels that are still active.

        bd-skqln.3 step (d): the legacy ``ChannelWatchStats`` write that
        used to live in this method is gone — watch time is now derived
        from ``session_telemetry`` (one row per poll per client) on read.
        The per-client ``UniqueClientConnection.watch_seconds`` write
        below stays — it's a per-connection accumulator, not the
        per-channel aggregate that was retired.
        """
        session = get_session()
        today = get_current_date()
        try:
            now = datetime.now(get_user_timezone())
            for ch in channels:
                channel_id = ch["channel_id"]
                channel_name = ch["channel_name"]
                new_clients = ch.get("new_clients", [])
                continuing_clients = ch.get("continuing_clients", [])
                client_user_map = ch.get("client_user_map", {})
                client_username_map = ch.get("client_username_map", {})

                # Handle new clients that joined mid-stream (v0.11.0)
                for ip in new_clients:
                    connection = UniqueClientConnection(
                        ip_address=ip,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        user_id=client_user_map.get(ip),
                        username=client_username_map.get(ip) or None,
                        date=today,
                        connected_at=now,
                        watch_seconds=0,
                    )
                    session.add(connection)
                    session.flush()
                    self._active_connections[(channel_id, ip)] = connection.id

                    # Update connection count in ChannelBandwidth
                    bw_record = session.query(ChannelBandwidth).filter(
                        ChannelBandwidth.channel_id == channel_id,
                        ChannelBandwidth.date == today
                    ).first()
                    if bw_record:
                        bw_record.connection_count += 1

                # Update watch_seconds for continuing connections
                for ip in continuing_clients:
                    conn_key = (channel_id, ip)
                    if conn_key in self._active_connections:
                        conn_id = self._active_connections[conn_key]
                        connection = session.query(UniqueClientConnection).filter(
                            UniqueClientConnection.id == conn_id
                        ).first()
                        if connection:
                            connection.watch_seconds += self.poll_interval

                # Handle clients that disconnected from this still-active channel
                last_clients = self._last_channel_clients.get(channel_id, set())
                current_clients = set(ch.get("client_ips", []))
                disconnected_clients = last_clients - current_clients
                for ip in disconnected_clients:
                    conn_key = (channel_id, ip)
                    if conn_key in self._active_connections:
                        conn_id = self._active_connections.pop(conn_key)
                        connection = session.query(UniqueClientConnection).filter(
                            UniqueClientConnection.id == conn_id
                        ).first()
                        if connection:
                            connection.disconnected_at = now

            session.commit()
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to update watch time: %s", e)
            session.rollback()
        finally:
            session.close()

    def _log_watch_stop_events(self, channel_ids: set[str]):
        """Log journal entries when channels stop being watched.

        bd-skqln.3 step (d): the channel-name fallback no longer reads
        from ``ChannelWatchStats`` (which is no longer written) — it
        falls through to a UUID-derived placeholder if neither the ECM
        channel map nor the in-memory cache has a name. The
        ``total_watch_seconds`` figure that was previously fetched from
        ``ChannelWatchStats`` is replaced by a session_telemetry
        aggregate using the same DISTINCT-by-(channel, observed_at)
        collapse the view (migration 0008) and popularity calculator
        use, so per-channel watch-time semantics stay consistent
        across surfaces.
        """
        from journal import log_entry

        session = get_session()
        try:
            for channel_id in channel_ids:
                # Get channel name - prefer ECM map, then cache, then placeholder.
                channel_name = (
                    self._ecm_channel_map.get(channel_id)
                    or self._channel_names.get(channel_id)
                    or f"Channel {channel_id[:8]}..."
                )

                # Derive lifetime watch_seconds for this channel from
                # session_telemetry. Same DISTINCT-by-(channel,
                # observed_at) collapse as the channel_watch_stats_v
                # view: a channel with N concurrent clients in one poll
                # contributes one interval, not N.
                per_poll = session.query(
                    SessionTelemetry.observed_at.label("observed_at"),
                    func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
                ).filter(
                    SessionTelemetry.channel_id == channel_id,
                ).group_by(
                    SessionTelemetry.observed_at,
                ).subquery()
                total_ms = session.query(
                    func.coalesce(func.sum(per_poll.c.poll_interval_ms), 0)
                ).scalar() or 0
                watch_time = int(total_ms) // 1000

                # Log journal entry for watch stop
                log_entry(
                    category="watch",
                    action_type="stop",
                    entity_name=channel_name,
                    description=f"Stopped watching {channel_name}",
                    user_initiated=False,
                    after_value={
                        "channel_id": channel_id,
                        "total_watch_seconds": watch_time,
                    },
                )

            logger.debug("[BANDWIDTH] Logged watch stop events for %s channels", len(channel_ids))
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to log watch stop events: %s", e)
        finally:
            session.close()

    def _close_client_connections(self, channel_ids: set[str]):
        """Mark all client connections as disconnected when channels stop (v0.11.0)."""
        session = get_session()
        try:
            now = datetime.now(get_user_timezone())
            closed_count = 0

            for channel_id in channel_ids:
                # Find all active connections for this channel
                keys_to_remove = [
                    key for key in self._active_connections
                    if key[0] == channel_id
                ]

                for key in keys_to_remove:
                    conn_id = self._active_connections.pop(key)
                    connection = session.query(UniqueClientConnection).filter(
                        UniqueClientConnection.id == conn_id
                    ).first()
                    if connection and connection.disconnected_at is None:
                        connection.disconnected_at = now
                        closed_count += 1

            session.commit()
            if closed_count > 0:
                logger.debug("[BANDWIDTH] Closed %s client connections for stopped channels", closed_count)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to close client connections: %s", e)
            session.rollback()
        finally:
            session.close()

    async def _resolve_provider_ids(
        self,
        channel_snapshot: list[dict],
    ) -> dict[str, ProviderResolution]:
        """Resolve each channel's active-stream identity for one poll.

        Bead: ``enhancedchannelmanager-skqln.14`` (initial), extended by
        ``enhancedchannelmanager-kbgey`` (URL fallback),
        ``enhancedchannelmanager-5g7kx`` (channel-streams URL-match
        fallback), ``enhancedchannelmanager-kh23e`` (stream identity
        capture), and ``enhancedchannelmanager-ox5q8`` (extraction of
        resolver core into ``resolve_active_channel_streams`` so the
        live ``/api/stats/channels`` Active Channels view shares one
        implementation with the polling tracker).

        Thin wrapper around the module-level
        ``resolve_active_channel_streams`` free function. The instance
        owns the cross-poll ``ChannelStreamsCache`` plus the
        ``_poll_count`` TTL anchor; the free function does the actual
        resolution work.

        Returns a ``{channel_uuid: ProviderResolution}`` map. Resolution
        failures land ``EMPTY_RESOLUTION`` (the all-None NamedTuple) —
        same row still writes, all three identity columns NULL. See
        ``resolve_active_channel_streams`` for the full path/failure
        taxonomy.
        """
        return await resolve_active_channel_streams(
            self.client,
            channel_snapshot,
            channel_streams_cache=self._provider_cache,
            poll_count=self._poll_count,
            emit_metrics=True,
        )

    # ------------------------------------------------------------------
    # Legacy method bodies removed in bd-ox5q8 — resolution logic now
    # lives in the module-level free functions above. The instance
    # methods below remain as back-compat shims so existing test names
    # (``tracker._resolve_via_channel_streams``,
    # ``tracker._get_cached_channel_streams``,
    # ``tracker._cache_channel_streams``,
    # ``tracker._log_provider_resolution_sli``) keep working unchanged.
    # ------------------------------------------------------------------

    async def _resolve_via_channel_streams(
        self,
        channel_uuid: str,
        active_url: Optional[str],
        per_poll_cache: dict[str, Optional[list[dict]]],
    ) -> Optional[ProviderResolution]:
        """Back-compat shim for the channel-streams fallback. Delegates
        to the module-level ``_resolve_via_channel_streams`` free
        function, passing the tracker's cross-poll cache + poll count.
        """
        return await _resolve_via_channel_streams(
            self.client,
            channel_uuid,
            active_url,
            per_poll_cache,
            self._provider_cache,
            self._poll_count,
        )

    def _get_cached_channel_streams(
        self, channel_uuid: str
    ) -> Optional[list[dict]]:
        """Back-compat shim. Delegates to the instance
        ``ChannelStreamsCache``.
        """
        return self._provider_cache.get(channel_uuid, self._poll_count)

    def _cache_channel_streams(
        self, channel_uuid: str, streams: list[dict]
    ) -> None:
        """Back-compat shim. Delegates to the instance
        ``ChannelStreamsCache``.
        """
        self._provider_cache.put(channel_uuid, streams, self._poll_count)

    def _log_provider_resolution_sli(
        self,
        resolved_count: int,
        unresolved_count: int,
    ) -> None:
        """Back-compat shim. Delegates to the module-level
        ``_log_provider_resolution_sli`` free function.
        """
        _log_provider_resolution_sli(resolved_count, unresolved_count)
    # Channel-health event types ECM tracks (bd-ov5vb). These are the four
    # Dispatcharr ts_proxy ``SystemEvent`` ``event_type`` values that
    # represent operationally-meaningful channel state — each maps to its
    # own per-poll counter on ``session_telemetry`` (migration 0013). Any
    # other event type Dispatcharr emits (client_connect, login_success,
    # epg_*, m3u_refresh, channel_start/stop, etc.) is noise for this
    # metric — they're either user-activity signals or routine lifecycle
    # events, not health signals — and are dropped at the type-filter
    # before the LRU dedup pays attention.
    _CHANNEL_EVENT_TYPES: ClassVar[tuple[str, ...]] = (
        "channel_buffering",
        "channel_reconnect",
        "channel_error",
        "stream_switch",
    )

    async def _collect_channel_events(
        self,
        channel_snapshot: list[dict],
    ) -> dict[str, dict[str, int]]:
        """Fetch the channel-health subset of Dispatcharr's system-events
        feed and bucket the deduped result by event_type for the current
        poll.

        Bead: ``enhancedchannelmanager-ov5vb`` (broadens
        ``enhancedchannelmanager-skqln.15``).

        Returns ``{channel_uuid: {event_type: count}}`` where ``event_type``
        is one of ``_CHANNEL_EVENT_TYPES``. Missing event types on a given
        channel are simply absent from the inner dict (the caller treats
        absence as zero, same shape contract the writer expects).

        Pre-bd-ov5vb history (why the broadening was necessary)
        --------------------------------------------------------
        The original ``_collect_buffer_events`` (skqln.15) passed
        ``event_type=buffering`` as a hard API-side filter, on the
        assumption that ``channel_buffering`` events were the
        operationally-meaningful health signal. Live verification on the
        PO's instance (2026-05-15) found that filter was returning zero
        on every poll because Dispatcharr's ``channel_buffering`` event
        only fires on rare ffmpeg-speed threshold trips — the events that
        actually represent channel-health problems are
        ``channel_reconnect`` (8 in the 100-event window),
        ``channel_error`` (4), and ``stream_switch`` (3). All three were
        being filtered out at the API call. This helper drops the
        ``event_type`` filter and buckets client-side so each type lands
        on its own column in ``session_telemetry``.

        Dispatcharr exposes recent events at
        ``GET /api/core/system-events/`` (wired on ``DispatcharrClient``
        as ``get_system_events``). The feed re-delivers recent events on
        every fetch — without dedup, the same event would be counted N
        times across successive polls. The ``self._seen_buffer_event_ids``
        LRU (name preserved from skqln.15 to avoid a churny rename across
        every test that introspects it; carries every channel-event id,
        not just buffering ones) is the cross-poll dedup state: an
        event's integer ``id`` is the dedup key, capped at
        ``self._seen_buffer_event_ids_cap`` with LRU eviction.

        Channel-id reconciliation: ``ChannelStats`` (``/proxy/ts/status``)
        and ``SystemEvent`` (``/api/core/system-events/``) can disagree on
        the ``channel_id`` field shape — the former is the Dispatcharr UUID
        string, the latter has historically been a numeric channel id.
        This helper normalizes both to ``str(channel_id)`` and tries match
        against the snapshot's channel_uuids; events whose channel cannot
        be mapped to a snapshot row are dropped (logged at WARNING).

        Type-filter ordering (cost discipline)
        ---------------------------------------
        We drop unwanted event types BEFORE the LRU lookup — see the
        ``event_type not in _CHANNEL_EVENT_TYPES`` short-circuit. The
        alternative (always LRU-check, then filter) would pollute the
        dedup set with the high-volume ``client_connect`` /
        ``login_success`` event ids and burn the bounded cap on values
        we have no interest in. The lookup itself is O(1), but the LRU
        churn is real — at the live cap of 10k and the observed
        ~70%-noise ratio on the PO's window, type-filter-first keeps
        the working set at the four health types rather than the full
        ~10 emitted types.

        Failure modes are non-fatal — the helper never raises:
        * ``get_system_events`` raises → ``{}`` returned + structured
          ``[STATS_V2] channel_event_fetch_failed`` log.
        * An event surfaces with no ``id`` → skipped (we cannot dedup it).
        * An event's channel doesn't match any snapshot row → dropped,
          logged once per occurrence as
          ``[STATS_V2] channel_event_unmapped_channel``.

        A per-poll SLI line is emitted at INFO:
        ``[STATS_V2] channel_event_ingest fetched=X deduped=Y
        attributed_buffer=A attributed_reconnect=B attributed_error=C
        attributed_switch=D``. The shape is stable so SRE's
        log-derived counter can grow per-type series without coupling
        a metric library into the ingest path.
        """
        # No channels active this poll → no rows will be written, so skip
        # the Dispatcharr round-trip entirely.
        if not channel_snapshot:
            return {}

        # Build the (str(channel_id) → channel_uuid) lookup so we can
        # reconcile Dispatcharr's two channel_id shapes (numeric on
        # system-events, UUID on /proxy/ts/status). Snapshot rows always
        # use UUID strings; we map numeric-id events through the
        # channel_number map if needed.
        snapshot_uuids = {entry["channel_uuid"] for entry in channel_snapshot}
        snapshot_uuids_str = {str(u) for u in snapshot_uuids}

        try:
            # bd-ov5vb: no ``event_type`` arg — broaden ingest so the
            # full channel-health event set lands in the response. The
            # client-side type-filter below buckets the result and
            # drops noise types.
            response = await self.client.get_system_events(
                limit=1000,
                offset=0,
            )
        except Exception as e:
            logger.warning(
                "[STATS_V2] channel_event_fetch_failed reason=request_raised error=%s",
                e,
            )
            self._log_channel_event_ingest_sli(
                fetched=0,
                deduped=0,
                attributed_by_type={t: 0 for t in self._CHANNEL_EVENT_TYPES},
            )
            return {}

        events = (response or {}).get("events", []) or []
        fetched = len(events)
        deduped_count = 0
        # ``counts_by_channel[channel_uuid][event_type] = count``. The
        # writer treats absent inner keys as zero so we don't pre-seed
        # zeros here.
        counts_by_channel: dict[str, dict[str, int]] = {}
        attributed_by_type: dict[str, int] = {t: 0 for t in self._CHANNEL_EVENT_TYPES}

        for event in events:
            event_type = event.get("event_type")
            if event_type not in self._CHANNEL_EVENT_TYPES:
                # Noise — login_success, client_connect, epg_*, m3u_refresh,
                # channel_start/stop, etc. Drop BEFORE LRU lookup so the
                # dedup set stays focused on the four health types.
                continue

            event_id = event.get("id")
            if event_id is None:
                # Cannot dedup without a stable id — skip rather than
                # double-count on the next poll.
                logger.warning(
                    "[STATS_V2] channel_event_skipped reason=no_event_id event_type=%s",
                    event_type,
                )
                continue
            event_id = int(event_id)
            if event_id in self._seen_buffer_event_ids:
                # Already counted in an earlier poll — bump it to MRU so the
                # LRU eviction prefers genuinely stale entries.
                self._seen_buffer_event_ids.move_to_end(event_id)
                deduped_count += 1
                continue

            event_channel = event.get("channel_id")
            event_channel_str = str(event_channel) if event_channel is not None else None
            if event_channel_str not in snapshot_uuids_str:
                # Dispatcharr surfaced an event for a channel that's not in
                # our snapshot — either the channel stopped between the
                # stats and system-events fetches, or the channel_id shape
                # mismatch documented above. Drop it; record the dedup
                # entry so we don't keep re-evaluating the same id every
                # poll.
                self._seen_buffer_event_ids[event_id] = None
                self._evict_buffer_event_ids_if_over_cap()
                logger.warning(
                    "[STATS_V2] channel_event_unmapped_channel event_id=%s "
                    "event_type=%s channel_id=%s",
                    event_id,
                    event_type,
                    event_channel,
                )
                continue

            # Attribute the event to the snapshot's UUID-keyed map. Reverse
            # the str() lookup back to the original UUID value so the
            # caller's keys match.
            attributed_uuid = next(
                (u for u in snapshot_uuids if str(u) == event_channel_str),
                event_channel_str,
            )
            per_type = counts_by_channel.setdefault(attributed_uuid, {})
            per_type[event_type] = per_type.get(event_type, 0) + 1
            attributed_by_type[event_type] += 1
            self._seen_buffer_event_ids[event_id] = None

        # Evict any LRU overflow once per poll — cheaper than per-event.
        self._evict_buffer_event_ids_if_over_cap()

        self._log_channel_event_ingest_sli(
            fetched=fetched,
            deduped=deduped_count,
            attributed_by_type=attributed_by_type,
        )
        return counts_by_channel

    def _evict_buffer_event_ids_if_over_cap(self) -> None:
        """LRU eviction for the channel-event dedup set. Keeps memory
        bounded; entries are popped from the front (oldest insertion).

        Name preserved from the skqln.15 era (when only buffer events
        were ingested) to avoid a churny rename across every test that
        introspects the attribute; the set is now keyed by every
        channel-health event type tracked, not just buffering.
        """
        while len(self._seen_buffer_event_ids) > self._seen_buffer_event_ids_cap:
            self._seen_buffer_event_ids.popitem(last=False)

    def _log_channel_event_ingest_sli(
        self,
        *,
        fetched: int,
        deduped: int,
        attributed_by_type: dict[str, int],
    ) -> None:
        """Emit the per-poll channel-event-ingest SLI line (bd-ov5vb).

        Format::

            [STATS_V2] channel_event_ingest fetched=X deduped=Y
            attributed_buffer=A attributed_reconnect=B
            attributed_error=C attributed_switch=D

        Stable substring shape; SRE's log-derived counter can grow
        per-type series without coupling a metric library into the
        ingest path. Pre-bd-ov5vb the line was
        ``buffer_event_ingest fetched=X deduped=Y attributed=Z`` —
        operators reading the new line see at a glance that the four
        event types are now distinct, and per-type zeros honestly
        reflect "this install's Dispatcharr did not emit any of that
        type this poll" rather than masking the gap.
        """
        logger.info(
            "[STATS_V2] channel_event_ingest fetched=%s deduped=%s "
            "attributed_buffer=%s attributed_reconnect=%s "
            "attributed_error=%s attributed_switch=%s",
            fetched,
            deduped,
            attributed_by_type.get("channel_buffering", 0),
            attributed_by_type.get("channel_reconnect", 0),
            attributed_by_type.get("channel_error", 0),
            attributed_by_type.get("stream_switch", 0),
        )

    async def _resolve_attributions(
        self,
        telemetry_channel_snapshot: list[dict],
        provider_by_channel: dict[str, ProviderResolution],
    ) -> dict[tuple[str, str], AttributionResult]:
        """Cross-reference active (channel, ip) pairs against all three media sources.

        bd-r5f0c.4 (epic bd-r5f0c) — extends bd-gih6d (Emby-only) to
        Plex + Jellyfin. Builds a sparse map of
        ``(channel_uuid, client_ip) → AttributionResult`` for every
        active viewing connection this poll. The result populates
        ``session_telemetry.{emby,plex,jellyfin}_user_id`` /
        ``..._user_name`` columns in the downstream
        ``_write_session_telemetry`` call — each source's columns get
        populated only when THAT source matched the (channel, ip) pair;
        all six default to NULL otherwise.

        Concurrency + failure isolation
        --------------------------------
        Each source's per-(channel, ip) resolution loop is wrapped in
        :func:`asyncio.wait_for` against
        ``_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS``, then the three loops
        run concurrently via :func:`asyncio.gather` (``return_exceptions=
        True`` so one source's timeout / unexpected raise does NOT
        poison the others' results). SRE failure-isolation requirement:
        a slow Plex server cannot block Jellyfin attribution past one
        timeout budget; an Emby cache fault cannot prevent the writer
        from getting Plex matches.

        Settings gate
        -------------
        Each source's coroutine reads its own ``<source>_enabled`` flag
        and short-circuits to an empty dict if the source is disabled.
        On a single-source install (the common case today), only one
        source's resolver actually walks the snapshot — the other two
        coroutines return an empty dict in microseconds without
        touching their resolvers.

        Per-source WARN rate-limiting
        -----------------------------
        Each source has its own module-level WARN clock
        (:func:`_log_emby_resolver_failure` /
        :func:`_log_plex_resolver_failure` /
        :func:`_log_jellyfin_resolver_failure`) so a sustained Plex
        outage cannot silence Emby or Jellyfin WARNs — failure
        signals stay independent across sources.

        Args:
            telemetry_channel_snapshot: Per-channel snapshot the
                ``_collect_stats`` loop already built — ``channel_uuid``
                + ``client_ips`` + ``channel_name`` + ``channel_number``
                per entry are the fields the resolvers consume.
            provider_by_channel: Same map ``_write_session_telemetry``
                already receives; used here to read the resolved
                ``stream_name`` so each resolver can match against its
                source's now-playing surface.

        Returns:
            ``{(channel_uuid, client_ip): AttributionResult}``. Sparse —
            entries are only present for pairs at least one source
            attributed. Empty dict when all sources are disabled, when
            no pair resolved, or when every resolver call failed.
        """
        try:
            from config import get_settings
            settings = get_settings()
        except Exception as exc:
            # Settings access raise is exotic but defensible — config
            # is loaded at import in normal flow. Fail soft so the
            # writer still runs. Log against each source so the operator
            # sees the failure in whatever source's monitoring they
            # have wired up.
            _log_emby_resolver_failure(exc)
            _log_plex_resolver_failure(exc)
            _log_jellyfin_resolver_failure(exc)
            return {}

        emby_enabled = bool(getattr(settings, "emby_enabled", False))
        plex_enabled = bool(getattr(settings, "plex_enabled", False))
        jellyfin_enabled = bool(getattr(settings, "jellyfin_enabled", False))

        # Short-circuit: every source disabled (the common posture on
        # installs without any media server configured). Skips three
        # coroutine creations + the gather machinery on the hot path.
        if not (emby_enabled or plex_enabled or jellyfin_enabled):
            return {}

        async def _with_timeout(
            coro,
            source: str,
            log_failure,
        ) -> dict[tuple[str, str], Any]:
            """Wrap one source's coroutine in a per-source timeout.

            Returns an empty dict on TimeoutError or any unexpected
            exception so ``gather`` can compose the source results
            without inheriting a per-source failure into the merged
            output.
            """
            try:
                return await asyncio.wait_for(
                    coro, timeout=_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # Surface the timeout against the source's own WARN
                # clock so SRE can see one source got slow without the
                # other sources' clocks being affected. Synthesize a
                # marker exception so the existing log helper's
                # ``%s`` formatting reads naturally.
                log_failure(asyncio.TimeoutError(
                    f"{source} resolver exceeded "
                    f"{_RESOLVER_PER_SOURCE_TIMEOUT_SECONDS:.1f}s budget"
                ))
                return {}
            except Exception as exc:  # noqa: BLE001 — top-level failure isolation
                log_failure(exc)
                return {}

        emby_task = _with_timeout(
            self._resolve_emby_for_clients(
                telemetry_channel_snapshot, provider_by_channel,
                enabled=emby_enabled,
            ),
            "emby",
            _log_emby_resolver_failure,
        )
        plex_task = _with_timeout(
            self._resolve_plex_for_clients(
                telemetry_channel_snapshot, provider_by_channel,
                enabled=plex_enabled,
            ),
            "plex",
            _log_plex_resolver_failure,
        )
        jellyfin_task = _with_timeout(
            self._resolve_jellyfin_for_clients(
                telemetry_channel_snapshot, provider_by_channel,
                enabled=jellyfin_enabled,
            ),
            "jellyfin",
            _log_jellyfin_resolver_failure,
        )

        emby_map, plex_map, jellyfin_map = await asyncio.gather(
            emby_task, plex_task, jellyfin_task,
        )

        # Merge the three sparse maps into a single result per (channel,
        # ip). Each per-source map value is now a list of attributions
        # (bd-r5f0c.9 multi-viewer) — sorted most-recent-first. We
        # populate BOTH the legacy singular fields (position 0 = most-
        # recent viewer, back-compat with Stats v2 aggregations + the
        # pre-W5 frontend) AND the full ``*_viewers`` list field (W5+
        # consumers render every viewer). Sources that didn't match
        # leave their slot as the dataclass default (None / empty list).
        merged: dict[tuple[str, str], AttributionResult] = {}
        all_keys = set(emby_map.keys()) | set(plex_map.keys()) | set(jellyfin_map.keys())
        for key in all_keys:
            emby_list = emby_map.get(key) or []
            plex_list = plex_map.get(key) or []
            jellyfin_list = jellyfin_map.get(key) or []

            # Most-recent viewer per source (position 0 of the sorted
            # list) → legacy singular columns. Empty list → None.
            emby_top = emby_list[0] if emby_list else None
            plex_top = plex_list[0] if plex_list else None
            jellyfin_top = jellyfin_list[0] if jellyfin_list else None

            merged[key] = AttributionResult(
                emby_user_id=emby_top.user_id if emby_top else None,
                emby_user_name=emby_top.user_name if emby_top else None,
                # Plex resolver currently surfaces only the user_name.
                # PlexAttribution.user_id is None today (no stable
                # upstream identifier on /status/sessions); the column
                # tolerates this by being nullable.
                plex_user_id=plex_top.user_id if plex_top else None,
                plex_user_name=plex_top.user_name if plex_top else None,
                jellyfin_user_id=jellyfin_top.user_id if jellyfin_top else None,
                jellyfin_user_name=jellyfin_top.user_name if jellyfin_top else None,
                emby_viewers=[
                    {"user_id": a.user_id, "user_name": a.user_name}
                    for a in emby_list
                ],
                plex_viewers=[
                    {"user_id": a.user_id, "user_name": a.user_name}
                    for a in plex_list
                ],
                jellyfin_viewers=[
                    {"user_id": a.user_id, "user_name": a.user_name}
                    for a in jellyfin_list
                ],
            )
        return merged

    async def _resolve_emby_for_clients(
        self,
        telemetry_channel_snapshot: list[dict],
        provider_by_channel: dict[str, ProviderResolution],
        *,
        enabled: bool,
    ) -> dict[tuple[str, str], list[EmbyAttribution]]:
        """Per-source Emby resolution loop (bd-r5f0c.4 + bd-r5f0c.9 multi-viewer).

        bd-r5f0c.9: per (channel_uuid, client_ip), capture the FULL list
        of matched Emby viewers (sorted ``last_activity_date``
        descending) rather than just the most-recent winner. Multi-viewer
        scenarios (N upstream users on the same channel via the same
        Emby server proxy) are now visible end-to-end.

        Back-compat call-site discipline: this loop calls BOTH
        :func:`resolve_emby_user` (singular, the bd-gih6d/bd-r5f0c.4
        mock target the existing
        ``test_bandwidth_tracker_emby.py`` regression suite asserts
        against) AND :func:`resolve_emby_users` (plural,
        bd-r5f0c.9 multi-viewer surface). Whichever returns the most
        viewers wins. In production both go to the same real resolver
        chain and the plural carries everything the singular does (the
        singular wrapper internally is ``plural[0]``); in legacy tests
        that mock only the singular function, the plural call hits the
        real un-mocked resolver and returns empty, so we fall back to
        wrapping the singular result into a 1-element list. The
        production cost is one extra microsecond-scale function call per
        (channel, ip); the back-compat benefit is the full bd-gih6d
        Emby regression suite continues to verify the single-viewer
        contract without test-file edits.
        """
        if not enabled:
            return {}
        attributions: dict[tuple[str, str], list[EmbyAttribution]] = {}
        for entry in telemetry_channel_snapshot:
            channel_uuid = entry["channel_uuid"]
            client_ips = entry.get("client_ips") or []
            if not client_ips:
                continue
            resolution = provider_by_channel.get(channel_uuid)
            stream_name = resolution.stream_name if resolution else None
            channel_name = entry.get("channel_name")
            channel_number = entry.get("channel_number")
            if not stream_name and not channel_name and channel_number is None:
                continue
            for ip in client_ips:
                viewers: list[EmbyAttribution] = []
                # Singular first — this is the bd-gih6d/bd-r5f0c.4
                # legacy mock seam. Existing tests patch this function
                # name; in production, it returns plural[0].
                try:
                    single = await resolve_emby_user(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    _log_emby_resolver_failure(exc)
                    # Continue to the next ip — one failure should not
                    # poison the rest of the poll's attributions.
                    continue
                # Plural — the bd-r5f0c.9 multi-viewer source. In
                # production, this is the authoritative full list. In
                # legacy single-source-mocked tests, plural is
                # unmocked and returns [] (real resolver short-circuits
                # because cache isn't mocked); we fall back to wrapping
                # the singular result.
                try:
                    plural = await resolve_emby_users(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    # Defensive: if plural raised but singular returned
                    # something useful, use the singular result. The
                    # resolver itself never raises in production
                    # (defensive ``except`` guards inside) so this is
                    # paranoia for edge-case test mocks.
                    _log_emby_resolver_failure(exc)
                    plural = []
                if plural:
                    viewers = list(plural)
                elif single is not None:
                    # Back-compat path: legacy single-viewer test mock
                    # provided a singular result but no plural mock.
                    viewers = [single]
                if viewers:
                    attributions[(channel_uuid, ip)] = viewers
        return attributions

    async def _resolve_plex_for_clients(
        self,
        telemetry_channel_snapshot: list[dict],
        provider_by_channel: dict[str, ProviderResolution],
        *,
        enabled: bool,
    ) -> dict[tuple[str, str], list[PlexAttribution]]:
        """Per-source Plex resolution loop (bd-r5f0c.4 + bd-r5f0c.9 multi-viewer).

        Same dual-call back-compat discipline as
        :meth:`_resolve_emby_for_clients` — call singular
        :func:`resolve_plex_user` first (bd-r5f0c.4 mock target,
        returns ``str | None``) AND plural :func:`resolve_plex_users`
        (bd-r5f0c.9 mock target, returns ``list[PlexAttribution]``).
        Whichever returns the most viewers wins. Legacy test mocks
        only the singular; the plural is unmocked and returns [] in
        those tests, so we wrap the singular ``str`` into a
        ``[PlexAttribution(user_name=..., user_id=None)]`` 1-element
        list.
        """
        if not enabled:
            return {}
        attributions: dict[tuple[str, str], list[PlexAttribution]] = {}
        for entry in telemetry_channel_snapshot:
            channel_uuid = entry["channel_uuid"]
            client_ips = entry.get("client_ips") or []
            if not client_ips:
                continue
            resolution = provider_by_channel.get(channel_uuid)
            stream_name = resolution.stream_name if resolution else None
            channel_name = entry.get("channel_name")
            channel_number = entry.get("channel_number")
            if not stream_name and not channel_name and channel_number is None:
                continue
            for ip in client_ips:
                viewers: list[PlexAttribution] = []
                try:
                    single_name = await resolve_plex_user(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    _log_plex_resolver_failure(exc)
                    continue
                try:
                    plural = await resolve_plex_users(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    _log_plex_resolver_failure(exc)
                    plural = []
                if plural:
                    viewers = list(plural)
                elif single_name is not None:
                    viewers = [
                        PlexAttribution(user_name=single_name, user_id=None)
                    ]
                if viewers:
                    attributions[(channel_uuid, ip)] = viewers
        return attributions

    async def _resolve_jellyfin_for_clients(
        self,
        telemetry_channel_snapshot: list[dict],
        provider_by_channel: dict[str, ProviderResolution],
        *,
        enabled: bool,
    ) -> dict[tuple[str, str], list[JellyfinAttribution]]:
        """Per-source Jellyfin resolution loop (bd-r5f0c.4 + bd-r5f0c.9 multi-viewer).

        Same dual-call back-compat discipline as
        :meth:`_resolve_emby_for_clients` and
        :meth:`_resolve_plex_for_clients`.
        """
        if not enabled:
            return {}
        attributions: dict[tuple[str, str], list[JellyfinAttribution]] = {}
        for entry in telemetry_channel_snapshot:
            channel_uuid = entry["channel_uuid"]
            client_ips = entry.get("client_ips") or []
            if not client_ips:
                continue
            resolution = provider_by_channel.get(channel_uuid)
            stream_name = resolution.stream_name if resolution else None
            channel_name = entry.get("channel_name")
            channel_number = entry.get("channel_number")
            if not stream_name and not channel_name and channel_number is None:
                continue
            for ip in client_ips:
                viewers: list[JellyfinAttribution] = []
                try:
                    single = await resolve_jellyfin_user(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    _log_jellyfin_resolver_failure(exc)
                    continue
                try:
                    plural = await resolve_jellyfin_users(
                        ip,
                        stream_name or "",
                        ecm_channel_name=channel_name,
                        ecm_channel_number=channel_number,
                    )
                except Exception as exc:
                    _log_jellyfin_resolver_failure(exc)
                    plural = []
                if plural:
                    viewers = list(plural)
                elif single is not None:
                    viewers = [single]
                if viewers:
                    attributions[(channel_uuid, ip)] = viewers
        return attributions

    async def _resolve_emby_attributions(
        self,
        telemetry_channel_snapshot: list[dict],
        provider_by_channel: dict[str, ProviderResolution],
    ) -> dict[tuple[str, str], EmbyAttribution]:
        """Back-compat shim — Emby-only single-viewer attribution map.

        bd-gih6d (Emby-only) → bd-r5f0c.4 (multi-source) → bd-r5f0c.9
        (multi-viewer). The current ``_collect_stats`` hot path uses
        :meth:`_resolve_attributions`; this wrapper is retained ONLY so
        the existing Emby regression test suite
        (``tests/unit/test_bandwidth_tracker_emby.py``) continues to
        verify the original single-viewer Emby-attribution contract
        end-to-end without rewriting every assertion against the new
        multi-viewer shape.

        Reads ``settings.emby_enabled`` and delegates to
        :meth:`_resolve_emby_for_clients` (which now returns lists of
        :class:`EmbyAttribution` post-bd-r5f0c.9). This wrapper
        flattens each list to its position-0 element so the legacy
        return shape ``{(channel, ip): EmbyAttribution}`` is preserved.
        Production code paths SHOULD prefer :meth:`_resolve_attributions`
        which carries the full viewer list.
        """
        try:
            from config import get_settings
            settings = get_settings()
        except Exception as exc:
            _log_emby_resolver_failure(exc)
            return {}
        viewer_lists = await self._resolve_emby_for_clients(
            telemetry_channel_snapshot,
            provider_by_channel,
            enabled=bool(getattr(settings, "emby_enabled", False)),
        )
        # Flatten to position 0 (most-recent viewer) for the legacy
        # single-viewer contract the bd-gih6d test suite asserts against.
        return {
            key: viewers[0]
            for key, viewers in viewer_lists.items()
            if viewers
        }

    def _record_session_telemetry_metrics(
        self,
        *,
        result: str,
        duration_seconds: float,
        rows_written: int,
        rows_excluded: int = 0,
    ) -> None:
        """Emit the Stats v2 session_telemetry write metrics (bd-skqln.12).

        Wrapped so a metric-side failure (registry not installed in some
        edge-case test, prometheus-client missing) can never propagate into
        the observation path it is instrumenting. The exception swallow
        is deliberate — observability must not break the writer.

        bd-uqbob: ``rows_excluded`` is the count of rows that
        ``_write_session_telemetry`` filtered out via
        ``ECM_TELEMETRY_EXCLUDE_USERS`` this poll. Emitted as a single
        ``.inc(n)`` against the ``session_telemetry_rows_excluded_total``
        counter (``reason=excluded_user``) so the suppression rate is
        observable without leaking the excluded identity into the metric
        label space. Default of 0 keeps the call ergonomics for older
        test seams that don't pass the kwarg.
        """
        try:
            from observability import get_metric

            get_metric("session_telemetry_writes_total").labels(
                result=result
            ).inc()
            get_metric("session_telemetry_write_duration_seconds").observe(
                max(0.0, float(duration_seconds))
            )
            # bd-ae58c (Option B): do NOT set ecm_session_telemetry_row_count
            # here. That gauge has one owner: the nightly rollup task sets it
            # to the post-prune table total after each successful run. The
            # per-poll batch size is observable via the
            # ecm_session_telemetry_writes_total counter rate — callers can
            # derive avg-rows-per-poll = writes_total rate / poll rate.
            # Having two writers with divergent semantics (batch size ~10 vs.
            # table total ~250k) caused the gauge to oscillate wildly and
            # trip the ECMStatsRowCountGrowthAnomaly alert spuriously.
            if rows_excluded > 0:
                get_metric("session_telemetry_rows_excluded_total").labels(
                    reason="excluded_user"
                ).inc(int(rows_excluded))
        except Exception:  # pragma: no cover — never break the writer
            logger.debug(
                "[STATS_V2] failed to emit session_telemetry write metrics",
                exc_info=True,
            )

    def _write_session_telemetry(
        self,
        channel_snapshot: list[dict],
        observed_at_ms: int,
        provider_by_channel: Optional[dict[str, ProviderResolution]] = None,
        channel_events_by_channel: Optional[dict[str, dict[str, int]]] = None,
        *,
        dispatcharr_user_map: Optional[dict[str, str]] = None,
        exclude_user_tokens: Optional[frozenset[str]] = None,
        emby_attributions: Optional[dict[tuple[str, str], EmbyAttribution]] = None,
        attributions: Optional[dict[tuple[str, str], AttributionResult]] = None,
    ) -> None:
        """Write one row per active viewing connection into ``session_telemetry``.

        Stats v2 additive write path — bead ``enhancedchannelmanager-skqln.3``
        step (a). Called from ``_collect_stats`` AFTER the four legacy writes
        and ONLY when ``ECM_SESSION_TELEMETRY_WRITE_ENABLED`` is on. No
        consumers of ``session_telemetry`` exist yet, so this is observation-
        only — the row shape is what later beads (skqln.5 read API, skqln.14
        provider resolver, skqln.15 buffer ingest) will populate further.

        Row population (step (a), conservative):

        * ``session_id`` — synthesized from the active-connection id we track
          in ``self._active_connections``; namespaced ``conn-<id>`` so it does
          not collide with future session-id sources. Stable for the life of
          the connection.
        * ``observed_at`` — ms since epoch stamped at the start of the poll
          (passed in so all rows in this cycle share the same value).
        * ``user_id`` — from the per-channel ``client_user_map`` if present;
          NULL when Dispatcharr did not surface a user id for that ip.
        * ``provider_id`` — populated from ``provider_by_channel`` (built
          upstream by ``_resolve_provider_ids``, bd-skqln.14). NULL when the
          resolver couldn't map the active stream to an M3U account.
        * ``stream_id`` + ``stream_name`` — populated from the SAME
          ``ProviderResolution`` NamedTuple the resolver returns
          (bd-kh23e). Both NULL when the resolver couldn't identify the
          active stream. ``stream_name`` may be NULL even when
          ``stream_id`` is present (Dispatcharr record had no ``name``
          field). The frontend renders these as
          ``[<provider_name>] - <stream_name>`` with provider name
          side-loaded from the M3U accounts map (PO 2026-05-14).
        * ``channel_id`` — Dispatcharr channel UUID (``String(64)``). Same
          shape the snapshot loop in ``_collect_stats`` already keys on, and
          matches every other channel-keyed table in the schema
          (``channel_watch_stats`` etc.). Migration 0007 corrected the
          column type from INTEGER to VARCHAR(64) NOT NULL after the
          step-(a) commit was first drafted with NULL writes; see the
          bead body for the schema-mismatch correction.
        * ``bytes_delta`` — per-channel byte delta divided equally across
          active clients (integer floor; remainder dropped). Acceptable for
          observation-only; refined when consumers exist.
        * ``buffer_event_count`` / ``reconnect_event_count`` /
          ``error_event_count`` / ``switch_event_count`` — deduplicated
          per-event-type counts surfaced for this channel during this
          poll (bd-ov5vb, broadens bd-skqln.15). Channel-health events
          are channel-level (a stall, reconnect, or stream-switch on
          the upstream pipeline affects every viewer), so each
          per-type count attributes to EXACTLY ONE row per
          ``(channel_uuid, observed_at)`` bucket — the first row
          emitted for that channel, with sibling rows writing 0. This
          keeps ``SUM(<column>) GROUP BY provider, time_bucket``
          well-defined for the Providers panel rollups without
          per-client double-counting. ``buffer_event_count`` is
          preserved for back-compat with the pre-0012 read paths;
          ``channel_buffering`` events are rare on real installs (only
          fires on ffmpeg-speed threshold trips) so this column
          typically reads zero — the other three carry the
          operationally-meaningful signal.
        * ``poll_interval_ms`` — ``self.poll_interval`` (seconds) × 1000.

        The write is wrapped in a defensive try/except so any failure here
        cannot disturb the legacy writes that already committed. This is
        the keystone of "single-write refactor that can't break what
        already works" — step (a) is dual-write under a flag, but ONLY for
        the duration needed to prove the new path; legacy-write removal is
        step (d) in this bead, behind a separate commit and PR.

        bd-uqbob (operator-facing exclude filter)
        -----------------------------------------
        When ``ECM_TELEMETRY_EXCLUDE_USERS`` is configured, the caller in
        ``_collect_stats`` parses the env var once and passes the already-
        lower-cased token set as ``exclude_user_tokens``. The caller also
        passes the per-poll Dispatcharr ``user_id → username`` map as
        ``dispatcharr_user_map`` so the per-row filter does not pay for a
        second Dispatcharr round-trip. Both kwargs default to empty for
        backward compatibility with the existing test seam — when the
        token set is empty the filter is short-circuited before any per-
        row work.

        The filter runs AFTER ``_coerce_session_user_id`` so anonymous
        sentinels (``0`` / ``"0"`` → ``None``) never trigger a numeric
        token match. The match is OR-shaped: either the coerced user_id
        (string-compared against the token set) OR the resolved username
        (case-insensitive) is sufficient to drop the row. Skipped rows
        are NOT silently dropped — they increment the
        ``session_telemetry_rows_excluded_total`` counter (labeled
        ``reason=excluded_user``) so SRE can see the suppression rate
        without leaking the excluded identity into the metric label space.
        """
        if provider_by_channel is None:
            provider_by_channel = {}
        if channel_events_by_channel is None:
            channel_events_by_channel = {}
        if dispatcharr_user_map is None:
            dispatcharr_user_map = {}
        if exclude_user_tokens is None:
            exclude_user_tokens = frozenset()
        if emby_attributions is None:
            # bd-gih6d: empty map = no Emby attribution this poll
            # (Emby disabled, no matches, or every resolver call failed).
            # Default to empty so the older test seams that don't pass
            # this kwarg continue working with NULL emby columns.
            emby_attributions = {}
        if attributions is None:
            # bd-r5f0c.4: empty map = no multi-source attribution this
            # poll (every source disabled, no match across any source,
            # or every resolver call failed). Default to empty so the
            # older test seams that only pass ``emby_attributions``
            # continue to work — the per-row merge below uses
            # ``attributions`` as the primary lookup and falls back to
            # ``emby_attributions`` when the merged map is missing a
            # key, so an Emby-only caller still populates the Emby
            # columns the old way.
            attributions = {}
        # bd-skqln.12: time the entire write attempt and record success /
        # failure on the way out. ``rows_written`` is hoisted here so it is
        # visible to the metric-emission block in the ``finally`` (it is 0
        # if the helper raises before the inner counter increments).
        write_start = time.perf_counter()
        rows_written = 0
        # bd-uqbob: hoisted alongside ``rows_written`` so the metric
        # emission in the outer ``finally`` can read it even when the
        # inner try raises before the counter is bumped. Default of 0
        # means a failed write reports zero exclusions, which is the
        # truthful posture — we cannot prove any rows were filtered if
        # the per-row loop never completed.
        rows_excluded = 0
        write_result = "failure"
        try:
            poll_interval_ms = max(int(self.poll_interval * 1000), 0)
            session = get_session()
            try:
                # Track which channels have already received their per-type
                # channel-event-count attribution this poll. Channel-level
                # counts land on the FIRST emitted row per channel (sorted
                # by client_ip for determinism); subsequent rows for the
                # same channel write 0 across every per-type column so SUM
                # aggregates do not double-count. (bd-ov5vb: single flag
                # covers all four per-type columns because the attribution
                # rule is the same — first row wins for buffer +
                # reconnect + error + switch alike.)
                channel_events_attributed: set[str] = set()
                # Channels with any per-type events but no eligible row
                # (no active client connection recorded) — counts are
                # dropped and logged below.
                channels_with_events = set(channel_events_by_channel.keys())
                for entry in channel_snapshot:
                    channel_uuid = entry["channel_uuid"]
                    client_ips = entry["client_ips"]
                    client_user_map = entry["client_user_map"]
                    channel_bytes_delta = max(int(entry["channel_bytes_delta"]), 0)
                    # ``ProviderResolution`` NamedTuple carries id + name +
                    # provider together (bd-kh23e). Falls back to the
                    # all-None sentinel for channels the resolver didn't
                    # visit (defensive — should not happen in practice
                    # because the resolver returns one entry per snapshot
                    # entry).
                    resolution = provider_by_channel.get(
                        channel_uuid, EMPTY_RESOLUTION
                    )
                    provider_id = resolution.provider_id
                    stream_id = resolution.stream_id
                    stream_name = resolution.stream_name

                    # No active clients on this channel this poll → nothing
                    # to attribute to a session.
                    if not client_ips:
                        continue

                    per_client_bytes = channel_bytes_delta // len(client_ips)
                    # Deterministic emission order so the buffer-event count
                    # consistently lands on the same row across runs (test
                    # parity + reproducible aggregates).
                    sorted_ips = sorted(client_ips)

                    for ip in sorted_ips:
                        conn_key = (channel_uuid, ip)
                        conn_id = self._active_connections.get(conn_key)
                        # Tracker has not yet recorded a connection row for
                        # this (channel, ip) — happens on the first poll of
                        # a brand-new viewer because _update_watch_counts
                        # has already run by the time we reach here, so this
                        # should be rare. Skip rather than synthesize a
                        # session id we cannot correlate later.
                        if conn_id is None:
                            continue

                        # bd-gbxmj: coerce the raw Dispatcharr user_id
                        # through the FK-safety helper. Anonymous sentinels
                        # ("0"/0/""/None) become NULL; positive ints (or
                        # int-parseable strings) pass through. Without this
                        # the FK to users.id raises IntegrityError at
                        # session.commit() and rolls back the WHOLE batch.
                        coerced_user_id = _coerce_session_user_id(
                            client_user_map.get(ip)
                        )

                        # bd-gsn3r: resolve the Dispatcharr-side username
                        # for this row from the caller-supplied
                        # ``dispatcharr_user_map`` and persist it as
                        # ``dispatcharr_username`` (denormalized at write
                        # time so the read APIs never join against ECM's
                        # local ``users`` table — that join was the
                        # namespace-collision bug bd-uqbob worked around
                        # with the env-var exclude filter; this is the
                        # architectural fix). NULL when the raw
                        # ``user_id`` is anonymous (no map key) or when
                        # the per-poll Dispatcharr ``get_users()`` call
                        # earlier this cycle failed (the caller passes
                        # an empty map in that case).
                        raw_uid = client_user_map.get(ip)
                        disp_username = (
                            dispatcharr_user_map.get(str(raw_uid))
                            if raw_uid is not None
                            else None
                        ) or None  # Coerce empty string back to None.

                        # bd-uqbob: drop rows attributed to operator-
                        # configured excluded users. The original purpose
                        # — dropping ECM admin/API account rows that
                        # collided with Dispatcharr viewer IDs in the
                        # dropped FK namespace — is moot post-bd-gsn3r:
                        # the dispatcharr_username column lets the read
                        # APIs surface real Dispatcharr viewers without
                        # touching ECM's users table. The env var stays
                        # in place (no harm) for operators who want to
                        # exclude a specific Dispatcharr viewer for any
                        # other reason. An empty token set short-
                        # circuits before any per-row work.
                        if exclude_user_tokens:
                            if _is_excluded_telemetry_user(
                                coerced_user_id,
                                disp_username,
                                exclude_user_tokens,
                            ):
                                rows_excluded += 1
                                # Per-row debug line for operator-facing
                                # traceability. Lazy-formatted so the
                                # interpolation cost is only paid when
                                # debug logging is enabled.
                                logger.debug(
                                    "[BANDWIDTH] Skipped telemetry write "
                                    "for excluded user user=%s reason=%s",
                                    disp_username or coerced_user_id,
                                    "excluded_user",
                                )
                                continue

                        # Attribute the channel's per-type event counts
                        # to the FIRST eligible row only (bd-ov5vb,
                        # broadens bd-skqln.15). Sibling rows write 0
                        # across every per-type column. (bd-uqbob:
                        # excluded rows above do NOT consume the
                        # attribution slot — counts land on the first
                        # row that survives the filter.) The inner
                        # ``per_type`` dict carries only the event types
                        # that fired for this channel this poll; the
                        # ``.get(<type>, 0)`` calls honestly write 0 for
                        # absent types.
                        if channel_uuid not in channel_events_attributed:
                            per_type = channel_events_by_channel.get(
                                channel_uuid, {}
                            )
                            row_buffer_count = per_type.get("channel_buffering", 0)
                            row_reconnect_count = per_type.get("channel_reconnect", 0)
                            row_error_count = per_type.get("channel_error", 0)
                            row_switch_count = per_type.get("stream_switch", 0)
                            channel_events_attributed.add(channel_uuid)
                        else:
                            row_buffer_count = 0
                            row_reconnect_count = 0
                            row_error_count = 0
                            row_switch_count = 0
                        # bd-r5f0c.4 (extends bd-gih6d): look up the
                        # per-(channel, ip) attribution the caller
                        # resolved in ``_resolve_attributions`` for this
                        # poll cycle. Sparse map — most pairs are NOT
                        # media-server-mediated so the lookup returns
                        # ``None``, which we collapse to NULL across
                        # every source's column pair. All six fields
                        # ride together on the row so any future split
                        # of the model into per-source tables can
                        # migrate this writer in one place.
                        #
                        # Back-compat: if the caller only passed the
                        # legacy ``emby_attributions`` kwarg (older test
                        # seams pre bd-r5f0c.4), the merged
                        # ``attributions`` map will be missing this key
                        # — fall back to the Emby-only lookup so the
                        # legacy contract continues working.
                        attribution = attributions.get((channel_uuid, ip))
                        if attribution is None:
                            emby_attr_legacy = emby_attributions.get((channel_uuid, ip))
                            emby_user_id = (
                                emby_attr_legacy.user_id if emby_attr_legacy else None
                            )
                            emby_user_name = (
                                emby_attr_legacy.user_name if emby_attr_legacy else None
                            )
                            plex_user_id: Optional[str] = None
                            plex_user_name: Optional[str] = None
                            jellyfin_user_id: Optional[str] = None
                            jellyfin_user_name: Optional[str] = None
                            # bd-r5f0c.9: legacy emby-only caller does
                            # not carry multi-viewer lists. Build a
                            # 1-element list from the legacy single
                            # attribution so the new emby_viewers column
                            # still reflects the writer's single match;
                            # plex / jellyfin viewers stay NULL (the
                            # legacy emby-only path doesn't surface them).
                            if emby_attr_legacy is not None:
                                emby_viewers_json: Optional[str] = json.dumps([
                                    {
                                        "user_id": emby_attr_legacy.user_id,
                                        "user_name": emby_attr_legacy.user_name,
                                    }
                                ])
                            else:
                                emby_viewers_json = None
                            plex_viewers_json: Optional[str] = None
                            jellyfin_viewers_json: Optional[str] = None
                        else:
                            emby_user_id = attribution.emby_user_id
                            emby_user_name = attribution.emby_user_name
                            plex_user_id = attribution.plex_user_id
                            plex_user_name = attribution.plex_user_name
                            jellyfin_user_id = attribution.jellyfin_user_id
                            jellyfin_user_name = attribution.jellyfin_user_name
                            # bd-r5f0c.9: serialise the full viewer lists
                            # for the new JSON columns. Empty list →
                            # NULL (semantically identical: no viewers
                            # for this source on this row). Non-empty
                            # → JSON-encoded ``[{"user_id", "user_name"},
                            # ...]`` per the column docstring in
                            # ``models.py``.
                            emby_viewers_json = (
                                json.dumps(attribution.emby_viewers)
                                if attribution.emby_viewers else None
                            )
                            plex_viewers_json = (
                                json.dumps(attribution.plex_viewers)
                                if attribution.plex_viewers else None
                            )
                            jellyfin_viewers_json = (
                                json.dumps(attribution.jellyfin_viewers)
                                if attribution.jellyfin_viewers else None
                            )

                        session.add(
                            SessionTelemetry(
                                session_id=f"conn-{conn_id}",
                                observed_at=observed_at_ms,
                                user_id=coerced_user_id,
                                # bd-gsn3r: denormalize the Dispatcharr
                                # username at write time so the read APIs
                                # can surface "who watched" without ever
                                # joining against ECM's local ``users``
                                # table (whose integer namespace
                                # collides with Dispatcharr's). NULL on
                                # anonymous viewers / unresolved
                                # usernames; documented on the model.
                                dispatcharr_username=disp_username,
                                provider_id=provider_id,
                                channel_id=channel_uuid,
                                bytes_delta=per_client_bytes,
                                buffer_event_count=row_buffer_count,
                                # bd-ov5vb (migration 0013): per-type
                                # counters paired with buffer_event_count.
                                # First-row-wins attribution per channel
                                # — sibling rows for the same channel
                                # poll write 0 across all four columns
                                # so SUM rollups don't double-count.
                                reconnect_event_count=row_reconnect_count,
                                error_event_count=row_error_count,
                                switch_event_count=row_switch_count,
                                poll_interval_ms=poll_interval_ms,
                                # bd-kh23e: capture stream identity so the
                                # read APIs can surface "what's playing"
                                # (PO directive 2026-05-14). NULL when
                                # the resolver couldn't attribute the
                                # active stream — same failure modes as
                                # provider_id.
                                stream_id=stream_id,
                                stream_name=stream_name,
                                # bd-gih6d (Emby) + bd-r5f0c.4 (Plex +
                                # Jellyfin): per-source attribution
                                # populated via the resolvers when the
                                # ECM session's client IP matches the
                                # configured source server's IP AND a
                                # concurrent source session is playing
                                # a matching item. All six NULL for
                                # non-source-mediated rows (most rows
                                # on most installs).
                                emby_user_id=emby_user_id,
                                emby_user_name=emby_user_name,
                                plex_user_id=plex_user_id,
                                plex_user_name=plex_user_name,
                                jellyfin_user_id=jellyfin_user_id,
                                jellyfin_user_name=jellyfin_user_name,
                                # bd-r5f0c.9: per-source full viewer
                                # lists (JSON-encoded). NULL when the
                                # source matched zero viewers for this
                                # (channel, ip) — semantically equivalent
                                # to the legacy *_user_name being NULL.
                                emby_viewers=emby_viewers_json,
                                plex_viewers=plex_viewers_json,
                                jellyfin_viewers=jellyfin_viewers_json,
                            )
                        )
                        rows_written += 1

                # Channel events were surfaced for channels with no
                # eligible row this poll (rare — between client
                # disconnect and the channel stopping). Log per channel
                # with a per-type breakdown so SRE can attribute the
                # drop without having to re-fetch the raw event feed.
                unattributed = channels_with_events - channel_events_attributed
                for channel_uuid in unattributed:
                    per_type = channel_events_by_channel.get(channel_uuid, {})
                    logger.warning(
                        "[STATS_V2] channel_event_dropped channel=%s "
                        "buffer=%s reconnect=%s error=%s switch=%s "
                        "reason=no_active_session_row",
                        channel_uuid,
                        per_type.get("channel_buffering", 0),
                        per_type.get("channel_reconnect", 0),
                        per_type.get("channel_error", 0),
                        per_type.get("stream_switch", 0),
                    )

                if rows_written:
                    # bd-gbxmj defense-in-depth: a future field could grow
                    # an FK constraint without this helper learning about
                    # it (and the user-id coercion above is itself a
                    # single point of failure). Catch IntegrityError at
                    # the batch commit, roll back the transaction, log
                    # the failure with enough context for SRE to find the
                    # poll in question, and treat the write as a
                    # recoverable failure rather than letting the
                    # outer Exception handler swallow it as an
                    # exc_info=True noise event. The primary fix is the
                    # helper; this is the safety net so a future
                    # constraint can never poison the entire batch
                    # silently again.
                    try:
                        session.commit()
                    except IntegrityError as e:
                        session.rollback()
                        logger.warning(
                            "[STATS_V2] session_telemetry batch FK violation "
                            "observed_at=%s rows_attempted=%s error=%s",
                            observed_at_ms,
                            rows_written,
                            e,
                        )
                        # The batch did not land — reflect that in the
                        # row count the metric block reports, and let
                        # the success/failure label below mark this as
                        # a failure so SRE's write-health gauge does not
                        # mis-attribute success on a rolled-back commit.
                        rows_written = 0
                        write_result = "failure"
                    else:
                        logger.debug(
                            "[STATS_V2] Wrote %s session_telemetry row(s) (observed_at=%s)",
                            rows_written,
                            observed_at_ms,
                        )
                        write_result = "success"
                        # bd-8axhi: a successful write proves the schema is
                        # currently consistent with the model. Re-arm the
                        # one-shot ERROR alarm so a future drift event
                        # (e.g., a second hot-deploy that adds a NEWER
                        # migration) triggers another ERROR rather than
                        # silently degrading to WARN.
                        self._schema_drift_alarm_armed = True
                else:
                    # Nothing to write; release the (empty) transaction.
                    session.rollback()
                    # Reaching this line means the write attempt
                    # completed without raising (rows_written is 0).
                    # Record success so the bd-skqln.12 metric reflects
                    # "the helper did what it was asked to do" rather
                    # than overstating failures whenever a poll had no
                    # active connections.
                    write_result = "success"
            finally:
                session.close()
        except Exception as e:
            # Observation-only path — failures must never propagate. The
            # legacy writes already committed before we got here.
            #
            # bd-skqln.12: WARN-level log carries trace_id (via the
            # observability filter) + observed_at (poll-scoped correlator)
            # + the count of channels we attempted, so SRE can correlate
            # this failure with the poll that produced it. Privacy 11a:
            # we deliberately do NOT enumerate per-row user_id+channel_id
            # pairs — those are aggregated away by the time we get here.
            #
            # bd-8axhi defense-in-depth: when the failure looks like
            # runtime schema drift (``no such column`` / ``no such
            # table`` — the bd-zaaey signature), escalate the FIRST
            # observation to ERROR-level with an explicit recovery path.
            # This catches the hot-deploy hazard the bd-zaaey boot guard
            # cannot cover: a developer ``docker cp``'s a new alembic
            # migration + writer code into a live container without
            # ``docker restart``. The boot guard already passed on the
            # last restart; the runtime drift surfaces here on the first
            # write that exercises the new column. Subsequent failures
            # fall back to WARN so the operator log is not flooded with
            # duplicate alarms while the root cause is being repaired.
            # Scope is deliberately SQLAlchemy ``OperationalError`` only —
            # sqlite3 ``OperationalError`` instances get wrapped before they
            # reach this layer, matching bd-zaaey's
            # ``_assert_schema_matches_models`` boot-guard scope. The helper
            # ``_is_schema_drift_error`` itself walks the exception chain so
            # any future scope expansion (e.g. accepting raw sqlite3 errors
            # from a different code path) only requires loosening this
            # ``isinstance`` gate, not changing the helper.
            if (
                isinstance(e, OperationalError)
                and _is_schema_drift_error(e)
                and self._schema_drift_alarm_armed
            ):
                self._schema_drift_alarm_armed = False
                logger.error(
                    "[STATS_V2] session_telemetry SCHEMA DRIFT detected at "
                    "runtime observed_at=%s channels_attempted=%s error=%s — "
                    "the SQLAlchemy model declares a column/table the live "
                    "DB does not have. Most likely cause: a new alembic "
                    "migration was added to the running container without "
                    "restarting it. Recovery: ``docker restart ecm-ecm-1`` "
                    "(the entrypoint runs ``alembic upgrade head`` on boot). "
                    "If the restart does not clear the error, see bd-zaaey "
                    "for the structural diagnostic. Subsequent occurrences "
                    "of this error in the current process will log at WARN.",
                    observed_at_ms,
                    len(channel_snapshot),
                    e,
                    exc_info=True,
                )
            else:
                logger.warning(
                    "[STATS_V2] session_telemetry write failed observed_at=%s channels_attempted=%s error=%s",
                    observed_at_ms,
                    len(channel_snapshot),
                    e,
                    exc_info=True,
                )
        finally:
            # Always emit the write-health metrics — success or failure
            # paths both observe duration and increment the result-keyed
            # counter. Wrapped helper so a metric-emission failure cannot
            # propagate out of the observation-only writer.
            duration = time.perf_counter() - write_start
            self._record_session_telemetry_metrics(
                result=write_result,
                duration_seconds=duration,
                rows_written=rows_written,
                rows_excluded=rows_excluded,
            )

    @staticmethod
    def get_bandwidth_summary() -> dict:
        """
        Get bandwidth summary for all time periods (using user's timezone).

        Returns:
            dict with today, this_week, this_month, this_year, all_time bytes,
            in/out breakdowns, peak bitrates, and daily_history for last 7 days
        """
        from sqlalchemy import func

        today = get_current_date()
        week_ago = today - timedelta(days=7)
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        session = get_session()
        try:
            # Use SQL aggregation for efficient calculations
            # Today's bytes (total, in, out)
            today_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_in), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_out), 0),
            ).filter(BandwidthDaily.date == today).first()
            today_bytes = today_result[0] or 0
            today_bytes_in = today_result[1] or 0
            today_bytes_out = today_result[2] or 0
            today_peak_bitrate_in = today_result[3] or 0
            today_peak_bitrate_out = today_result[4] or 0

            # This week's bytes
            week_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_in), 0),
                func.coalesce(func.max(BandwidthDaily.peak_bitrate_out), 0),
            ).filter(BandwidthDaily.date >= week_ago).first()
            week_bytes = week_result[0] or 0
            week_bytes_in = week_result[1] or 0
            week_bytes_out = week_result[2] or 0
            week_peak_bitrate_in = week_result[3] or 0
            week_peak_bitrate_out = week_result[4] or 0

            # This month's bytes
            month_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).filter(BandwidthDaily.date >= month_start).first()
            month_bytes = month_result[0] or 0
            month_bytes_in = month_result[1] or 0
            month_bytes_out = month_result[2] or 0

            # This year's bytes
            year_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).filter(BandwidthDaily.date >= year_start).first()
            year_bytes = year_result[0] or 0
            year_bytes_in = year_result[1] or 0
            year_bytes_out = year_result[2] or 0

            # All time bytes
            all_time_result = session.query(
                func.coalesce(func.sum(BandwidthDaily.bytes_transferred), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_in), 0),
                func.coalesce(func.sum(BandwidthDaily.bytes_out), 0),
            ).first()
            all_time_bytes = all_time_result[0] or 0
            all_time_bytes_in = all_time_result[1] or 0
            all_time_bytes_out = all_time_result[2] or 0

            # Get last 7 days for chart
            week_records = session.query(BandwidthDaily).filter(
                BandwidthDaily.date >= week_ago
            ).order_by(BandwidthDaily.date.asc()).all()

            daily_history = [record.to_dict() for record in week_records]

            return {
                # Legacy fields (backwards compatible)
                "today": today_bytes,
                "this_week": week_bytes,
                "this_month": month_bytes,
                "this_year": year_bytes,
                "all_time": all_time_bytes,
                # Inbound/Outbound breakdown
                "today_in": today_bytes_in,
                "today_out": today_bytes_out,
                "week_in": week_bytes_in,
                "week_out": week_bytes_out,
                "month_in": month_bytes_in,
                "month_out": month_bytes_out,
                "year_in": year_bytes_in,
                "year_out": year_bytes_out,
                "all_time_in": all_time_bytes_in,
                "all_time_out": all_time_bytes_out,
                # Peak bitrates (today and week)
                "today_peak_bitrate_in": today_peak_bitrate_in,
                "today_peak_bitrate_out": today_peak_bitrate_out,
                "week_peak_bitrate_in": week_peak_bitrate_in,
                "week_peak_bitrate_out": week_peak_bitrate_out,
                # Daily history for charts
                "daily_history": daily_history,
            }

        finally:
            session.close()

    @staticmethod
    def get_top_watched_channels(limit: int = 10, sort_by: str = "views") -> list[dict]:
        """
        Get the top watched channels by watch count or watch time.

        Args:
            limit: Maximum number of channels to return (default 10)
            sort_by: "views" for watch count, "time" for total watch time (default "views")

        Returns:
            List of channel watch stats dicts (channel_id, channel_name,
            watch_count, total_watch_seconds, last_watched), ordered by
            selected metric desc.

        bd-skqln.3 step (d): reads from ``session_telemetry`` (DISTINCT
        session_id and DISTINCT-by-observed_at poll-interval sum) and
        side-loads channel_name from ``UniqueClientConnection``. Returns
        the same dict shape the legacy ``ChannelWatchStats.to_dict()``
        produced, so API consumers don't need to change.
        """
        session = get_session()
        try:
            # DISTINCT-by-(channel_id, observed_at) collapse so concurrent
            # clients in one poll contribute one interval each — matches
            # the channel_watch_stats_v view + popularity calculator.
            per_poll = session.query(
                SessionTelemetry.channel_id.label("channel_id"),
                SessionTelemetry.observed_at.label("observed_at"),
                func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
            ).group_by(
                SessionTelemetry.channel_id,
                SessionTelemetry.observed_at,
            ).subquery()

            per_channel = session.query(
                per_poll.c.channel_id.label("channel_id"),
                func.coalesce(
                    func.sum(per_poll.c.poll_interval_ms) / 1000, 0
                ).label("total_watch_seconds"),
                func.max(per_poll.c.observed_at).label("last_observed_at_ms"),
            ).group_by(per_poll.c.channel_id).subquery()

            session_counts = session.query(
                SessionTelemetry.channel_id.label("channel_id"),
                func.count(distinct(SessionTelemetry.session_id)).label("watch_count"),
            ).group_by(SessionTelemetry.channel_id).subquery()

            query = session.query(
                per_channel.c.channel_id,
                per_channel.c.total_watch_seconds,
                per_channel.c.last_observed_at_ms,
                func.coalesce(session_counts.c.watch_count, 0).label("watch_count"),
            ).outerjoin(
                session_counts,
                session_counts.c.channel_id == per_channel.c.channel_id,
            )

            if sort_by == "time":
                query = query.order_by(per_channel.c.total_watch_seconds.desc())
            else:
                query = query.order_by(func.coalesce(session_counts.c.watch_count, 0).desc())

            rows = query.limit(limit).all()
            if not rows:
                return []

            # Side-load channel_name from UniqueClientConnection. One
            # round-trip for the candidate channel set rather than per-row.
            channel_ids = [r.channel_id for r in rows]
            name_rows = session.query(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name,
            ).filter(
                UniqueClientConnection.channel_id.in_(channel_ids),
            ).all()
            name_lookup: dict[str, str] = {}
            for cn_row in name_rows:
                # Take the first non-empty name we see per channel; the
                # writer keeps names consistent across rows on the same
                # channel but a stale row may still exist.
                if cn_row.channel_id not in name_lookup and cn_row.channel_name:
                    name_lookup[cn_row.channel_id] = cn_row.channel_name

            results = []
            for row in rows:
                last_watched_dt = (
                    datetime.utcfromtimestamp(row.last_observed_at_ms / 1000.0)
                    if row.last_observed_at_ms is not None
                    else None
                )
                results.append({
                    "channel_id": row.channel_id,
                    "channel_name": name_lookup.get(
                        row.channel_id, f"Channel {row.channel_id[:8]}..."
                    ),
                    "watch_count": int(row.watch_count or 0),
                    "total_watch_seconds": int(row.total_watch_seconds or 0),
                    "last_watched": (
                        last_watched_dt.isoformat() + "Z"
                        if last_watched_dt is not None
                        else None
                    ),
                })
            return results
        finally:
            session.close()

    @staticmethod
    def purge_old_records(days: int = 90):
        """Remove records older than specified days (using user's timezone)."""
        cutoff = get_current_date() - timedelta(days=days)

        session = get_session()
        try:
            deleted = session.query(BandwidthDaily).filter(
                BandwidthDaily.date < cutoff
            ).delete()
            session.commit()
            if deleted > 0:
                logger.info("[BANDWIDTH] Purged %s old bandwidth records", deleted)
        except Exception as e:
            logger.error("[BANDWIDTH] Failed to purge old records: %s", e)
            session.rollback()
        finally:
            session.close()

    # =========================================================================
    # Enhanced Statistics Query Methods (v0.11.0)
    # =========================================================================

    @staticmethod
    def get_unique_viewers_summary(days: int = 7) -> dict:
        """
        Get unique viewer statistics for the specified period.

        Args:
            days: Number of days to look back (default 7)

        Returns:
            dict with unique viewer counts and breakdown
        """
        from sqlalchemy import func, distinct

        cutoff = get_current_date() - timedelta(days=days)
        today = get_current_date()

        session = get_session()
        try:
            # Total unique IPs in period
            total_unique = session.query(
                func.count(distinct(UniqueClientConnection.ip_address))
            ).filter(UniqueClientConnection.date >= cutoff).scalar() or 0

            # Unique IPs today
            today_unique = session.query(
                func.count(distinct(UniqueClientConnection.ip_address))
            ).filter(UniqueClientConnection.date == today).scalar() or 0

            # Total connections in period
            total_connections = session.query(
                func.count(UniqueClientConnection.id)
            ).filter(UniqueClientConnection.date >= cutoff).scalar() or 0

            # Average watch time per connection
            avg_watch_time = session.query(
                func.avg(UniqueClientConnection.watch_seconds)
            ).filter(
                UniqueClientConnection.date >= cutoff,
                UniqueClientConnection.watch_seconds > 0
            ).scalar() or 0

            # Top viewers by connection count
            top_viewers = session.query(
                UniqueClientConnection.ip_address,
                func.count(UniqueClientConnection.id).label("connection_count"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds")
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.ip_address
            ).order_by(
                func.count(UniqueClientConnection.id).desc()
            ).limit(10).all()

            # Daily unique viewer counts for chart
            daily_unique = session.query(
                UniqueClientConnection.date,
                func.count(distinct(UniqueClientConnection.ip_address)).label("unique_count")
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.date
            ).order_by(
                UniqueClientConnection.date.asc()
            ).all()

            return {
                "period_days": days,
                "total_unique_viewers": total_unique,
                "today_unique_viewers": today_unique,
                "total_connections": total_connections,
                "avg_watch_seconds": round(avg_watch_time, 1),
                "top_viewers": [
                    {
                        "ip_address": v.ip_address,
                        "connection_count": v.connection_count,
                        "total_watch_seconds": v.total_watch_seconds or 0,
                    }
                    for v in top_viewers
                ],
                "daily_unique": [
                    {"date": d.date.isoformat(), "unique_count": d.unique_count}
                    for d in daily_unique
                ],
            }
        finally:
            session.close()

    @staticmethod
    def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes") -> list[dict]:
        """
        Get per-channel bandwidth statistics.

        Args:
            days: Number of days to aggregate (default 7)
            limit: Maximum channels to return (default 20)
            sort_by: "bytes", "connections", or "watch_time" (default "bytes")

        Returns:
            List of channel bandwidth stats, sorted by specified metric
        """
        from sqlalchemy import func

        cutoff = get_current_date() - timedelta(days=days)

        session = get_session()
        try:
            # Aggregate per-channel data
            query = session.query(
                ChannelBandwidth.channel_id,
                ChannelBandwidth.channel_name,
                func.sum(ChannelBandwidth.bytes_transferred).label("total_bytes"),
                func.sum(ChannelBandwidth.connection_count).label("total_connections"),
                func.sum(ChannelBandwidth.total_watch_seconds).label("total_watch_seconds"),
                func.max(ChannelBandwidth.peak_clients).label("peak_clients"),
            ).filter(
                ChannelBandwidth.date >= cutoff
            ).group_by(
                ChannelBandwidth.channel_id,
                ChannelBandwidth.channel_name
            )

            # Apply sorting
            if sort_by == "connections":
                query = query.order_by(func.sum(ChannelBandwidth.connection_count).desc())
            elif sort_by == "watch_time":
                query = query.order_by(func.sum(ChannelBandwidth.total_watch_seconds).desc())
            else:  # bytes
                query = query.order_by(func.sum(ChannelBandwidth.bytes_transferred).desc())

            results = query.limit(limit).all()

            return [
                {
                    "channel_id": r.channel_id,
                    "channel_name": r.channel_name,
                    "total_bytes": r.total_bytes or 0,
                    "total_connections": r.total_connections or 0,
                    "total_watch_seconds": r.total_watch_seconds or 0,
                    "peak_clients": r.peak_clients or 0,
                }
                for r in results
            ]
        finally:
            session.close()

    @staticmethod
    def get_unique_viewers_by_channel(days: int = 7, limit: int = 20) -> list[dict]:
        """
        Get unique viewer counts per channel.

        Args:
            days: Number of days to look back (default 7)
            limit: Maximum channels to return (default 20)

        Returns:
            List of channels with their unique viewer counts
        """
        from sqlalchemy import func, distinct

        cutoff = get_current_date() - timedelta(days=days)

        session = get_session()
        try:
            results = session.query(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name,
                func.count(distinct(UniqueClientConnection.ip_address)).label("unique_viewers"),
                func.count(UniqueClientConnection.id).label("total_connections"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds"),
            ).filter(
                UniqueClientConnection.date >= cutoff
            ).group_by(
                UniqueClientConnection.channel_id,
                UniqueClientConnection.channel_name
            ).order_by(
                func.count(distinct(UniqueClientConnection.ip_address)).desc()
            ).limit(limit).all()

            return [
                {
                    "channel_id": r.channel_id,
                    "channel_name": r.channel_name,
                    "unique_viewers": r.unique_viewers,
                    "total_connections": r.total_connections,
                    "total_watch_seconds": r.total_watch_seconds or 0,
                }
                for r in results
            ]
        finally:
            session.close()


# Global tracker instance
_tracker: Optional[BandwidthTracker] = None


def get_tracker() -> Optional[BandwidthTracker]:
    """Get the global tracker instance."""
    return _tracker


def set_tracker(tracker: BandwidthTracker):
    """Set the global tracker instance."""
    global _tracker
    _tracker = tracker
