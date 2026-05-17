"""
Stats router — channel stats, enhanced stats, and popularity endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.settings import get_auth_settings
from bandwidth_tracker import (
    BandwidthTracker,
    ChannelStreamsCache,
    resolve_active_channel_streams,
)
from database import get_session
from dispatcharr_client import get_client
from models import SessionTelemetry, UniqueClientConnection, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["Stats"])


# =============================================================================
# GH-62 watch-time read API (bd-skqln.5) — module-level helpers
# =============================================================================


async def get_watch_time_caller(
    request: Request,
    session: Session = Depends(get_session),
) -> Optional[User]:
    """Resolve the calling user for watch-time endpoints.

    Returns the authenticated ``User`` when global auth is enabled, ``None``
    when auth is disabled (the global auth middleware has already let the
    request through in that posture). The watch-time handlers use this to
    enforce the "non-admin can only query own user_id" rule per bd-skqln.5
    acceptance.

    Defined as a module-level function (rather than reusing
    ``auth.RequireAuthIfEnabled``) so tests can override it via
    ``app.dependency_overrides[get_watch_time_caller]`` — the standard
    FastAPI test seam.
    """
    settings = get_auth_settings()
    if not settings.require_auth or not settings.setup_complete:
        return None
    return await get_current_user(request, session)


def _parse_iso_utc(value: str, *, param: str) -> int:
    """Parse an ISO-8601 UTC string into unix-epoch milliseconds.

    Accepts both ``...Z`` and ``...+00:00`` forms. Rejects anything else with
    HTTP 400 so timezone-naive inputs cannot silently slip through and be
    interpreted as local time.
    """
    raw = value.strip()
    # Python's fromisoformat doesn't accept the 'Z' suffix until 3.11; accept
    # either form by normalising 'Z' -> '+00:00'.
    normalised = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must be an ISO-8601 UTC timestamp (got {value!r})",
        )
    if dt.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must include a timezone (Z or +00:00)",
        )
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _ms_to_iso_z(ms: Optional[int]) -> Optional[str]:
    """Convert ms-since-epoch -> ISO-8601 UTC string with trailing Z."""
    if ms is None:
        return None
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _distinct_poll_subquery(session: Session):
    """DISTINCT (user_id, channel_id, observed_at) collapse subquery.

    Returns one row per (user, channel, poll-tick) tuple with the poll
    interval. Collapses multi-client overcount: a user with N concurrent
    sessions on the same channel in one poll contributes ONE poll interval,
    not N. Mirrors the ``channel_watch_stats_v`` view's collapse pattern
    (migration 0008) but adds ``user_id`` to the DISTINCT key so per-user
    aggregations get the same guarantee.

    ``MAX(poll_interval_ms)`` inside the GROUP BY is defensive: in the rare
    case where two clients report different poll intervals for the same
    (user, channel, observed_at) tuple, take the longer one — never
    overcount.

    bd-gsn3r: ``MAX(dispatcharr_username)`` is also defensive — within a
    single (user_id, channel_id, observed_at) tuple every row should
    carry the same username (one human, one poll). MAX picks one
    deterministically without inflating the row count, so the outer
    GROUP-BY-user can read the username back without a second join.

    bd-fm23o (final bead of epic bd-2cenq): ``MAX(emby_user_name)`` is
    surfaced for the same reason — the writer
    (``BandwidthTracker._collect_emby_attributions``) populates this
    when the stream pull originates from the configured Emby server IP
    AND the resolver attributes the session to exactly one Emby user.
    The watch-time endpoint denormalizes it onto the per-user
    aggregation as the preferred display name (Emby wins over
    Dispatcharr when present) with a discriminator
    ``attribution_source`` field so the frontend can render a "via
    Emby" badge.
    """
    return (
        session.query(
            SessionTelemetry.user_id.label("user_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            SessionTelemetry.observed_at.label("observed_at"),
            func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
            func.max(SessionTelemetry.dispatcharr_username).label("dispatcharr_username"),
            func.max(SessionTelemetry.emby_user_name).label("emby_user_name"),
            # bd-r5f0c.4: Plex + Jellyfin attribution surfaces alongside
            # Emby on the same per-row aggregation. Same defensive MAX
            # rationale as the Emby column — within one (user, channel,
            # observed_at) tuple every row should agree, but MAX picks
            # one deterministically without inflating the row count.
            func.max(SessionTelemetry.plex_user_name).label("plex_user_name"),
            func.max(SessionTelemetry.jellyfin_user_name).label("jellyfin_user_name"),
        )
        .group_by(
            SessionTelemetry.user_id,
            SessionTelemetry.channel_id,
            SessionTelemetry.observed_at,
        )
        .subquery()
    )


def _build_envelope(data, *, from_ms, to_ms, group_by):
    """Standard response envelope for watch-time endpoints.

    Shape: ``{data: [...], meta: {...}, pagination: null}``. The pagination
    slot is reserved for a future page-cursor extension (bd-skqln.10 perf
    work may add it); for now both endpoints return all rows in-range and
    leave the slot ``null`` so clients can be coded against the full shape
    upfront.
    """
    return {
        "data": data,
        "meta": {
            "from_iso": _ms_to_iso_z(from_ms),
            "to_iso": _ms_to_iso_z(to_ms),
            "group_by": group_by,
            "total_rows": len(data),
        },
        "pagination": None,
    }


def _channel_name_or_fallback(channel_id: str, name_map: dict) -> str:
    """Look up channel name from UniqueClientConnection map, else synth fallback.

    Matches the skqln.3 step (d) precedent for popularity_calculator:
    side-load from ``UniqueClientConnection.channel_name``, fall back to
    ``"Channel <first-8-chars>..."`` when the connection table has no row
    yet (e.g., a brand-new channel observed only by ``session_telemetry``).
    """
    name = name_map.get(channel_id)
    if name:
        return name
    return f"Channel {channel_id[:8]}..."


def _seed_attribution_keys(channels: list) -> None:
    """Seed every channel + client with attribution keys (single + multi-viewer).

    Idempotent — ``setdefault`` only writes when the key is missing.
    Called from every short-circuit branch in
    :func:`_enrich_channels_with_attribution` so the TypeScript shape
    contract on the frontend stays stable (key present, value
    ``None`` / empty list) regardless of which sources are enabled or
    whether the resolver call short-circuits.

    bd-r5f0c.9: also seeds the three multi-viewer ``*_viewers`` list
    keys at parity with the singular ``*_user_name`` keys. The
    frontend (W5) renders the viewers list separately; this helper
    keeps the response shape stable so the frontend can rely on key
    presence regardless of resolver state.
    """
    for ch in channels:
        ch.setdefault("emby_user_name", None)
        ch.setdefault("plex_user_name", None)
        ch.setdefault("jellyfin_user_name", None)
        ch.setdefault("emby_viewers", [])
        ch.setdefault("plex_viewers", [])
        ch.setdefault("jellyfin_viewers", [])
        for client in ch.get("clients", []) or []:
            client.setdefault("emby_user_name", None)
            client.setdefault("plex_user_name", None)
            client.setdefault("jellyfin_user_name", None)
            client.setdefault("emby_viewers", [])
            client.setdefault("plex_viewers", [])
            client.setdefault("jellyfin_viewers", [])


async def _enrich_channels_with_attribution(channels: list) -> None:
    """Populate per-source ``*_user_name`` per channel + per client (bd-r5f0c.4).

    Extends bd-fm23o (Emby-only) to Plex + Jellyfin. Mutates ``channels``
    in place — adds three nullable fields (``emby_user_name``,
    ``plex_user_name``, ``jellyfin_user_name``) to each channel entry
    AND to each entry in the channel's ``clients`` list. The frontend
    AttributionBadge keys on whichever of the three is non-null,
    falling back to the Dispatcharr-side username if all three are
    None.

    Gate
    ----
    Each source's enrichment branch checks its own ``<source>_enabled``
    flag and short-circuits when disabled. Sources that are off
    contribute ``None`` to every channel/client field — the keys are
    still seeded via :func:`_seed_attribution_keys` so the shape
    contract holds regardless of operator configuration.

    Precedence
    ----------
    Each source's resolver is called independently — a channel may
    surface attribution from multiple sources concurrently when an
    operator runs multiple media servers. The frontend (and
    :func:`_pick_display_name_and_source` on backend read paths)
    decides which to display via the documented precedence:
    Emby > Plex > Jellyfin > Dispatcharr.

    Back-compat
    -----------
    bd-fm23o (existing) keyed on ``emby_user_name`` and the
    ``_enrich_channels_with_emby`` alias below preserves the old
    name for any external caller. The Active Channels endpoint now
    calls THIS helper instead so Plex + Jellyfin attribution
    surfaces on the same response shape.
    """
    if not channels:
        return
    try:
        from config import get_settings
        settings = get_settings()
    except Exception:  # pragma: no cover — settings access raise is exotic
        _seed_attribution_keys(channels)
        return

    _seed_attribution_keys(channels)

    emby_enabled = bool(getattr(settings, "emby_enabled", False))
    plex_enabled = bool(getattr(settings, "plex_enabled", False))
    jellyfin_enabled = bool(getattr(settings, "jellyfin_enabled", False))

    if not (emby_enabled or plex_enabled or jellyfin_enabled):
        # Every source disabled — keys already seeded to None above.
        return

    # Lazy imports — when a source is disabled, we don't pay the import
    # cost on the hot path. The disabled checks already short-circuited
    # the disabled-everything case above; from here on at least one
    # source is enabled.
    #
    # bd-r5f0c.9: import BOTH the singular (bd-fm23o/bd-r5f0c.4 mock
    # target, also the existing test_stats_emby.py regression target)
    # and the plural (bd-r5f0c.9 multi-viewer surface) resolvers. The
    # per-source helper calls both — whichever returns the most
    # viewers populates the response. In production the plural is
    # authoritative (singular = plural[0]); in legacy tests that mock
    # only singular, the plural call returns empty and we wrap the
    # singular result into a 1-element list so the legacy contract
    # holds.
    resolve_emby_user = None
    resolve_emby_users = None
    resolve_plex_user = None
    resolve_plex_users = None
    resolve_jellyfin_user = None
    resolve_jellyfin_users = None
    if emby_enabled:
        from services.emby_resolver import resolve_emby_user as resolve_emby_user
        from services.emby_resolver import resolve_emby_users as resolve_emby_users
    if plex_enabled:
        from services.plex_resolver import resolve_plex_user as resolve_plex_user
        from services.plex_resolver import resolve_plex_users as resolve_plex_users
    if jellyfin_enabled:
        from services.jellyfin_resolver import resolve_jellyfin_user as resolve_jellyfin_user
        from services.jellyfin_resolver import resolve_jellyfin_users as resolve_jellyfin_users

    for ch in channels:
        stream_name = ch.get("stream_name")
        # bd-zldrq (fix-forward for v0.17.1-0033): pull channel_name +
        # channel_number off the Dispatcharr response so the tiered
        # resolvers can match each source's live-TV
        # "<number> | <name>" item surface even when the Dispatcharr
        # stream name is provider-prefixed verbose.
        channel_name = ch.get("channel_name")
        channel_number = ch.get("channel_number")
        # Skip cheap when no tier could match for any source.
        if not stream_name and not channel_name and channel_number is None:
            continue
        clients = ch.get("clients") or []
        client_ips = [c.get("ip_address") for c in clients if c.get("ip_address")]
        if not client_ips:
            continue

        # Each source's enrichment is independent — a channel can
        # surface attribution from multiple sources on the same poll
        # if the operator runs multiple media servers (rare but
        # supported).
        if emby_enabled:
            await _enrich_one_source(
                ch=ch,
                clients=clients,
                client_ips=client_ips,
                stream_name=stream_name,
                channel_name=channel_name,
                channel_number=channel_number,
                single_resolver=resolve_emby_user,
                plural_resolver=resolve_emby_users,
                source_label="emby",
                user_name_key="emby_user_name",
                viewers_key="emby_viewers",
            )
        if plex_enabled:
            await _enrich_one_source(
                ch=ch,
                clients=clients,
                client_ips=client_ips,
                stream_name=stream_name,
                channel_name=channel_name,
                channel_number=channel_number,
                single_resolver=resolve_plex_user,
                plural_resolver=resolve_plex_users,
                source_label="plex",
                user_name_key="plex_user_name",
                viewers_key="plex_viewers",
            )
        if jellyfin_enabled:
            await _enrich_one_source(
                ch=ch,
                clients=clients,
                client_ips=client_ips,
                stream_name=stream_name,
                channel_name=channel_name,
                channel_number=channel_number,
                single_resolver=resolve_jellyfin_user,
                plural_resolver=resolve_jellyfin_users,
                source_label="jellyfin",
                user_name_key="jellyfin_user_name",
                viewers_key="jellyfin_viewers",
            )


async def _enrich_one_source(
    *,
    ch: dict,
    clients: list,
    client_ips: list,
    stream_name,
    channel_name,
    channel_number,
    single_resolver,
    plural_resolver,
    source_label: str,
    user_name_key: str,
    viewers_key: str,
) -> None:
    """Resolve one channel's clients against one media source (multi-viewer).

    Shared loop used by Emby / Plex / Jellyfin branches of
    :func:`_enrich_channels_with_attribution`.

    bd-r5f0c.9 dual-call back-compat discipline: call BOTH
    ``single_resolver`` (the bd-fm23o/bd-r5f0c.4 mock target the
    existing ``test_stats_emby.py`` regression suite asserts against)
    and ``plural_resolver`` (the bd-r5f0c.9 multi-viewer surface).
    Whichever returns the most viewers populates the response. In
    production both go to the same real resolver chain and the plural
    carries everything the singular does (the singular wrapper is
    ``plural[0]``); in legacy tests that mock only the singular
    function, the plural call hits the real un-mocked resolver and
    returns empty, so we wrap the singular result into a 1-element
    list. The production cost is one extra microsecond-scale function
    call per (channel, ip); the back-compat benefit is the full
    bd-fm23o + bd-r5f0c.4 stats regression suite continues to verify
    the single-viewer contract without test-file edits.

    Surfaces TWO fields per channel + per client:

    * ``<user_name_key>`` — back-compat singular field, populated from
      position 0 of the viewer list (most-recent viewer). Frontend
      pre-W5 renders this verbatim.
    * ``<viewers_key>`` — full list of ``{"user_id", "user_name"}``
      dicts. W5 frontend renders every viewer; empty list when no
      tier matched.

    Each Attribution duck-typed result is normalised to a plain dict
    ``{"user_id": ..., "user_name": ...}``; PlexAttribution carries a
    ``.user_id`` slot (currently always ``None``) and a ``.user_name``
    slot, matching the Emby / Jellyfin shape. The Plex resolver's
    legacy singular wrapper returns ``str | None``; we handle both the
    string and the dataclass forms in the duck-typed extraction below.
    """
    for ip in client_ips:
        # Call BOTH the singular (legacy mock seam) and the plural
        # (bd-r5f0c.9 multi-viewer). Whichever has more entries wins.
        try:
            single_result = await single_resolver(
                ip,
                stream_name or "",
                ecm_channel_name=channel_name,
                ecm_channel_number=channel_number,
            )
        except Exception as exc:  # pragma: no cover — resolver never raises
            logger.debug(
                "[STATS] %s singular resolver raised for ip=%s stream=%r: %s",
                source_label.upper(), ip, stream_name, exc,
            )
            single_result = None
        try:
            plural_result = await plural_resolver(
                ip,
                stream_name or "",
                ecm_channel_name=channel_name,
                ecm_channel_number=channel_number,
            )
        except Exception as exc:  # pragma: no cover — resolver never raises
            logger.debug(
                "[STATS] %s plural resolver raised for ip=%s stream=%r: %s",
                source_label.upper(), ip, stream_name, exc,
            )
            plural_result = []

        # Normalize to a single viewers list. Plural wins when non-empty
        # (production path + new multi-viewer tests). Singular wraps to
        # a 1-element list when plural is empty (legacy test path that
        # mocks only the singular function).
        viewers_list: list = []
        if plural_result:
            viewers_list = list(plural_result)
        elif single_result is not None:
            viewers_list = [single_result]

        if not viewers_list:
            continue

        # Duck-typed dict coercion: Plex's singular returns a bare
        # ``str`` (legacy contract); Emby + Jellyfin (and all three
        # plural variants) return dataclasses with ``.user_id +
        # .user_name``. Normalise to ``{"user_id", "user_name"}`` so
        # the response shape matches the bandwidth_tracker writer's
        # JSON encoding of ``session_telemetry.<source>_viewers``.
        viewers_payload = [_coerce_viewer_to_dict(v) for v in viewers_list]
        # Filter out entries with no user_name (defensive; should not
        # occur in production).
        viewers_payload = [v for v in viewers_payload if v.get("user_name")]
        if not viewers_payload:
            continue

        top_user_name = viewers_payload[0]["user_name"]

        ch[user_name_key] = top_user_name
        ch[viewers_key] = viewers_payload
        # bd-5kbyf-style propagation: a source-mediated stream has
        # every client coming from the source server's IP, so the
        # channel-level resolved viewers ARE the per-client viewers.
        for client in clients:
            client[user_name_key] = top_user_name
            client[viewers_key] = viewers_payload
        # First IP-match wins — same precedent as bd-fm23o for the
        # Emby-only flow. The source server's clients all share an IP,
        # so the first IP's viewers list is the channel's viewers list.
        return


def _coerce_viewer_to_dict(viewer) -> dict:
    """Normalise a resolver result to ``{"user_id", "user_name"}``.

    Handles three input shapes:

    * Bare ``str`` (legacy Plex singular wrapper) → ``{"user_id": None,
      "user_name": <str>}``.
    * Dataclass with ``.user_id`` + ``.user_name`` (Emby / Jellyfin
      attribution + the bd-r5f0c.9 PlexAttribution) → dict of the same.
    * Plain dict → return verbatim (defensive; the bandwidth_tracker
      writer's JSON-decoded form already uses this shape).
    """
    if isinstance(viewer, str):
        return {"user_id": None, "user_name": viewer}
    if isinstance(viewer, dict):
        return {
            "user_id": viewer.get("user_id"),
            "user_name": viewer.get("user_name"),
        }
    return {
        "user_id": getattr(viewer, "user_id", None),
        "user_name": getattr(viewer, "user_name", None),
    }


async def _enrich_channels_with_emby(channels: list) -> None:
    """Back-compat alias for :func:`_enrich_channels_with_attribution` (bd-fm23o).

    bd-r5f0c.4 superseded this Emby-only helper with the multi-source
    enrichment. The alias is kept so external callers (and any older
    tests that haven't migrated yet) continue working without a hard
    break — the new helper produces a superset of the old's fields
    (plex / jellyfin keys added; existing ``emby_user_name`` path
    unchanged).
    """
    await _enrich_channels_with_attribution(channels)


# =============================================================================
# Stats & Monitoring
# =============================================================================


@router.get("/channels")
async def get_channel_stats():
    """Get status of all active channels.

    Returns summary including active channels, client counts, bitrates, speeds, etc.
    Enriches client data with usernames resolved from Dispatcharr user accounts.

    bd-ox5q8: per active channel, additionally surfaces
    ``stream_name`` and ``m3u_account_id`` resolved live (request-time)
    via ``resolve_active_channel_streams`` — the same code path the
    polling tracker uses for ``session_telemetry``. The PO directive is
    "Active Channels is a live view; operators expect immediate
    accuracy" — so this is intentionally a fresh resolver call, NOT a
    read from the (up-to-one-poll-stale) ``session_telemetry`` table.
    Resolver failure leaves both fields ``None`` and the row still
    surfaces (best-effort enrichment — never block the live view on a
    Dispatcharr lookup hiccup).

    bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution): each
    channel additionally surfaces ``emby_user_name`` when at least one
    of its clients resolves to an Emby session via
    :func:`services.emby_resolver.resolve_emby_user` (gated on
    ``settings.emby_enabled``). The field is ``None`` when Emby is
    disabled, the resolver couldn't attribute any client, or no client
    came from the configured Emby server IP. This drives the Active
    Channels "(watching: <emby_user>)" badge — the operator-visible
    surface that completes the Emby attribution chain in the live view.
    """
    logger.debug("[STATS] GET /api/stats/channels")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_stats()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_channel_stats completed in %.1fms", elapsed_ms)

        # Resolve user_id → username for connected clients
        has_user_ids = any(
            c.get("user_id")
            for ch in result.get("channels", [])
            for c in ch.get("clients", [])
        )
        if has_user_ids:
            try:
                users = await client.get_users()
                user_map = {str(u["id"]): u.get("username", "") for u in users}
                for ch in result.get("channels", []):
                    for c in ch.get("clients", []):
                        uid = c.get("user_id")
                        if uid and str(uid) in user_map:
                            c["username"] = user_map[str(uid)]
            except Exception as e:
                logger.warning("[STATS] Failed to resolve usernames: %s", e)

        # bd-ox5q8: live stream-identity enrichment for the Active
        # Channels view. Build the snapshot shape ``resolve_active_
        # channel_streams`` expects (channel_uuid + stream_id + url),
        # call the resolver with a transient cache so this request
        # doesn't share state with any other request, and merge the
        # resolved ``stream_name`` + ``m3u_account_id`` (+ ``stream_id``
        # for completeness) onto each ChannelStats row.
        channels = result.get("channels", []) or []
        if channels:
            snapshot = [
                {
                    "channel_uuid": str(ch.get("channel_id", "")),
                    "stream_id": ch.get("stream_id"),
                    "url": ch.get("url"),
                }
                for ch in channels
            ]
            try:
                resolutions = await resolve_active_channel_streams(
                    client,
                    snapshot,
                    channel_streams_cache=ChannelStreamsCache(),
                    poll_count=0,
                    # Suppress the per-poll SLI metric — that signal is
                    # owned by the BandwidthTracker hot path. The endpoint
                    # call is on-demand and would dilute the cyclic SLI.
                    emit_metrics=False,
                )
            except Exception as e:
                # Defense-in-depth — resolver internals already catch
                # per-channel failures and return EMPTY_RESOLUTION; an
                # outer raise here would be unexpected (e.g. structural
                # bug). Best-effort enrichment: log + degrade gracefully
                # so the Active Channels view still renders.
                logger.warning(
                    "[STATS] live stream-identity resolver raised: %s", e
                )
                resolutions = {}

            for ch in channels:
                uuid = str(ch.get("channel_id", ""))
                resolution = resolutions.get(uuid)
                if resolution is None:
                    ch["stream_name"] = ch.get("stream_name")
                    ch["m3u_account_id"] = None
                    continue
                # Only override the existing stream_name when the
                # resolver actually produced one (preserve whatever
                # Dispatcharr surfaced if the resolver came up empty).
                if resolution.stream_name is not None:
                    ch["stream_name"] = resolution.stream_name
                elif "stream_name" not in ch:
                    ch["stream_name"] = None
                if resolution.stream_id is not None and not ch.get("stream_id"):
                    ch["stream_id"] = resolution.stream_id
                ch["m3u_account_id"] = resolution.provider_id

            # bd-fm23o: Emby attribution enrichment for the Active
            # Channels live view. Mirrors the writer-side enrichment
            # (``BandwidthTracker._collect_emby_attributions``) but
            # operates per-channel on the request-time stream_name and
            # the live client IPs surfaced by Dispatcharr's
            # ``/proxy/ts/status`` response. ``emby_user_name`` is
            # populated on the channel when AT LEAST ONE client resolves
            # to a single Emby user — the frontend renders the
            # "(watching: <emby_user>)" badge from this field. ``None``
            # when Emby is disabled, no client matched, or the resolver
            # produced no attribution. Never blocks the live view: any
            # resolver fault drops the field for that channel and
            # continues.
            # bd-r5f0c.4: multi-source attribution. Same call shape as
            # the bd-fm23o Emby-only enrichment — the function now
            # populates emby_user_name + plex_user_name +
            # jellyfin_user_name per channel and per client. Existing
            # frontend consumers reading emby_user_name see no shape
            # change; new consumers (W5 AttributionBadge) read the
            # added keys.
            await _enrich_channels_with_attribution(channels)

        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get channel stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channels/{channel_id}")
async def get_channel_stats_detail(channel_id: int):
    """Get detailed stats for a specific channel.

    Includes per-client information, buffer status, codec details, etc.
    """
    logger.debug("[STATS] GET /api/stats/channels/%s", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_stats_detail(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_channel_stats_detail for %s completed in %.1fms", channel_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get channel stats for %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/activity")
async def get_system_events(
    limit: int = 100,
    offset: int = 0,
    event_type: Optional[str] = None,
):
    """Get recent system events (channel start/stop, buffering, client connections).

    Args:
        limit: Number of events to return (default 100, max 1000)
        offset: Pagination offset
        event_type: Optional filter by event type
    """
    logger.debug("[STATS] GET /api/stats/activity - limit=%s offset=%s event_type=%s", limit, offset, event_type)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_system_events(
            limit=min(limit, 1000),
            offset=offset,
            event_type=event_type,
        )
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] get_system_events completed in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to get system events")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/channels/{channel_id}/stop")
async def stop_channel(channel_id: str):
    """Stop a channel and release all associated resources."""
    logger.debug("[STATS] POST /api/stats/channels/%s/stop", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.stop_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] stop_channel %s completed in %.1fms", channel_id, elapsed_ms)
        logger.info("[STATS] Stopped channel id=%s", channel_id)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to stop channel %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/channels/{channel_id}/stop-client")
async def stop_client(channel_id: str):
    """Stop a specific client connection."""
    logger.debug("[STATS] POST /api/stats/channels/%s/stop-client", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.stop_client(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[STATS] stop_client for channel %s completed in %.1fms", channel_id, elapsed_ms)
        logger.info("[STATS] Stopped client for channel id=%s", channel_id)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to stop client for channel %s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/bandwidth")
async def get_bandwidth_stats():
    """Get bandwidth usage summary for all time periods."""
    logger.debug("[STATS] GET /api/stats/bandwidth")
    try:
        return BandwidthTracker.get_bandwidth_summary()
    except Exception as e:
        logger.exception("[STATS] Failed to get bandwidth stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/top-watched")
async def get_top_watched_channels(limit: int = 10, sort_by: str = "views"):
    """Get the top watched channels by watch count or watch time."""
    logger.debug("[STATS] GET /api/stats/top-watched - limit=%s sort_by=%s", limit, sort_by)
    try:
        return BandwidthTracker.get_top_watched_channels(limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.exception("[STATS] Failed to get top watched channels")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Enhanced Statistics Endpoints (v0.11.0)
# =============================================================================


@router.get("/unique-viewers")
async def get_unique_viewers_summary(days: int = 7):
    """Get unique viewer statistics for the specified period."""
    logger.debug("[STATS] GET /api/stats/unique-viewers - days=%s", days)
    try:
        return BandwidthTracker.get_unique_viewers_summary(days=days)
    except Exception as e:
        logger.exception("[STATS] Failed to get unique viewers summary")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channel-bandwidth")
async def get_channel_bandwidth_stats(days: int = 7, limit: int = 20, sort_by: str = "bytes"):
    """Get per-channel bandwidth statistics."""
    logger.debug("[STATS] GET /api/stats/channel-bandwidth - days=%s limit=%s sort_by=%s", days, limit, sort_by)
    try:
        return BandwidthTracker.get_channel_bandwidth_stats(days=days, limit=limit, sort_by=sort_by)
    except Exception as e:
        logger.exception("[STATS] Failed to get channel bandwidth stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/unique-viewers-by-channel")
async def get_unique_viewers_by_channel(days: int = 7, limit: int = 20):
    """Get unique viewer counts per channel."""
    logger.debug("[STATS] GET /api/stats/unique-viewers-by-channel - days=%s limit=%s", days, limit)
    try:
        return BandwidthTracker.get_unique_viewers_by_channel(days=days, limit=limit)
    except Exception as e:
        logger.exception("[STATS] Failed to get unique viewers by channel")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/watch-history")
async def get_watch_history(
    page: int = 1,
    page_size: int = 50,
    channel_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    days: Optional[int] = None,
):
    """
    Get watch history log - all channel viewing sessions.

    Args:
        page: Page number (1-indexed)
        page_size: Number of records per page (max 100)
        channel_id: Filter by specific channel
        ip_address: Filter by specific IP address
        days: Filter to last N days (None = all time)
    """
    logger.debug("[STATS] GET /api/stats/watch-history - page=%s page_size=%s channel_id=%s", page, page_size, channel_id)
    try:
        from models import UniqueClientConnection
        from sqlalchemy import func, desc
        from datetime import date, timedelta

        session = get_session()
        try:
            # Build query
            query = session.query(UniqueClientConnection)

            # Apply filters
            if channel_id:
                query = query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                query = query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(UniqueClientConnection.date >= cutoff_date)

            # Get total count
            total = query.count()

            # Limit page_size
            page_size = min(page_size, 100)

            # Apply pagination and ordering (most recent first)
            offset = (page - 1) * page_size
            records = query.order_by(
                desc(UniqueClientConnection.connected_at)
            ).offset(offset).limit(page_size).all()

            # Get summary stats
            summary_query = session.query(
                func.count(func.distinct(UniqueClientConnection.channel_id)).label("unique_channels"),
                func.count(func.distinct(UniqueClientConnection.ip_address)).label("unique_ips"),
                func.sum(UniqueClientConnection.watch_seconds).label("total_watch_seconds"),
            )
            if channel_id:
                summary_query = summary_query.filter(UniqueClientConnection.channel_id == channel_id)
            if ip_address:
                summary_query = summary_query.filter(UniqueClientConnection.ip_address == ip_address)
            if days:
                summary_query = summary_query.filter(UniqueClientConnection.date >= cutoff_date)

            summary = summary_query.first()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
                "summary": {
                    "unique_channels": summary.unique_channels or 0,
                    "unique_ips": summary.unique_ips or 0,
                    "total_watch_seconds": summary.total_watch_seconds or 0,
                },
                "history": [
                    {
                        "id": r.id,
                        "channel_id": r.channel_id,
                        "channel_name": r.channel_name,
                        "ip_address": r.ip_address,
                        "user_id": r.user_id,
                        "username": r.username,
                        "date": r.date.isoformat() if r.date else None,
                        "connected_at": r.connected_at.isoformat() + "Z" if r.connected_at else None,
                        "disconnected_at": r.disconnected_at.isoformat() + "Z" if r.disconnected_at else None,
                        "watch_seconds": r.watch_seconds,
                    }
                    for r in records
                ],
            }
        finally:
            session.close()
    except Exception as e:
        logger.exception("[STATS] Failed to get watch history")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Popularity Endpoints (v0.11.0)
# =============================================================================


@router.get("/popularity/rankings")
async def get_popularity_rankings(limit: int = 50, offset: int = 0):
    """Get channel popularity rankings."""
    logger.debug("[STATS] GET /api/stats/popularity/rankings - limit=%s offset=%s", limit, offset)
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_rankings(limit=limit, offset=offset)
    except Exception as e:
        logger.exception("[STATS] Failed to get popularity rankings")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/popularity/channel/{channel_id}")
async def get_channel_popularity(channel_id: str):
    """Get popularity score for a specific channel."""
    logger.debug("[STATS] GET /api/stats/popularity/channel/%s", channel_id)
    try:
        from popularity_calculator import PopularityCalculator
        result = PopularityCalculator.get_channel_score(channel_id)
        if not result:
            raise HTTPException(status_code=404, detail="Channel popularity score not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STATS] Failed to get channel popularity")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/popularity/trending")
async def get_trending_channels(direction: str = "up", limit: int = 10):
    """Get channels that are trending up or down."""
    logger.debug("[STATS] GET /api/stats/popularity/trending - direction=%s limit=%s", direction, limit)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    try:
        from popularity_calculator import PopularityCalculator
        return PopularityCalculator.get_trending_channels(direction=direction, limit=limit)
    except Exception as e:
        logger.exception("[STATS] Failed to get trending channels")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/popularity/calculate")
async def calculate_popularity_scores(
    period_days: int = 7,
):
    """
    Trigger popularity score calculation.

    Args:
        period_days: Number of days to consider for scoring
    """
    logger.debug("[STATS] POST /api/stats/popularity/calculate - period_days=%s", period_days)
    try:
        from popularity_calculator import calculate_popularity
        result = calculate_popularity(period_days=period_days)
        logger.info("[STATS] Completed popularity calculation for %s days", period_days)
        return result
    except Exception as e:
        logger.exception("[STATS] Failed to calculate popularity")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# GH-62 watch-time read API (bd-skqln.5)
# =============================================================================
#
# Both endpoints read directly from ``session_telemetry`` (the per-poll fact
# table from bd-skqln.2 / .3). The ``channel_watch_stats_v`` view from
# bd-skqln.3 step (b) aggregates per-channel only and does NOT expose
# ``user_id``, so it cannot satisfy these per-user queries — using
# ``session_telemetry`` directly with a DISTINCT-by-(user, channel,
# observed_at) subquery is the honest fit.
#
# Performance: relies on the ``idx_session_telemetry_user_observed``
# composite index (migration 0006) for range scans by user_id, and the
# bare ``idx_session_telemetry_observed_at`` index when ``user_id`` is not
# filtered. p95 < 800ms / p99 < 2s at 3-6 months of data per
# bd-skqln.5 acceptance — pytest-benchmark gate lands separately in
# bd-skqln.10.


_VALID_GROUP_BY = {"total", "day"}


@router.get("/watch-time")
async def get_watch_time_by_user(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    user_id: Optional[int] = None,
    group_by: str = "total",
):
    """List per-user watch-time totals (GH-62).

    Query parameters (all snake_case in the URL):

    * ``from`` — ISO-8601 UTC range start (inclusive). Optional.
    * ``to`` — ISO-8601 UTC range end (exclusive). Optional.
    * ``user_id`` — filter to a single user. Optional. **Admin-only
      endpoint**: non-admin callers receive 403.
    * ``group_by`` — ``total`` (default) or ``day``. ``total`` returns one
      row per user. ``day`` returns one row per (user, UTC-day) pair.

    Response envelope (per bd-skqln.5):
        ``{data: [...], meta: {from_iso, to_iso, group_by, total_rows},
           pagination: null}``

    Row shape for ``group_by=total``:
        ``{user_id, username, attribution_source, total_watch_seconds,
           last_watched}``

    Row shape for ``group_by=day``:
        ``{user_id, username, attribution_source, day, watch_seconds}``

    bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution): the
    ``username`` field prefers ``emby_user_name`` (denormalized at write
    time on rows attributed to an Emby user by
    ``BandwidthTracker._collect_emby_attributions``) over the
    Dispatcharr-side ``dispatcharr_username`` for users with ANY
    Emby-attributed row. The ``attribution_source`` discriminator
    (``"emby"`` or ``"dispatcharr"``) lets the frontend render a "via
    Emby" badge so the operator knows the attribution chain. When neither
    name is present the row still surfaces with ``username = null``
    (the legacy "Unknown viewer" case).
    """
    logger.debug(
        "[STATS] GET /api/stats/watch-time from=%s to=%s user_id=%s group_by=%s",
        from_, to, user_id, group_by,
    )
    if group_by not in _VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"group_by must be one of {sorted(_VALID_GROUP_BY)}",
        )

    # Auth enforcement: watch-time stats are admin-only. Non-admin callers
    # are blocked regardless of which user_id they query (or whether they
    # omit the filter). PO directive 2026-05-13: non-admins do not see stats.
    # (caller is None when global auth is disabled — auth-disabled mode is
    # the test-default posture and operator-only deployments; treat as
    # admin-equivalent there.)
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )

    from_ms = _parse_iso_utc(from_, param="from") if from_ else None
    to_ms = _parse_iso_utc(to, param="to") if to else None

    # Empty-range short-circuit: if from == to, the inclusive/exclusive
    # convention makes the window empty. Skip the DB round-trip.
    if from_ms is not None and to_ms is not None and from_ms >= to_ms:
        return _build_envelope([], from_ms=from_ms, to_ms=to_ms, group_by=group_by)

    try:
        distinct = _distinct_poll_subquery(db)
        # Only count rows whose user_id is non-NULL: NULL user_id == anonymous
        # poll (no logged-in user). Watch-time-by-user has no meaningful row
        # for the anonymous bucket — drop them rather than emit a NULL-user
        # row.
        base_q = db.query(distinct).filter(distinct.c.user_id.isnot(None))
        if from_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at >= from_ms)
        if to_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at < to_ms)
        if user_id is not None:
            base_q = base_q.filter(distinct.c.user_id == user_id)
        inner = base_q.subquery()

        if group_by == "total":
            rows = (
                db.query(
                    inner.c.user_id,
                    func.sum(inner.c.poll_interval_ms).label("total_ms"),
                    func.max(inner.c.observed_at).label("last_observed_at"),
                    # bd-gsn3r: surface the denormalized Dispatcharr
                    # username directly from session_telemetry. MAX is
                    # defensive (every row for one user_id should carry
                    # the same username — one human, one Dispatcharr
                    # account) and gives stable behavior even if a
                    # Dispatcharr-side rename mid-window left both old
                    # and new values in the table.
                    func.max(inner.c.dispatcharr_username).label(
                        "dispatcharr_username"
                    ),
                    # bd-fm23o: also pull the denormalized Emby username
                    # so we can prefer it over the Dispatcharr-side name
                    # when present. MAX is again defensive — multiple
                    # Emby-attributed rows for one ECM user_id should
                    # carry the same emby_user_name (one viewer, one Emby
                    # account); MAX picks one deterministically without
                    # inflating the row count.
                    func.max(inner.c.emby_user_name).label("emby_user_name"),
                    # bd-r5f0c.4: Plex + Jellyfin attribution alongside
                    # Emby. ``_pick_display_name_and_source`` applies the
                    # precedence (Emby > Plex > Jellyfin > Dispatcharr)
                    # to choose the user-facing display name.
                    func.max(inner.c.plex_user_name).label("plex_user_name"),
                    func.max(inner.c.jellyfin_user_name).label(
                        "jellyfin_user_name"
                    ),
                )
                .group_by(inner.c.user_id)
                .all()
            )
            data = [
                _build_watch_time_row_total(r)
                for r in rows
            ]
            # Stable ordering: highest total first, then user_id ASC.
            data.sort(key=lambda d: (-d["total_watch_seconds"], d["user_id"]))
        else:  # group_by == "day"
            # SQLite ``date()`` accepts unixepoch seconds — divide ms by 1000.
            day_expr = func.date(inner.c.observed_at / 1000, "unixepoch").label("day")
            rows = (
                db.query(
                    inner.c.user_id,
                    day_expr,
                    func.sum(inner.c.poll_interval_ms).label("total_ms"),
                    # bd-gsn3r: same denormalized read as the total branch.
                    func.max(inner.c.dispatcharr_username).label(
                        "dispatcharr_username"
                    ),
                    # bd-fm23o: same Emby denorm as the total branch.
                    func.max(inner.c.emby_user_name).label("emby_user_name"),
                    # bd-r5f0c.4: Plex + Jellyfin denorm — same MAX
                    # rationale as Emby.
                    func.max(inner.c.plex_user_name).label("plex_user_name"),
                    func.max(inner.c.jellyfin_user_name).label(
                        "jellyfin_user_name"
                    ),
                )
                .group_by(inner.c.user_id, day_expr)
                .all()
            )
            data = [
                _build_watch_time_row_day(r)
                for r in rows
            ]
            data.sort(key=lambda d: (d["user_id"], d["day"]))

        return _build_envelope(data, from_ms=from_ms, to_ms=to_ms, group_by=group_by)
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get watch-time totals")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/watch-time/{user_id}")
async def get_watch_time_for_user(
    user_id: int,
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    """Per-user breakdown of watch time by channel (GH-62).

    Query parameters:

    * ``from`` — ISO-8601 UTC range start (inclusive). Optional.
    * ``to`` — ISO-8601 UTC range end (exclusive). Optional.

    Response envelope (per bd-skqln.5):
        ``{data: [...], meta: {from_iso, to_iso, group_by, total_rows},
           pagination: null}``

    Row shape:
        ``{channel_id, channel_name, total_watch_seconds, session_count,
           last_watched, latest_stream_id, latest_stream_name}``

    ``channel_name`` is side-loaded from
    ``UniqueClientConnection.channel_name`` (the skqln.3 step (d)
    precedent) — falls back to ``"Channel <first-8-chars-of-uuid>..."``
    when no connection row carries the name yet (brand-new channels first
    observed only via ``session_telemetry``).

    ``latest_stream_id`` + ``latest_stream_name`` (bd-kh23e) carry the
    most-recently-watched stream identity on that channel within the
    window. Aggregation: ``MAX(observed_at)`` per channel — one row per
    channel, the stream column shows the latest stream the user watched
    on that channel. The frontend renders the label as
    ``[<provider>] - <stream_name>`` with provider name side-loaded from
    the M3U accounts map. Both fields are nullable — older rows
    pre-kh23e and resolver-miss rows surface as ``null``.
    """
    logger.debug(
        "[STATS] GET /api/stats/watch-time/%s from=%s to=%s",
        user_id, from_, to,
    )

    # Auth enforcement: watch-time stats are admin-only. PO directive
    # 2026-05-13: non-admins do not see stats — including their own.
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )

    return _per_user_breakdown_by_channel(
        db,
        source="dispatcharr",
        identifier=user_id,
        from_=from_,
        to=to,
    )


def _per_user_breakdown_by_channel(
    db: Session,
    *,
    source: str,
    identifier,
    from_: Optional[str],
    to: Optional[str],
):
    """Build the per-channel watch-time breakdown for one user (any source).

    bd-fm23o (final bead of EPIC bd-2cenq): factored out of
    :func:`get_watch_time_for_user` so the dispatcharr-keyed and
    emby-keyed endpoints can share aggregation logic without each
    sprouting its own copy.

    Args:
        db: SQLAlchemy session (caller-provided so tests can override).
        source: ``"dispatcharr"`` (filter by ``session_telemetry.user_id ==
            identifier``) or ``"emby"`` (filter by
            ``session_telemetry.emby_user_id == identifier``). Any other
            value is a programmer error and raises ``ValueError`` — the
            caller validates before reaching this helper.
        identifier: For ``source="dispatcharr"`` this is the integer ECM
            user_id. For ``source="emby"`` this is the Emby user GUID
            (string). Both column types are nullable in the schema, so a
            no-match identifier returns an empty data list rather than
            an error.
        from_, to: ISO-8601 UTC range bounds (forwarded from the route).
    """
    if source == "dispatcharr":
        user_filter_col = SessionTelemetry.user_id
    elif source == "emby":
        user_filter_col = SessionTelemetry.emby_user_id
    else:  # pragma: no cover — guarded at route layer
        raise ValueError(f"unknown attribution source: {source!r}")

    from_ms = _parse_iso_utc(from_, param="from") if from_ else None
    to_ms = _parse_iso_utc(to, param="to") if to else None
    if from_ms is not None and to_ms is not None and from_ms >= to_ms:
        return _build_envelope([], from_ms=from_ms, to_ms=to_ms, group_by="channel")

    try:
        distinct = _distinct_poll_subquery(db)
        # The distinct subquery groups by (user_id, channel_id,
        # observed_at) and surfaces emby_user_name but not emby_user_id.
        # For source="emby" we need to filter on the raw column, so we
        # apply the filter against ``SessionTelemetry`` directly via a
        # correlated inner-join surface. Cheap because SQLite folds the
        # subquery into the outer filter.
        if source == "dispatcharr":
            base_q = db.query(distinct).filter(distinct.c.user_id == identifier)
        else:
            # Correlate the (user_id, channel_id, observed_at) tuple back
            # against session_telemetry rows whose emby_user_id matches.
            # An EXISTS subquery keeps the row population the same as the
            # dispatcharr path (one row per distinct (user, channel,
            # observed_at) tuple) while restricting to Emby-attributed
            # ticks. The exists targets the same poll tick the distinct
            # subquery already collapsed, so multi-client overcount is
            # preserved.
            from sqlalchemy import and_, exists
            emby_marker = (
                exists()
                .where(
                    and_(
                        SessionTelemetry.user_id == distinct.c.user_id,
                        SessionTelemetry.channel_id == distinct.c.channel_id,
                        SessionTelemetry.observed_at == distinct.c.observed_at,
                        SessionTelemetry.emby_user_id == identifier,
                    )
                )
                .correlate(distinct)
            )
            base_q = db.query(distinct).filter(emby_marker)
        if from_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at >= from_ms)
        if to_ms is not None:
            base_q = base_q.filter(distinct.c.observed_at < to_ms)
        inner = base_q.subquery()

        # session_count: count distinct session_ids per channel
        # (informational only — not the legacy ``watch_count``
        # state-transition counter, which is not derivable from per-poll
        # rows; see migration 0008 docstring).
        sess_q = (
            db.query(
                SessionTelemetry.channel_id.label("channel_id"),
                func.count(func.distinct(SessionTelemetry.session_id)).label(
                    "session_count"
                ),
            )
            .filter(user_filter_col == identifier)
        )
        if from_ms is not None:
            sess_q = sess_q.filter(SessionTelemetry.observed_at >= from_ms)
        if to_ms is not None:
            sess_q = sess_q.filter(SessionTelemetry.observed_at < to_ms)
        sess_q = sess_q.group_by(SessionTelemetry.channel_id)
        session_counts = {r.channel_id: int(r.session_count) for r in sess_q.all()}

        agg_rows = (
            db.query(
                inner.c.channel_id,
                func.sum(inner.c.poll_interval_ms).label("total_ms"),
                func.max(inner.c.observed_at).label("last_observed_at"),
            )
            .group_by(inner.c.channel_id)
            .all()
        )

        # Side-load channel names from UniqueClientConnection. One query
        # per request — the in-range channel_id set is bounded by the
        # user's viewing footprint (typically O(10) channels), so an
        # IN-list lookup is fine without a join.
        channel_ids = [r.channel_id for r in agg_rows]
        name_map = _load_channel_names(db, channel_ids)
        # bd-kh23e: side-load the latest stream identity per channel.
        # The helper filters by ECM user_id, which is only meaningful for
        # source="dispatcharr". For source="emby" we re-implement the
        # MAX(observed_at) join here with the emby_user_id filter — same
        # aggregation rule, different identity axis.
        if source == "dispatcharr":
            latest_stream_map = _load_latest_stream_identity_per_channel(
                db,
                channel_ids,
                user_id=identifier,
                from_ms=from_ms,
                to_ms=to_ms,
            )
        else:
            latest_stream_map = _load_latest_stream_identity_per_emby_user(
                db,
                channel_ids,
                emby_user_id=identifier,
                from_ms=from_ms,
                to_ms=to_ms,
            )

        data = [
            {
                "channel_id": r.channel_id,
                "channel_name": _channel_name_or_fallback(r.channel_id, name_map),
                "total_watch_seconds": int((r.total_ms or 0) // 1000),
                "session_count": session_counts.get(r.channel_id, 0),
                "last_watched": _ms_to_iso_z(r.last_observed_at),
                # Both null when the resolver couldn't attribute the stream
                # (older rows pre-kh23e, lookup failures). Channel rendered
                # gracefully on the frontend with ``—`` for the stream column.
                "latest_stream_id": latest_stream_map.get(r.channel_id, (None, None))[0],
                "latest_stream_name": latest_stream_map.get(r.channel_id, (None, None))[1],
            }
            for r in agg_rows
        ]
        # Stable ordering: highest total first, then channel_id ASC.
        data.sort(key=lambda d: (-d["total_watch_seconds"], d["channel_id"]))
        return _build_envelope(data, from_ms=from_ms, to_ms=to_ms, group_by="channel")
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "[STATS] Failed to get per-user watch-time breakdown (source=%s)",
            source,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# bd-fm23o: per-user channel breakdown routes split by attribution source
# (final bead of EPIC bd-2cenq — Emby user attribution).
# ---------------------------------------------------------------------------
#
# URL shape rationale (PO decision, recorded in the bead):
#
#   * ``/api/stats/users/dispatcharr/{id}`` keys on the ECM user_id
#     (integer) — same as the legacy ``/api/stats/watch-time/{user_id}``
#     endpoint, just under a source-prefixed URL so the operator can
#     tell from the URL which identity space the id belongs to.
#   * ``/api/stats/users/emby/{id}`` keys on the Emby user GUID
#     (string). This is the new surface that fulfills the epic's
#     acceptance: stats can be reached BY EMBY USER, not just by
#     Dispatcharr-side user.
#   * ``/api/stats/users/{user_id}`` is a deprecated alias that routes
#     to the dispatcharr behavior. Kept for back-compat with any
#     consumer that started wiring against the natural URL before the
#     source split landed. Logs a WARN per call so operators / log
#     analysis can see when consumers still use it.
#
# Deprecation removal date: not scheduled yet. The alias is a
# comment-only marker until we see whether any consumer actually
# depends on it.


@router.get("/users/dispatcharr/{user_id}")
async def get_user_breakdown_by_dispatcharr_id(
    user_id: int,
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    """Per-channel watch-time breakdown keyed by ECM (Dispatcharr) user_id.

    bd-fm23o: source-prefixed sibling of ``/api/stats/watch-time/{user_id}``
    introduced as the final bead of EPIC bd-2cenq. Same response shape,
    same auth posture (admin-only), same aggregation rules — the only
    behavioral difference vs. the legacy endpoint is the URL shape.

    See :func:`_per_user_breakdown_by_channel` for the row contract and
    side-load behavior. ``user_id`` is the integer ECM user identifier
    (same key as ``session_telemetry.user_id``).
    """
    logger.debug(
        "[STATS] GET /api/stats/users/dispatcharr/%s from=%s to=%s",
        user_id, from_, to,
    )
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )
    return _per_user_breakdown_by_channel(
        db,
        source="dispatcharr",
        identifier=user_id,
        from_=from_,
        to=to,
    )


@router.get("/users/emby/{emby_user_id}")
async def get_user_breakdown_by_emby_id(
    emby_user_id: str,
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    """Per-channel watch-time breakdown keyed by Emby user GUID.

    bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution):
    surfaces watch-time aggregations for sessions ECM attributed to
    one specific Emby user. The ``emby_user_id`` path parameter is the
    Emby ``UserId`` field (a GUID-shaped string like
    ``b5c2a1e8-...``) persisted to ``session_telemetry.emby_user_id``
    by :meth:`BandwidthTracker._collect_emby_attributions`.

    Same response shape and admin-only auth as the dispatcharr-keyed
    sibling — see :func:`_per_user_breakdown_by_channel`.
    """
    logger.debug(
        "[STATS] GET /api/stats/users/emby/%s from=%s to=%s",
        emby_user_id, from_, to,
    )
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )
    return _per_user_breakdown_by_channel(
        db,
        source="emby",
        identifier=emby_user_id,
        from_=from_,
        to=to,
    )


@router.get("/users/{user_id}")
async def get_user_breakdown_deprecated_alias(
    user_id: int,
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
):
    """DEPRECATED alias for ``/api/stats/users/dispatcharr/{user_id}``.

    bd-fm23o: kept for back-compat with any consumer that wired against
    the natural URL before the source split landed. Defaults to the
    Dispatcharr-source behavior (the pre-bd-fm23o contract).

    Each call logs a WARN with the URL so operators / log analysis can
    see whether any consumer is still using it. Removal is not yet
    scheduled — the deprecation marker stays comment-only until usage
    data shows it's safe to drop.
    """
    logger.warning(
        "[STATS] Deprecated alias /api/stats/users/%s called — "
        "use /api/stats/users/dispatcharr/%s instead (bd-fm23o)",
        user_id, user_id,
    )
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Watch-time stats are admin-only",
        )
    return _per_user_breakdown_by_channel(
        db,
        source="dispatcharr",
        identifier=user_id,
        from_=from_,
        to=to,
    )


def _pick_display_name_and_source(
    *,
    emby_user_name: Optional[str],
    dispatcharr_username: Optional[str],
    plex_user_name: Optional[str] = None,
    jellyfin_user_name: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Pick the per-row display name + attribution source (bd-r5f0c.4).

    Extends bd-fm23o (Emby > Dispatcharr) to the full multi-source
    precedence: Emby > Plex > Jellyfin > Dispatcharr. Each media
    source's username, when present, represents the TRUE viewer
    identity for that row — the Dispatcharr-side username is the
    proxy/API account name, which is what the epic exists to replace.

    Why Emby wins: every match guarantee is equivalent across sources
    (single concurrent session on a matching item), so ordering is
    historical (Emby shipped first in bd-fm23o) plus PO direction:
    operators with both Emby and Plex configured almost always have
    one as the primary surface and the other as a secondary.
    Emby > Plex > Jellyfin keeps the W4 precedence simple to reason
    about. W8 will exercise the spill behavior across all 7 ordered
    sub-cases.

    Args:
        emby_user_name: Emby attribution from the row (or None).
        dispatcharr_username: Dispatcharr proxy username from the row.
        plex_user_name: Plex attribution from the row (bd-r5f0c.4).
        jellyfin_user_name: Jellyfin attribution from the row (bd-r5f0c.4).

    Returns:
        ``(display_name, attribution_source)`` — source is one of
        ``"emby"``, ``"plex"``, ``"jellyfin"``, ``"dispatcharr"``.
        ``display_name`` may still be ``None`` when no source provided
        a name (legacy rows pre-bd-gsn3r); the frontend renders that
        as "Unknown viewer".
    """
    if emby_user_name:
        return emby_user_name, "emby"
    if plex_user_name:
        return plex_user_name, "plex"
    if jellyfin_user_name:
        return jellyfin_user_name, "jellyfin"
    return dispatcharr_username, "dispatcharr"


def _build_watch_time_row_total(row) -> dict:
    """Render one ``/api/stats/watch-time?group_by=total`` row."""
    display_name, source = _pick_display_name_and_source(
        emby_user_name=row.emby_user_name,
        dispatcharr_username=row.dispatcharr_username,
        # bd-r5f0c.4: plex / jellyfin attribution feed the precedence.
        # ``getattr`` defaults to ``None`` so older row objects (test
        # seams that build rows without these labels) keep working.
        plex_user_name=getattr(row, "plex_user_name", None),
        jellyfin_user_name=getattr(row, "jellyfin_user_name", None),
    )
    return {
        "user_id": row.user_id,
        "username": display_name,
        "attribution_source": source,
        "total_watch_seconds": int((row.total_ms or 0) // 1000),
        "last_watched": _ms_to_iso_z(row.last_observed_at),
    }


def _build_watch_time_row_day(row) -> dict:
    """Render one ``/api/stats/watch-time?group_by=day`` row."""
    display_name, source = _pick_display_name_and_source(
        emby_user_name=row.emby_user_name,
        dispatcharr_username=row.dispatcharr_username,
        plex_user_name=getattr(row, "plex_user_name", None),
        jellyfin_user_name=getattr(row, "jellyfin_user_name", None),
    )
    return {
        "user_id": row.user_id,
        "username": display_name,
        "attribution_source": source,
        "day": row.day,
        "watch_seconds": int((row.total_ms or 0) // 1000),
    }


def _load_channel_names(db: Session, channel_ids):
    """Bulk-resolve ``channel_id -> channel_name`` via UniqueClientConnection.

    Picks an arbitrary name per channel — the connection table stores one
    row per (ip, channel) viewing session and ECM keeps the channel name
    cached in every row, so they all agree under normal operation. ``MAX``
    is the cheap, stable picker.
    """
    if not channel_ids:
        return {}
    rows = (
        db.query(
            UniqueClientConnection.channel_id,
            func.max(UniqueClientConnection.channel_name).label("channel_name"),
        )
        .filter(UniqueClientConnection.channel_id.in_(channel_ids))
        .group_by(UniqueClientConnection.channel_id)
        .all()
    )
    return {r.channel_id: r.channel_name for r in rows}


def _load_latest_stream_identity_per_channel(
    db: Session,
    channel_ids,
    *,
    user_id: int,
    from_ms: Optional[int],
    to_ms: Optional[int],
):
    """Bulk-resolve ``channel_id -> (latest_stream_id, latest_stream_name)`` for one user.

    bd-kh23e: side-loader for the watch-time-by-user breakdown. Returns a
    map keyed by ``channel_id`` whose value is a 2-tuple of the stream
    identity on the row with the highest ``observed_at`` for the
    ``(user_id, channel_id)`` pair within the optional time window.

    Implementation: a per-channel ``MAX(observed_at)`` subquery joins back
    to ``session_telemetry`` to pull the corresponding stream_id +
    stream_name. The user_id + observed_at filters mirror the outer
    aggregation so the per-channel "latest" picks the same row population
    the user is shown.

    NULL handling: a channel whose latest row has ``stream_id=NULL`` and
    ``stream_name=NULL`` (older rows pre-kh23e or resolver-miss rows)
    still appears in the map with both tuple elements set to ``None``.
    Channels not present in ``channel_ids`` are simply absent from the
    map — callers use ``.get(channel_id, (None, None))`` to default
    gracefully.
    """
    if not channel_ids:
        return {}

    # Subquery: the latest observed_at per channel for this user, within
    # the optional window. Mirrors the outer aggregate's filter set so
    # the picked row is from the same population the user sees.
    latest_q = (
        db.query(
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.observed_at).label("max_observed_at"),
        )
        .filter(SessionTelemetry.user_id == user_id)
        .filter(SessionTelemetry.channel_id.in_(channel_ids))
    )
    if from_ms is not None:
        latest_q = latest_q.filter(SessionTelemetry.observed_at >= from_ms)
    if to_ms is not None:
        latest_q = latest_q.filter(SessionTelemetry.observed_at < to_ms)
    latest_sq = latest_q.group_by(SessionTelemetry.channel_id).subquery()

    # Join back: pull stream identity for the matched (channel, max ts).
    # ``MAX(stream_id)`` / ``MAX(stream_name)`` collapse the rare case
    # where two rows share the highest observed_at (e.g., two concurrent
    # clients on the same channel at the same poll-tick): both rows
    # carry the same stream identity by construction (one resolver pass
    # per poll), so ``MAX`` is the cheap, deterministic picker.
    rows = (
        db.query(
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.stream_id).label("stream_id"),
            func.max(SessionTelemetry.stream_name).label("stream_name"),
        )
        .join(
            latest_sq,
            (SessionTelemetry.channel_id == latest_sq.c.channel_id)
            & (SessionTelemetry.observed_at == latest_sq.c.max_observed_at),
        )
        .filter(SessionTelemetry.user_id == user_id)
        .group_by(SessionTelemetry.channel_id)
        .all()
    )
    return {r.channel_id: (r.stream_id, r.stream_name) for r in rows}


def _load_latest_stream_identity_per_emby_user(
    db: Session,
    channel_ids,
    *,
    emby_user_id: str,
    from_ms: Optional[int],
    to_ms: Optional[int],
):
    """Same as :func:`_load_latest_stream_identity_per_channel` but keyed by Emby user.

    bd-fm23o: side-loader for the ``/api/stats/users/emby/{id}`` endpoint
    (final bead of EPIC bd-2cenq). Returns the latest stream identity
    per channel for rows whose ``emby_user_id`` matches — same
    MAX(observed_at)-per-channel aggregation rule as the
    Dispatcharr-source helper so the two endpoints render identical
    label semantics on the frontend.
    """
    if not channel_ids:
        return {}

    latest_q = (
        db.query(
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.observed_at).label("max_observed_at"),
        )
        .filter(SessionTelemetry.emby_user_id == emby_user_id)
        .filter(SessionTelemetry.channel_id.in_(channel_ids))
    )
    if from_ms is not None:
        latest_q = latest_q.filter(SessionTelemetry.observed_at >= from_ms)
    if to_ms is not None:
        latest_q = latest_q.filter(SessionTelemetry.observed_at < to_ms)
    latest_sq = latest_q.group_by(SessionTelemetry.channel_id).subquery()

    rows = (
        db.query(
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.stream_id).label("stream_id"),
            func.max(SessionTelemetry.stream_name).label("stream_name"),
        )
        .join(
            latest_sq,
            (SessionTelemetry.channel_id == latest_sq.c.channel_id)
            & (SessionTelemetry.observed_at == latest_sq.c.max_observed_at),
        )
        .filter(SessionTelemetry.emby_user_id == emby_user_id)
        .group_by(SessionTelemetry.channel_id)
        .all()
    )
    return {r.channel_id: (r.stream_id, r.stream_name) for r in rows}


def _load_latest_stream_identity_per_provider_channel(
    db: Session,
    channel_ids,
    *,
    from_ms: int,
    to_ms: int,
):
    """Bulk-resolve ``(provider_id, channel_id) -> (latest_stream_id, latest_stream_name)``.

    bd-kh23e: side-loader for the channel-heatmap. Returns a map keyed
    by ``(provider_id, channel_id)`` tuple whose value is the latest
    stream identity surfaced on the row with ``MAX(observed_at)`` for
    that (provider, channel) bucket within the window.

    Same MAX(observed_at) aggregation rule as the watch-time-by-user
    breakdown — the frontend renders both surfaces with the same
    ``[<provider>] - <stream_name>`` label format so the rule needs to
    be consistent.

    ``provider_id`` may be ``None`` in either side of the tuple — NULL
    provider buckets ("Unknown") still carry stream identity when the
    resolver attributed the stream but the upstream provider was
    deleted; the heatmap row still appears and the operator sees which
    stream ran out of provider attribution.
    """
    if not channel_ids:
        return {}
    latest_q = (
        db.query(
            SessionTelemetry.provider_id.label("provider_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.observed_at).label("max_observed_at"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .filter(SessionTelemetry.channel_id.in_(channel_ids))
        .group_by(SessionTelemetry.provider_id, SessionTelemetry.channel_id)
        .subquery()
    )

    # Join back to pick the matched row's stream identity. ``MAX`` over
    # stream_id / stream_name collapses ties on observed_at (concurrent
    # clients on the same poll-tick) deterministically — all such rows
    # carry the same identity by construction.
    #
    # NULL-safety on the join: SQLAlchemy / SQLite treat ``NULL = NULL``
    # as UNKNOWN in a join predicate, which would drop NULL-provider
    # cells silently. Use ``IS NOT DISTINCT FROM`` semantics via
    # explicit ``OR (both NULL)`` so the join survives NULL providers.
    rows = (
        db.query(
            SessionTelemetry.provider_id.label("provider_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            func.max(SessionTelemetry.stream_id).label("stream_id"),
            func.max(SessionTelemetry.stream_name).label("stream_name"),
        )
        .join(
            latest_sq := latest_q,
            (
                (SessionTelemetry.channel_id == latest_sq.c.channel_id)
                & (SessionTelemetry.observed_at == latest_sq.c.max_observed_at)
                & (
                    (SessionTelemetry.provider_id == latest_sq.c.provider_id)
                    | (
                        SessionTelemetry.provider_id.is_(None)
                        & latest_sq.c.provider_id.is_(None)
                    )
                )
            ),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .group_by(SessionTelemetry.provider_id, SessionTelemetry.channel_id)
        .all()
    )
    return {
        (r.provider_id, r.channel_id): (r.stream_id, r.stream_name)
        for r in rows
    }


# =============================================================================
# GH-59 per-provider stats read API (bd-skqln.16)
# =============================================================================
#
# Four endpoints that aggregate ``session_telemetry`` per provider for the
# Stats v2 Providers panel (skqln.18). All four reuse skqln.5's auth seam
# (``get_watch_time_caller``) and {data, meta, pagination} envelope; the
# panel is admin-only (PO directive 2026-05-13).
#
# Indexes used (skqln.2 / migration 0006 + the trailing-bytes index
# co-located with skqln.2):
#   - idx_session_telemetry_provider_observed     (queries 1, 2, 4)
#   - idx_session_telemetry_provider_channel_observed_bytes  (query 3)
#
# Multi-client overcount guard: queries 2 (watch-time) and 4 (bitrate)
# aggregate quantities that must be counted once per (channel, observed_at)
# tuple — concurrent clients on the same channel-poll-tick double-count
# otherwise. Both use the ``_distinct_provider_poll_subquery`` collapse;
# query 1 (buffering) sums an inherently per-poll quantity and does not
# need the collapse; query 3 (heatmap) sums per-poll bytes_delta similarly.


_VALID_WINDOW = {"7d": 7, "30d": 30, "90d": 90}
_VALID_BUCKET = {"hour", "day"}
_HEATMAP_DEFAULT_TOP_N = 50
_HEATMAP_MAX_TOP_N = 500  # absolute cap regardless of caller param


def _check_admin(caller: Optional[User]) -> None:
    """Reject non-admin callers with 403. PO directive 2026-05-13:
    per-provider stats are admin-only.

    Mirrors the inline check in ``get_watch_time_by_user`` — extracted so
    the four provider-stats handlers don't duplicate it. ``caller`` is
    ``None`` when global auth is disabled (test-default + operator-only
    deployments); treat that as admin-equivalent.
    """
    if caller is not None and not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Provider stats are admin-only",
        )


def _resolve_window_ms(window: str) -> tuple[int, int]:
    """Resolve a ``window`` literal to (from_ms, to_ms).

    ``to`` is the current wall time (anchor); ``from`` is N days back. Both
    are unix-epoch milliseconds for direct comparison with
    ``session_telemetry.observed_at``.
    """
    if window not in _VALID_WINDOW:
        raise HTTPException(
            status_code=400,
            detail=f"window must be one of {sorted(_VALID_WINDOW)}",
        )
    days = _VALID_WINDOW[window]
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    return int(from_dt.timestamp() * 1000), int(to_dt.timestamp() * 1000)


def _bucket_expr(bucket: str, observed_at_col):
    """Return a SQLAlchemy expression that floors ``observed_at`` (ms-epoch)
    to the start of its hour or UTC-day bucket, in ISO-8601 with trailing Z.

    Uses SQLite's ``strftime`` over ``unixepoch``: ``observed_at_col / 1000``
    converts ms → seconds, then ``strftime('%Y-%m-%dT%H:00:00Z', ..., 'unixepoch')``
    yields the floor as a string. Day bucket uses ``T00:00:00Z`` suffix.
    """
    if bucket == "hour":
        fmt = "%Y-%m-%dT%H:00:00Z"
    else:  # day
        fmt = "%Y-%m-%dT00:00:00Z"
    return func.strftime(fmt, observed_at_col / 1000, "unixepoch")


def _build_provider_envelope(data, *, from_ms, to_ms, **meta_extras):
    """Provider-stats response envelope. Same {data, meta, pagination}
    shape as skqln.5 with ``meta`` extended for window/bucket/top_n etc.
    """
    meta = {
        "from_iso": _ms_to_iso_z(from_ms),
        "to_iso": _ms_to_iso_z(to_ms),
        "total_rows": len(data),
    }
    meta.update(meta_extras)
    return {"data": data, "meta": meta, "pagination": None}


def _distinct_provider_poll_subquery(db: Session, from_ms: int, to_ms: int):
    """DISTINCT (provider_id, channel_id, observed_at) collapse subquery.

    Returns one row per (provider, channel, poll-tick) tuple with the poll
    interval and bytes. Collapses multi-client overcount in per-provider
    aggregations the same way ``_distinct_poll_subquery`` does for
    per-user aggregations (skqln.5).

    ``MAX(poll_interval_ms)`` / ``MAX(bytes_delta)`` are defensive: in the
    rare case where two clients report different values for the same
    (provider, channel, observed_at), take the larger one — never
    overcount, but don't undercount either. Under normal operation all
    concurrent clients report the same values from the same upstream poll.

    NOTE: SQLite's ``GROUP BY`` treats ``NULL`` values as a single group,
    so rows with ``provider_id = NULL`` aggregate into one "Unknown" bucket
    correctly with no special handling.
    """
    return (
        db.query(
            SessionTelemetry.provider_id.label("provider_id"),
            SessionTelemetry.channel_id.label("channel_id"),
            SessionTelemetry.observed_at.label("observed_at"),
            func.max(SessionTelemetry.poll_interval_ms).label("poll_interval_ms"),
            func.max(SessionTelemetry.bytes_delta).label("bytes_delta"),
        )
        .filter(SessionTelemetry.observed_at >= from_ms)
        .filter(SessionTelemetry.observed_at < to_ms)
        .group_by(
            SessionTelemetry.provider_id,
            SessionTelemetry.channel_id,
            SessionTelemetry.observed_at,
        )
        .subquery()
    )


@router.get("/providers/buffering")
async def get_providers_buffering(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    window: str = "7d",
    bucket: str = "hour",
):
    """Per-provider channel-event time-series (bd-ov5vb, broadens
    bd-skqln.16 / GH-59).

    Query params:

    * ``window`` — one of ``7d`` / ``30d`` / ``90d``. Default ``7d``.
    * ``bucket`` — ``hour`` or ``day``. Default ``hour``.

    Response row shape:
        ``{provider_id, time_bucket, buffer_event_count,
        reconnect_event_count, error_event_count, switch_event_count,
        total_event_count}``

    Pre-bd-ov5vb history: this endpoint historically returned only
    ``buffer_event_count``. Live verification on the PO's instance
    (2026-05-15) found that ``channel_buffering`` events are rare on
    real installs — the operationally-meaningful health signals are
    ``channel_reconnect`` / ``channel_error`` / ``stream_switch``,
    which the ingest layer
    (``BandwidthTracker._collect_channel_events``) now writes to
    dedicated columns on ``session_telemetry`` (migration 0013). This
    endpoint surfaces all four counters alongside their pre-summed
    total so the Providers panel can render a "Channel events" view
    without a second round-trip. The ``buffer_event_count`` field is
    preserved for back-compat with any consumer that wired against
    the pre-bd-ov5vb shape (it now typically reads zero on real
    installs — the truthful posture for installs whose Dispatcharr
    does not emit ``channel_buffering``).

    Each per-type counter is inherently per-poll (one column per
    row), so no DISTINCT-collapse is needed. ``NULL`` ``provider_id``
    surfaces as a row with ``provider_id: null`` (operators need the
    attribution gap visible). The URL path stays ``/buffering`` for
    back-compat with any external dashboard or alerting integration
    that has the path hard-coded; renaming the path would break
    those consumers without a benefit beyond aesthetics. The bead
    documentation surfaces the semantic broadening.
    """
    _check_admin(caller)
    if bucket not in _VALID_BUCKET:
        raise HTTPException(
            status_code=400,
            detail=f"bucket must be one of {sorted(_VALID_BUCKET)}",
        )
    from_ms, to_ms = _resolve_window_ms(window)

    try:
        bucket_col = _bucket_expr(bucket, SessionTelemetry.observed_at).label(
            "time_bucket"
        )
        rows = (
            db.query(
                SessionTelemetry.provider_id.label("provider_id"),
                bucket_col,
                func.sum(SessionTelemetry.buffer_event_count).label("buffer"),
                func.sum(SessionTelemetry.reconnect_event_count).label("reconnect"),
                func.sum(SessionTelemetry.error_event_count).label("error"),
                func.sum(SessionTelemetry.switch_event_count).label("switch"),
            )
            .filter(SessionTelemetry.observed_at >= from_ms)
            .filter(SessionTelemetry.observed_at < to_ms)
            .group_by(SessionTelemetry.provider_id, bucket_col)
            .all()
        )
        data = []
        for r in rows:
            buffer_n = int(r.buffer or 0)
            reconnect_n = int(r.reconnect or 0)
            error_n = int(r.error or 0)
            switch_n = int(r.switch or 0)
            data.append({
                "provider_id": r.provider_id,
                "time_bucket": r.time_bucket,
                "buffer_event_count": buffer_n,
                "reconnect_event_count": reconnect_n,
                "error_event_count": error_n,
                "switch_event_count": switch_n,
                # Pre-summed so the frontend's "Channel events" column
                # can render a single primary number without summing
                # four fields in the render path. The breakdown
                # tooltip (bd-1x5v0 option a) consumes the per-type
                # counters directly.
                "total_event_count": (
                    buffer_n + reconnect_n + error_n + switch_n
                ),
            })
        # Stable ordering: provider_id NULLS LAST, then bucket ASC.
        data.sort(
            key=lambda d: (
                d["provider_id"] is None,
                d["provider_id"] or 0,
                d["time_bucket"],
            )
        )
        return _build_provider_envelope(
            data, from_ms=from_ms, to_ms=to_ms, window=window, bucket=bucket
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get provider buffering stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/providers/watch-time")
async def get_providers_watch_time(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    window: str = "7d",
):
    """Total watch time per provider (bd-skqln.16, GH-59).

    Query params:

    * ``window`` — one of ``7d`` / ``30d`` / ``90d``. Default ``7d``.

    Response row shape:
        ``{provider_id, total_watch_seconds}``

    Aggregates ``SUM(poll_interval_ms)`` per provider_id, with the
    DISTINCT-(provider_id, channel_id, observed_at) collapse to prevent
    multi-client overcount. ``NULL`` ``provider_id`` surfaces as its own
    row (``provider_id: null``).
    """
    _check_admin(caller)
    from_ms, to_ms = _resolve_window_ms(window)

    try:
        distinct = _distinct_provider_poll_subquery(db, from_ms, to_ms)
        rows = (
            db.query(
                distinct.c.provider_id,
                func.sum(distinct.c.poll_interval_ms).label("total_ms"),
            )
            .group_by(distinct.c.provider_id)
            .all()
        )
        data = [
            {
                "provider_id": r.provider_id,
                "total_watch_seconds": int((r.total_ms or 0) // 1000),
            }
            for r in rows
        ]
        # Stable ordering: highest watch-time first, then provider_id ASC
        # (NULL last).
        data.sort(
            key=lambda d: (
                -d["total_watch_seconds"],
                d["provider_id"] is None,
                d["provider_id"] or 0,
            )
        )
        return _build_provider_envelope(
            data, from_ms=from_ms, to_ms=to_ms, window=window
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get provider watch-time stats")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/providers/channel-heatmap")
async def get_providers_channel_heatmap(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    window: str = "7d",
    top_n: int = _HEATMAP_DEFAULT_TOP_N,
):
    """Provider × channel byte heatmap (bd-skqln.16, GH-59).

    Query params:

    * ``window`` — one of ``7d`` / ``30d`` / ``90d``. Default ``7d``.
    * ``top_n`` — cap the response at the top-N channels by total bytes
      across all providers. Default 50; absolute max 500 (defensive).

    Response row shape:
        ``{provider_id, channel_id, channel_name, bytes,
           latest_stream_id, latest_stream_name}``

    Each row is one cell of the 2D grid (rows=providers, cols=channels).
    Cells with zero bytes are omitted. ``channel_name`` is side-loaded from
    ``UniqueClientConnection`` (skqln.3 step (d) precedent) with the same
    ``"Channel <first-8-chars>..."`` fallback the per-user breakdown uses.

    ``latest_stream_id`` + ``latest_stream_name`` (bd-kh23e) carry the
    most-recently-observed stream identity for the (provider, channel)
    cell within the window. Aggregation: ``MAX(observed_at)`` per
    (provider_id, channel_id) pair. The frontend's heatmap data-table
    fallback renders this as ``[<provider>] - <stream_name>`` so the
    operator can see WHICH stream the bytes were attributed to, not just
    which channel.

    The DBA-flagged covering index ``idx_session_telemetry_provider_channel_observed_bytes``
    (model: ``__table_args__`` of ``SessionTelemetry``) backs this query.
    """
    _check_admin(caller)
    if top_n < 1:
        raise HTTPException(status_code=400, detail="top_n must be >= 1")
    top_n = min(top_n, _HEATMAP_MAX_TOP_N)
    from_ms, to_ms = _resolve_window_ms(window)

    try:
        # First pass: total bytes per channel across all providers — pick the
        # top-N channel_ids by that total.
        channel_totals = (
            db.query(
                SessionTelemetry.channel_id.label("channel_id"),
                func.sum(SessionTelemetry.bytes_delta).label("total"),
            )
            .filter(SessionTelemetry.observed_at >= from_ms)
            .filter(SessionTelemetry.observed_at < to_ms)
            .group_by(SessionTelemetry.channel_id)
            .order_by(func.sum(SessionTelemetry.bytes_delta).desc())
            .limit(top_n)
            .all()
        )
        top_channel_ids = [r.channel_id for r in channel_totals]
        if not top_channel_ids:
            return _build_provider_envelope(
                [], from_ms=from_ms, to_ms=to_ms, window=window, top_n=top_n
            )

        # Second pass: per-(provider, channel) bytes_delta sum, restricted to
        # the top-N channels.
        cells = (
            db.query(
                SessionTelemetry.provider_id.label("provider_id"),
                SessionTelemetry.channel_id.label("channel_id"),
                func.sum(SessionTelemetry.bytes_delta).label("bytes"),
            )
            .filter(SessionTelemetry.observed_at >= from_ms)
            .filter(SessionTelemetry.observed_at < to_ms)
            .filter(SessionTelemetry.channel_id.in_(top_channel_ids))
            .group_by(SessionTelemetry.provider_id, SessionTelemetry.channel_id)
            .all()
        )
        name_map = _load_channel_names(db, top_channel_ids)
        # bd-kh23e: side-load latest stream identity per (provider, channel).
        # MAX(observed_at) per cell — same aggregation rule as the
        # watch-time-by-user breakdown (bd-kh23e). The frontend's
        # heatmap data-table fallback uses these to render the
        # ``[<provider>] - <stream_name>`` label per cell.
        stream_id_map = _load_latest_stream_identity_per_provider_channel(
            db,
            top_channel_ids,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        data = [
            {
                "provider_id": c.provider_id,
                "channel_id": c.channel_id,
                "channel_name": _channel_name_or_fallback(c.channel_id, name_map),
                "bytes": int(c.bytes or 0),
                "latest_stream_id": stream_id_map.get(
                    (c.provider_id, c.channel_id), (None, None)
                )[0],
                "latest_stream_name": stream_id_map.get(
                    (c.provider_id, c.channel_id), (None, None)
                )[1],
            }
            for c in cells
        ]
        # Stable ordering: by bytes DESC, then (provider, channel).
        data.sort(
            key=lambda d: (
                -d["bytes"],
                d["provider_id"] is None,
                d["provider_id"] or 0,
                d["channel_id"],
            )
        )
        return _build_provider_envelope(
            data, from_ms=from_ms, to_ms=to_ms, window=window, top_n=top_n
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get provider channel-heatmap")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/providers/bitrate")
async def get_providers_bitrate(
    db: Session = Depends(get_session),
    caller: Optional[User] = Depends(get_watch_time_caller),
    window: str = "7d",
    bucket: str = "hour",
):
    """Per-provider derived bitrate time-series (bd-skqln.16, GH-59).

    Query params:

    * ``window`` — one of ``7d`` / ``30d`` / ``90d``. Default ``7d``.
    * ``bucket`` — ``hour`` or ``day``. Default ``hour``.

    Response row shape:
        ``{provider_id, time_bucket, bitrate_bps}``

    Computes ``bitrate_bps = SUM(bytes_delta) * 8 * 1000 / SUM(poll_interval_ms)``
    per (provider_id, time_bucket) — the *1000 converts the denominator
    from ms to s so the result is bits/second. Uses the DISTINCT-(provider,
    channel, observed_at) collapse so multi-client polls don't multiply
    the denominator.

    Buckets with ``SUM(poll_interval_ms) == 0`` are skipped (defensive —
    shouldn't happen in practice since poll_interval_ms is a NOT-NULL
    field with check constraint > 0 in the writer).
    """
    _check_admin(caller)
    if bucket not in _VALID_BUCKET:
        raise HTTPException(
            status_code=400,
            detail=f"bucket must be one of {sorted(_VALID_BUCKET)}",
        )
    from_ms, to_ms = _resolve_window_ms(window)

    try:
        distinct = _distinct_provider_poll_subquery(db, from_ms, to_ms)
        bucket_col = _bucket_expr(bucket, distinct.c.observed_at).label("time_bucket")
        rows = (
            db.query(
                distinct.c.provider_id,
                bucket_col,
                func.sum(distinct.c.bytes_delta).label("total_bytes"),
                func.sum(distinct.c.poll_interval_ms).label("total_ms"),
            )
            .group_by(distinct.c.provider_id, bucket_col)
            .all()
        )
        data = []
        for r in rows:
            total_ms = int(r.total_ms or 0)
            if total_ms <= 0:
                continue
            # bytes * 8 / seconds = bits/second. seconds = total_ms / 1000,
            # so bps = total_bytes * 8 * 1000 / total_ms. Integer-truncated
            # — fractional bps is noise at the scales we operate on.
            bps = int((int(r.total_bytes or 0) * 8 * 1000) // total_ms)
            data.append(
                {
                    "provider_id": r.provider_id,
                    "time_bucket": r.time_bucket,
                    "bitrate_bps": bps,
                }
            )
        # Stable ordering: provider_id (NULLS LAST), then time_bucket ASC.
        data.sort(
            key=lambda d: (
                d["provider_id"] is None,
                d["provider_id"] or 0,
                d["time_bucket"],
            )
        )
        return _build_provider_envelope(
            data, from_ms=from_ms, to_ms=to_ms, window=window, bucket=bucket
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[STATS] Failed to get provider bitrate stats")
        raise HTTPException(status_code=500, detail="Internal server error")
