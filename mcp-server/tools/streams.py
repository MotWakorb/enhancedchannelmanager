"""Stream management and health tools."""
import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def _is_not_found(exc: Exception) -> bool:
    """True if ``exc`` represents an upstream HTTP 404.

    The ECM client wraps upstream failures in a ``RuntimeError`` (built by
    ``ecm_client._http_error``) chained via ``raise ... from e`` to the original
    ``httpx.HTTPStatusError``. Prefer the precise status code on the chained
    cause; fall back to the message text (``-> HTTP 404``) for robustness.
    """
    cause = getattr(exc, "__cause__", None)
    resp = getattr(cause, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 404:
        return True
    return "HTTP 404" in str(exc)


# enhancedchannelmanager-jnzst: the MCP-local fuzzy scorer (_strip_stream_prefix,
# _normalize, _generate_variants, _normalize_channel, _score_match, and the
# _fuzzy_helpers export) was DELETED. Name matching now flows through the single
# backend scoring core via GET /api/auto-creation/fuzzy-preview (see
# _preview_triples below). channels.build_channel_lineup was repointed too.


async def _preview_triples(
    client,
    group_ids: list[int],
    min_score: float,
    allow_no_callsign: bool = False,
) -> list[dict]:
    """Fetch ALL ADMISSIBLE scored (stream, channel) triples from the backend.

    Calls the backend ``GET /api/auto-creation/fuzzy-preview`` endpoint
    (Component B, jnzst) — the SAME shared scoring core AND admission policy the
    rule path uses (``services.dedup_matcher.is_admissible``). The endpoint
    returns ADMISSIBLE-only triples: M1 callsign ``conflict`` pairs are never
    returned, and no-callsign ``absent`` pairs only when ``allow_no_callsign``
    is True at score >= 0.90. This is why the write consumers below
    (match_streams_to_channels apply=true, build_channel_lineup) can assign
    purely on ``score`` and still inherit M1/M2 — they never see a non-admissible
    triple (FIX 2). Pages through every result so callers can rank/group locally.
    Pure read — the preview endpoint never writes.

    ``allow_no_callsign`` defaults False so the write path REQUIRES a parseable
    callsign on both sides (Q1 default policy).

    Module-level (not nested in ``register``) so channels.build_channel_lineup
    can import it after the MCP-local scorer deletion.
    """
    triples: list[dict] = []
    page = 1
    while True:
        result = await client.call_endpoint(
            ENDPOINTS["ac_fuzzy_preview"],
            query={
                "group_ids": group_ids,
                "min_score": min_score,
                "allow_no_callsign": allow_no_callsign,
                "page": page,
                "page_size": 200,
            },
        )
        if not isinstance(result, dict):
            break
        triples.extend(result.get("triples", []))
        if page >= (result.get("total_pages") or 0):
            break
        page += 1
    return triples


def _stream_query(*, search=None, m3u_account=None, channel_group_name=None,
                  page=None, page_size=None, include_assignment=None):
    """Build a query dict for ENDPOINTS["streams_list"], dropping None values.

    Note the param names match the backend (``m3u_account``, ``channel_group_name``)
    — the MCP tools used to send ``provider_id`` / ``group`` here, which the
    backend silently ignored (drift fixed in bd-vtghg Phase 2).

    ``include_assignment`` asks the backend to annotate each stream with
    channel-assignment status (has_channel / assigned_channel_ids). It is only
    sent when truthy so the default list path keeps the backend on its cheap
    no-channel-fetch path.
    """
    q = {
        "search": search,
        "m3u_account": m3u_account,
        "channel_group_name": channel_group_name,
        "page": page,
        "page_size": page_size,
        "include_assignment": True if include_assignment else None,
    }
    return {k: v for k, v in q.items() if v is not None}


def _filter_by_assigned(streams: list[dict], assigned: bool | None) -> list[dict]:
    """Filter streams by channel-assignment status.

    assigned=True  -> only streams in at least one channel (has_channel)
    assigned=False -> only unassigned streams
    assigned=None  -> no filtering (returned unchanged)
    """
    if assigned is None:
        return streams
    return [s for s in streams if bool(s.get("has_channel")) is assigned]


def _stream_assignment_suffix(s: dict) -> str:
    """Render the `` -> channel N`` / `` [unassigned]`` assignment suffix.

    Only emitted when the stream dict carries assignment fields (i.e. the
    backend was called with include_assignment). Returns "" otherwise so the
    default rendering is unchanged.
    """
    if "has_channel" not in s:
        return ""
    if not s.get("has_channel"):
        return " [unassigned]"
    channel_ids = s.get("assigned_channel_ids") or []
    if len(channel_ids) > 1:
        return " -> channels " + ", ".join(str(c) for c in channel_ids)
    cid = s.get("assigned_channel_id")
    if cid is None and channel_ids:
        cid = channel_ids[0]
    return f" -> channel {cid}"


async def _build_id_name_maps(client) -> tuple[dict, dict]:
    """Fetch provider (m3u_account) and channel-group id->name lookup maps.

    Raw Dispatcharr stream objects carry ``m3u_account`` (provider id) and
    ``channel_group`` (group id) — numbers, not names. The streams-list
    endpoint enriches list results with ``channel_group_name``, but
    ``/api/channels/{id}/streams`` and ``/api/streams/by-ids`` return the raw
    objects, so the MCP tool resolves names itself (lq38l.13 #2).

    Returns ``(provider_map, group_map)``. A failed lookup degrades to an empty
    map so the caller falls back to ids rather than erroring.
    """
    provider_map: dict = {}
    group_map: dict = {}
    try:
        providers = await client.call_endpoint(ENDPOINTS["m3u_list_providers"])
        for p in providers or []:
            if isinstance(p, dict) and p.get("id") is not None:
                provider_map[p["id"]] = p.get("name")
    except Exception as e:  # degrade gracefully — fall back to ids
        logger.warning("[MCP] could not resolve provider names: %s", e)
    try:
        groups = await client.call_endpoint(ENDPOINTS["groups_list"])
        for g in groups or []:
            if isinstance(g, dict) and g.get("id") is not None:
                group_map[g["id"]] = g.get("name")
    except Exception as e:
        logger.warning("[MCP] could not resolve channel-group names: %s", e)
    return provider_map, group_map


def _stream_group_provider_suffix(s: dict, provider_map: dict, group_map: dict) -> str:
    """Render the `` [Group] from Provider`` suffix for a raw stream dict.

    Prefers an already-enriched ``channel_group_name`` / ``provider_name`` if
    present; otherwise resolves the numeric ``channel_group`` / ``m3u_account``
    ids through the supplied maps, falling back to the raw id when no name is
    known.
    """
    group = s.get("channel_group_name") or s.get("group") or ""
    if not group:
        gid = s.get("channel_group")
        if gid is not None:
            group = group_map.get(gid) or f"group {gid}"

    provider = s.get("provider_name") or ""
    if not provider:
        pid = s.get("m3u_account")
        if pid is not None:
            provider = provider_map.get(pid) or f"provider {pid}"

    suffix = f" [{group}]" if group else ""
    if provider:
        suffix += f" from {provider}"
    return suffix


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_streams(
        group: str | None = None,
        provider_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
        assigned: bool | None = None,
    ) -> str:
        """List streams with optional filtering.

        Args:
            group: Filter by stream group name
            provider_id: Filter by M3U provider/account ID
            search: Search streams by name
            page: Page number (default 1)
            page_size: Results per page (default 50, max 100)
            assigned: Filter by channel-assignment status. True -> only streams
                assigned to a channel; False -> only unassigned streams; None
                (default) -> no filtering. When set, the backend is asked to
                include assignment status (costs an extra cached channel fetch),
                so leave it None unless you need it.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["streams_list"],
                query=_stream_query(
                    search=search, m3u_account=provider_id, channel_group_name=group,
                    page=page, page_size=min(page_size, 100),
                    include_assignment=assigned is not None,
                ),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
                total = len(streams)

            streams = _filter_by_assigned(streams, assigned)

            if not streams:
                return "No streams found."

            lines = [f"Showing {len(streams)} of {total} streams (page {page}):"]
            for s in streams:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group_name = s.get("group", "")
                provider = s.get("provider_name", "")
                info = f" [{group_name}]" if group_name else ""
                info += f" from {provider}" if provider else ""
                info += _stream_assignment_suffix(s)
                lines.append(f"  {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_streams failed: %s", e)
            return f"Error listing streams: {e}"

    @mcp.tool()
    async def get_stream_health() -> str:
        """Get an overview of stream health from the most recent probe results."""
        try:
            client = get_ecm_client()
            summary = await client.call_endpoint(ENDPOINTS["stream_stats_summary"])

            if not summary:
                return "No stream health data available. Run a probe first."

            lines = ["Stream Health Summary:"]
            for key, value in summary.items():
                label = key.replace("_", " ").title()
                lines.append(f"  {label}: {value}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_stream_health failed: %s", e)
            return f"Error getting stream health: {e}"

    @mcp.tool()
    async def probe_streams() -> str:
        """Start probing all streams to check their health. This runs in the background and may take a while."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_probe_all"], timeout=300.0)
            msg = result.get("message", "Check progress in ECM.") if isinstance(result, dict) else ""
            return f"Stream probe started. {msg}"
        except Exception as e:
            logger.error("[MCP] probe_streams failed: %s", e)
            return f"Error starting stream probe: {e}"

    @mcp.tool()
    async def get_probe_progress() -> str:
        """Check the progress of an ongoing stream probe."""
        try:
            client = get_ecm_client()
            p = await client.call_endpoint(ENDPOINTS["stream_stats_probe_progress"])

            if not p.get("in_progress"):
                return "No probe is currently running."

            total = p.get("total", 0)
            current = p.get("current", 0)
            pct = (current / total * 100) if total else 0
            lines = [
                f"Probe in progress: {current}/{total} ({pct:.0f}%)",
                f"  Success: {p.get('success_count', 0)}",
                f"  Failed: {p.get('failed_count', 0)}",
                f"  Skipped: {p.get('skipped_count', 0)}",
            ]
            cur_stream = p.get("current_stream", "")
            if cur_stream:
                lines.append(f"  Currently probing: {cur_stream}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_probe_progress failed: %s", e)
            return f"Error getting probe progress: {e}"

    @mcp.tool()
    async def probe_single_stream(stream_id: int) -> str:
        """Probe a single stream to check its health.

        Note: this manual probe does NOT feed get_probe_results (only the
        scheduled probe task does), but the result IS persisted, so
        get_stream_health and get_streams_by_ids reflect it.
        (See bd enhancedchannelmanager-znc76.5.)

        Args:
            stream_id: The stream ID to probe
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stream_stats_probe_one"], path_args={"stream_id": stream_id},
                timeout=300.0,
            )
            status = result.get("status", result.get("probe_status", "unknown")) if isinstance(result, dict) else "unknown"
            return f"Stream {stream_id} probe complete. Status: {status}"
        except Exception as e:
            logger.error("[MCP] probe_single_stream failed: %s", e)
            return f"Error probing stream {stream_id}: {e}"

    @mcp.tool()
    async def get_struck_out_streams() -> str:
        """List streams that have been struck out due to consecutive probe failures.
        Includes channel associations for each stream."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_struck_out"])

            streams = result.get("streams", []) if isinstance(result, dict) else result
            threshold = result.get("threshold", "?") if isinstance(result, dict) else "?"
            enabled = result.get("enabled", True) if isinstance(result, dict) else True

            if not enabled:
                return "Strike detection is disabled."

            if not streams:
                return f"No struck-out streams (threshold: {threshold} consecutive failures)."

            lines = [f"Struck-out streams ({len(streams)}, threshold: {threshold} failures):"]
            for s in streams[:30]:
                name = s.get("stream_name", s.get("name", "Unknown"))
                sid = s.get("stream_id", s.get("id", "?"))
                failures = s.get("consecutive_failures", s.get("strike_count", "?"))
                channels = s.get("channels", [])
                if channels:
                    ch_info = ", ".join(
                        f"{c.get('name', '?')} (ch_id={c.get('id', '?')})"
                        for c in channels
                    )
                    lines.append(f"  {name} (id={sid}) — {failures} failures — in: {ch_info}")
                else:
                    lines.append(f"  {name} (id={sid}) — {failures} failures — not assigned to any channel")

            if len(streams) > 30:
                lines.append(f"  ... and {len(streams) - 30} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_struck_out_streams failed: %s", e)
            return f"Error getting struck-out streams: {e}"

    @mcp.tool()
    async def get_stale_streams(days: int = 7) -> str:
        """List streams flagged stale by either of two independent signals.

        - not_probed_recently: ECM hasn't ffprobed the stream in `days` days,
          or never has. It may still be listed and playable — just unchecked.
          Run probe_streams to refresh.
        - provider_stale: Dispatcharr's own M3U refresh no longer re-matched
          this stream in the source playlist. The provider may have removed
          it; Dispatcharr will delete it automatically after its own grace
          period. Independent of `days`.

        Distinct from get_struck_out_streams (consecutive probe *failures*):
        a stale stream may be passing every probe it gets.

        This is the HEAVIER of the two stale-stream reports: it re-derives
        both signals (including a fresh ffprobe-age comparison) and returns
        channel associations for each stream. For just the cheap
        Dispatcharr-``is_stale`` id set (no probe-age signal, no channel
        joins), use get_stale_stream_ids instead.

        Args:
            days: Age threshold in days (default 7) for the not_probed_recently
                signal only. Does not affect provider_stale.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_stale"], query={"days": days})

            streams = result.get("streams", []) if isinstance(result, dict) else result
            threshold_days = result.get("threshold_days", days) if isinstance(result, dict) else days

            if not streams:
                return f"No stale streams (probe threshold: {threshold_days} days)."

            lines = [f"Stale streams ({len(streams)}, probe threshold: {threshold_days}+ days):"]
            for s in streams[:30]:
                name = s.get("stream_name") or "Unknown"
                sid = s.get("stream_id", s.get("id", "?"))
                reasons = s.get("reasons", [])
                reason_bits = []
                if "not_probed_recently" in reasons:
                    reason_bits.append(f"last probed: {s.get('last_probed') or 'never'}")
                if "provider_stale" in reasons:
                    reason_bits.append(f"provider last saw it: {s.get('provider_last_seen') or 'unknown'}")
                reason_info = ", ".join(reason_bits) if reason_bits else "stale"

                channels = s.get("channels", [])
                if channels:
                    ch_info = ", ".join(
                        f"{c.get('name', '?')} (ch_id={c.get('id', '?')})"
                        for c in channels
                    )
                    lines.append(f"  {name} (id={sid}) — {reason_info} — in: {ch_info}")
                else:
                    lines.append(f"  {name} (id={sid}) — {reason_info} — not assigned to any channel")

            if len(streams) > 30:
                lines.append(f"  ... and {len(streams) - 30} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_stale_streams failed: %s", e)
            return f"Error getting stale streams: {e}"

    @mcp.tool()
    async def get_stale_stream_ids(bypass_cache: bool = False) -> str:
        """List the Dispatcharr stream IDs currently flagged ``is_stale``.

        Dispatcharr flags a stream stale when its own M3U refresh no longer
        re-matches it in the source playlist. This is the CHEAP signal only
        — a single paged scan of Dispatcharr's ``is_stale`` flag, cached for
        a short TTL, with no ffprobe-age check and no channel-association
        joins. For the heavier report (both staleness signals, channel
        associations, and a configurable probe-age threshold), use
        get_stale_streams instead.

        Args:
            bypass_cache: Force a fresh scan instead of using the cached
                result (default False — the cache TTL is short, so this is
                rarely needed).
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["streams_stale_ids"], query={"bypass_cache": bypass_cache},
            )

            stale_ids = result.get("stale_stream_ids", []) if isinstance(result, dict) else []
            last_seen = result.get("last_seen", {}) if isinstance(result, dict) else {}
            count = result.get("count", len(stale_ids)) if isinstance(result, dict) else len(stale_ids)

            if not stale_ids:
                return "No stale stream IDs (Dispatcharr is_stale flag)."

            lines = [f"Stale stream IDs ({count}):"]
            for sid in stale_ids[:50]:
                seen = last_seen.get(str(sid), last_seen.get(sid))
                lines.append(f"  id={sid} — last seen: {seen or 'unknown'}")
            if count > 50:
                lines.append(f"  ... and {count - 50} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_stale_stream_ids failed: %s", e)
            return f"Error getting stale stream ids: {e}"

    @mcp.tool()
    async def cleanup_struck_out_streams(
        delete_empty_channels: bool = False,
        plan_stream_ids: list[int] | None = None,
        plan_channel_ids: list[int] | None = None,
    ) -> str:
        """Remove all struck-out streams from their channels in one operation.

        This is a bulk cleanup tool that:
        1. Finds all struck-out streams (consecutive probe failures above threshold)
        2. Removes them from any channels they belong to
        3. Optionally deletes channels left with no streams after removal

        Args:
            delete_empty_channels: If True, delete any channels that have no streams
                                   remaining after the struck-out streams are removed
        """
        try:
            client = get_ecm_client()

            # Get struck-out streams with channel associations
            if plan_stream_ids is not None:
                streams = [{"stream_id": value} for value in plan_stream_ids]
                threshold = "planned"
            else:
                struck_result = await client.call_endpoint(ENDPOINTS["stream_stats_struck_out"])
                streams = struck_result.get("streams", []) if isinstance(struck_result, dict) else struck_result
                threshold = struck_result.get("threshold", "?") if isinstance(struck_result, dict) else "?"

            if not streams:
                return f"No struck-out streams to clean up (threshold: {threshold})."

            stream_ids = []
            stream_names = {}
            for s in streams:
                sid = s.get("stream_id", s.get("id"))
                if sid:
                    stream_ids.append(sid)
                    stream_names[sid] = s.get("stream_name", s.get("name", "Unknown"))

            # Build map of channels that will be affected
            affected_channels = {}  # ch_id -> {name, struck_ids}
            if plan_channel_ids is not None:
                affected_channels = {
                    value: {"name": f"channel {value}", "struck_ids": set(stream_ids)}
                    for value in plan_channel_ids
                }
            for s in streams:
                for ch in s.get("channels", []):
                    ch_id = ch.get("id")
                    if ch_id and ch_id not in affected_channels:
                        affected_channels[ch_id] = {
                            "name": ch.get("name", "Unknown"),
                            "struck_ids": set(),
                        }
                    if ch_id:
                        sid = s.get("stream_id", s.get("id"))
                        affected_channels[ch_id]["struck_ids"].add(sid)

            # Remove struck-out streams from channels
            remove_result = await client.call_endpoint(
                ENDPOINTS["stream_stats_struck_out_remove"], body={"stream_ids": stream_ids},
            )
            removed_count = remove_result.get("removed_from_channels", 0) if isinstance(remove_result, dict) else 0

            lines = [
                f"Cleaned up {len(stream_ids)} struck-out streams (threshold: {threshold}):",
                f"  Removed from channels: {removed_count} stream-channel links",
            ]

            # Delete empty channels if requested
            deleted_channels = []
            failed_channel_deletions: list[str] = []
            if delete_empty_channels and affected_channels:
                for ch_id, info in affected_channels.items():
                    try:
                        ch_data = await client.call_endpoint(ENDPOINTS["channels_get"], path_args={"channel_id": ch_id})
                        remaining = len(ch_data.get("streams", []))
                        if remaining == 0:
                            await client.call_endpoint(ENDPOINTS["channels_delete"], path_args={"channel_id": ch_id})
                            deleted_channels.append(f"{info['name']} (id={ch_id})")
                    except Exception as ch_err:
                        # Channel may already be gone, or deletion may have failed.
                        # Collect the failure so it is surfaced in the result rather
                        # than silently swallowed.
                        failed_channel_deletions.append(
                            f"{info['name']} (id={ch_id}): {ch_err}"
                        )

                if deleted_channels:
                    lines.append(f"  Deleted {len(deleted_channels)} empty channels:")
                    for ch in deleted_channels:
                        lines.append(f"    - {ch}")
                else:
                    lines.append("  No channels were left empty.")

                if failed_channel_deletions:
                    lines.append(
                        f"  WARNING: {len(failed_channel_deletions)} channel deletion(s) failed:"
                    )
                    for failure in failed_channel_deletions:
                        lines.append(f"    - {failure}")

            # Summary of affected streams
            unassigned = sum(1 for s in streams if not s.get("channels"))
            lines.append(f"  Unassigned streams (not in any channel): {unassigned}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] cleanup_struck_out_streams failed: %s", e)
            return f"Error cleaning up struck-out streams: {e}"

    @mcp.tool()
    async def bulk_remove_streams(channel_id: int, stream_ids: list[int]) -> str:
        """Remove multiple streams from a channel in one operation.

        Args:
            channel_id: The channel to remove streams from
            stream_ids: List of stream IDs to remove
        """
        try:
            client = get_ecm_client()
            # Get current channel streams
            ch = await client.call_endpoint(ENDPOINTS["channels_get"], path_args={"channel_id": channel_id})
            current_streams = ch.get("streams", [])

            remove_set = set(stream_ids)
            filtered = [sid for sid in current_streams if sid not in remove_set]
            actually_removed = len(current_streams) - len(filtered)

            if actually_removed == 0:
                return f"None of the specified streams were in channel {channel_id}."

            updated = await client.call_endpoint(
                ENDPOINTS["channels_update"], path_args={"channel_id": channel_id}, body={"streams": filtered},
            )
            # Report the resulting stream count from the response, not the request.
            now_count = len(updated.get("streams", filtered)) if isinstance(updated, dict) else len(filtered)
            return (
                f"Removed {actually_removed} streams from channel {channel_id}. "
                f"Remaining: {now_count} streams."
            )
        except Exception as e:
            logger.error("[MCP] bulk_remove_streams failed: %s", e)
            return f"Error removing streams from channel {channel_id}: {e}"

    @mcp.tool()
    async def cancel_probe() -> str:
        """Cancel the currently running stream probe."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_probe_cancel"])
            status = result.get("status") if isinstance(result, dict) else None
            msg = result.get("message", "") if isinstance(result, dict) else ""
            # Backend returns status='no_probe_running' when nothing is active —
            # don't prepend "Probe cancelled." (it contradicts the message).
            # lq38l.13 #11.
            if status == "no_probe_running":
                return msg or "No probe is currently running."
            return f"Probe cancelled. {msg}".rstrip()
        except Exception as e:
            logger.error("[MCP] cancel_probe failed: %s", e)
            return f"Error cancelling probe: {e}"

    @mcp.tool()
    async def get_probe_results() -> str:
        """Get results from the most recent completed probe run.

        Populated by the scheduled stream probe task, by probe_streams (probe
        all), and by probe_bulk_streams (which now runs as a background probe
        through the same envelope). NOT populated by probe_single_stream, which
        stays synchronous — read its return value, or use get_stream_health /
        get_struck_out_streams (persisted per-stream stats) for a single probe.
        (See bd enhancedchannelmanager-znc76.5.)
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_probe_results"])

            if not result:
                return "No probe results available."

            if isinstance(result, dict):
                # The backend returns an all-zero/all-empty envelope (success_count,
                # failed_count, ... = 0; stream lists empty) when no probe has
                # completed — a truthy dict that the `not result` guard misses.
                # Treat "every *_count is 0" as "no results" rather than printing
                # an empty structure (lq38l.13 #10).
                count_keys = [k for k in result if k.endswith("_count")]
                if count_keys and all((result.get(k) or 0) == 0 for k in count_keys):
                    return "No probe results available."

                lines = ["Latest Probe Results:"]
                for key, value in result.items():
                    label = key.replace("_", " ").title()
                    lines.append(f"  {label}: {value}")
                return "\n".join(lines)

            # If it's a list of per-stream results
            lines = [f"Probe results for {len(result)} streams:"]
            healthy = sum(1 for s in result if s.get("status") == "success")
            failed = sum(1 for s in result if s.get("status") == "failed")
            lines.append(f"  Healthy: {healthy}, Failed: {failed}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_probe_results failed: %s", e)
            return f"Error getting probe results: {e}"

    @mcp.tool()
    async def get_probe_history() -> str:
        """Get the history of the last several completed probe runs (bd-rv5w1).

        Each entry has a timestamp, total streams probed, success/failed
        counts, and status — useful for spotting a probe that's been failing
        repeatedly or one that never completes.
        """
        try:
            client = get_ecm_client()
            history = await client.call_endpoint(ENDPOINTS["stream_stats_probe_history"])

            if not history:
                return "No probe run history available."

            lines = [f"Probe run history ({len(history)} run(s), most recent first):"]
            for run in history:
                ts = run.get("timestamp", "?")
                total = run.get("total", 0)
                success = run.get("success_count", 0)
                failed = run.get("failed_count", 0)
                status = run.get("status", "?")
                lines.append(f"  {ts}: {status} — {success}/{total} succeeded, {failed} failed")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_probe_history failed: %s", e)
            return f"Error getting probe history: {e}"

    @mcp.tool()
    async def reset_probe_state(confirm: bool = False) -> str:
        """Force-reset the probe state if it appears stuck (bd-rv5w1).

        DIAGNOSTIC RECOVERY ACTION: clears the server-side prober's
        in-progress/paused flags so a NEW probe can start, WITHOUT waiting
        for or cancelling whatever probe loop may still be running in the
        background. Only use this when a probe genuinely appears stuck (e.g.
        get_probe_progress has shown in_progress=true with no forward
        movement for an extended period) — resetting a probe that IS still
        actively running can let a second probe start concurrently and
        interleave results with the first.

        CONFIRM GATING: the first call (confirm=False, the default) fetches
        current probe progress and returns a preview — it resets NOTHING.
        Re-invoke with confirm=True to actually force the reset.

        Args:
            confirm: Set True on the second call to perform the reset.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                progress = await client.call_endpoint(ENDPOINTS["stream_stats_probe_progress"])
                in_progress = progress.get("in_progress") if isinstance(progress, dict) else None
                status = progress.get("status", "?") if isinstance(progress, dict) else "?"
                status_line = "IN PROGRESS" if in_progress else "not in progress"
                return (
                    f"Probe is currently {status_line} (status={status}). "
                    "Force-resetting will clear the probe state, which can cause a "
                    "still-running probe thread to interleave with a subsequent probe. "
                    "Re-invoke with confirm=True to proceed."
                )
            result = await client.call_endpoint(ENDPOINTS["stream_stats_probe_reset"])
            msg = result.get("message", "") if isinstance(result, dict) else ""
            return f"Probe state reset. {msg}".rstrip()
        except Exception as e:
            logger.error("[MCP] reset_probe_state failed: %s", e)
            return f"Error resetting probe state: {e}"

    @mcp.tool()
    async def dismiss_probe_failures(stream_ids: list[int]) -> str:
        """Dismiss probe failures for the given streams (bd-rv5w1).

        Marks the streams as 'dismissed' so they stop appearing in failed-
        stream lists (e.g. get_struck_out_streams). The dismissal is cleared
        automatically the next time a stream is re-probed.

        Args:
            stream_ids: Stream IDs whose probe failures to dismiss.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stream_stats_dismiss"], body={"stream_ids": stream_ids}
            )
            dismissed = result.get("dismissed", 0) if isinstance(result, dict) else 0
            return f"Dismissed probe failures for {dismissed} stream(s)."
        except Exception as e:
            logger.error("[MCP] dismiss_probe_failures failed: %s", e)
            return f"Error dismissing probe failures: {e}"

    @mcp.tool()
    async def list_dismissed_probe_failures() -> str:
        """List stream IDs whose probe failures are currently dismissed (bd-rv5w1)."""
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stream_stats_dismissed"])
            ids = result.get("dismissed_stream_ids", []) if isinstance(result, dict) else []

            if not ids:
                return "No dismissed probe failures."

            return f"Dismissed probe failures ({len(ids)}): {', '.join(str(i) for i in ids)}"
        except Exception as e:
            logger.error("[MCP] list_dismissed_probe_failures failed: %s", e)
            return f"Error listing dismissed probe failures: {e}"

    @mcp.tool()
    async def get_streams_for_channel(channel_id: int) -> str:
        """Get detailed stream information for a specific channel, including stream names and metadata.

        Args:
            channel_id: The channel ID to get streams for
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["channels_streams"], path_args={"channel_id": channel_id})

            streams = result if isinstance(result, list) else result.get("streams", result.get("results", []))

            if not streams:
                return f"Channel {channel_id} has no streams assigned."

            provider_map, group_map = await _build_id_name_maps(client)

            lines = [f"Channel {channel_id} has {len(streams)} streams:"]
            for i, s in enumerate(streams, 1):
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                info = _stream_group_provider_suffix(s, provider_map, group_map)
                lines.append(f"  {i}. {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_streams_for_channel failed: %s", e)
            if _is_not_found(e):
                return (
                    f"Channel {channel_id} not found "
                    "(channel_id is the internal channel id, not the channel "
                    "number shown in the UI). Use list_channels to find the id."
                )
            return f"Error getting streams for channel {channel_id}: {e}"

    @mcp.tool()
    async def search_streams(
        query: str,
        provider_id: int | None = None,
        limit: int = 25,
        assigned: bool | None = None,
    ) -> str:
        """Search for streams by name across all providers.

        Args:
            query: Search term to match against stream names
            provider_id: Optional M3U provider ID to narrow the search
            limit: Maximum results to return (default 25)
            assigned: Filter by channel-assignment status. True -> only assigned
                streams; False -> only unassigned; None (default) -> no filter.
                When set, the backend includes assignment status (extra cached
                channel fetch), and filtering is applied client-side.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["streams_list"],
                query=_stream_query(
                    search=query, m3u_account=provider_id, page_size=min(limit, 100),
                    include_assignment=assigned is not None,
                ),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
                total = len(streams)

            streams = _filter_by_assigned(streams, assigned)
            # When an assignment filter narrows results client-side, the backend
            # total no longer reflects what's shown; report the filtered count.
            if assigned is not None:
                total = len(streams)

            if not streams:
                return f"No streams found matching '{query}'."

            lines = [f"Found {total} streams matching '{query}' (showing {min(len(streams), limit)}):"]
            for s in streams[:limit]:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group = s.get("group", "")
                provider = s.get("provider_name", "")
                info = f" [{group}]" if group else ""
                info += f" from {provider}" if provider else ""
                info += _stream_assignment_suffix(s)
                lines.append(f"  {name} (id={sid}){info}")

            if total > limit:
                lines.append(f"  ... and {total - limit} more results")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] search_streams failed: %s", e)
            return f"Error searching streams: {e}"

    @mcp.tool()
    async def get_streams_by_ids(stream_ids: list[int], include_assignment: bool = False) -> str:
        """Fetch detailed stream information for specific stream IDs.

        Dispatcharr's stream payload carries no channel linkage, so assignment
        status is derived backend-side from a reverse stream->channel index that
        costs an extra (cached, ~45s TTL) channel fetch. It is therefore opt-in:
        only set include_assignment=True when you need to know whether streams
        are attached to a channel.

        Args:
            stream_ids: List of stream IDs to look up
            include_assignment: When True, annotate each stream with its channel
                assignment and render " -> channel N" (or " -> channels N, M" for
                several) when assigned, or " [unassigned]" when not. Default
                False leaves output unchanged and skips the channel fetch.
        """
        try:
            client = get_ecm_client()
            body = {"stream_ids": stream_ids}
            if include_assignment:
                body["include_assignment"] = True
            result = await client.call_endpoint(ENDPOINTS["streams_by_ids"], body=body)

            streams = result if isinstance(result, list) else result.get("streams", result.get("results", []))

            if not streams:
                return f"No streams found for the given {len(stream_ids)} IDs."

            provider_map, group_map = await _build_id_name_maps(client)

            lines = [f"Found {len(streams)} of {len(stream_ids)} requested streams:"]
            for s in streams:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                info = _stream_group_provider_suffix(s, provider_map, group_map)
                info += _stream_assignment_suffix(s)
                lines.append(f"  {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_streams_by_ids failed: %s", e)
            return f"Error fetching streams by IDs: {e}"

    @mcp.tool()
    async def probe_bulk_streams(stream_ids: list[int]) -> str:
        """Probe multiple streams at once and return a health results summary.

        Starts an asynchronous background probe of the given streams (the same
        job/progress/results machinery probe_streams uses), then polls progress
        until it completes and reports the real success/failed counts.

        The probe feeds get_probe_progress / get_probe_results, and the per-stream
        stats are persisted, so get_stream_health, get_struck_out_streams, and
        get_streams_by_ids also reflect the new results. Because it is async, it
        no longer times out at the gateway (504) on larger batches. If a probe is
        already running, this returns without starting a second one. If the probe
        is still running when the poll budget is exhausted, this returns a
        "still running" message — use get_probe_progress / get_probe_results to
        follow it. (Fixes enhancedchannelmanager-znc76.5.)

        Args:
            stream_ids: List of stream IDs to probe
        """
        # Bounded polling: cap how long we wait for the background probe before
        # handing back to the caller. ~5 minutes total at 3s intervals — enough
        # for sizable batches without hanging the tool indefinitely.
        poll_interval_s = 3.0
        max_polls = 100

        try:
            client = get_ecm_client()
            start = await client.call_endpoint(
                ENDPOINTS["stream_stats_probe_bulk"], body={"stream_ids": stream_ids}, timeout=60.0,
            )

            status = start.get("status") if isinstance(start, dict) else None
            if status == "already_running":
                return (
                    "A stream probe is already in progress, so this bulk probe was "
                    "not started. Check get_probe_progress, or cancel_probe first."
                )

            # Poll progress until the run leaves the in_progress state.
            final = None
            for _ in range(max_polls):
                await asyncio.sleep(poll_interval_s)
                progress = await client.call_endpoint(ENDPOINTS["stream_stats_probe_progress"])
                if not progress.get("in_progress"):
                    final = progress
                    break

            if final is None:
                # Poll budget exhausted while still running — don't hang or error.
                progress = await client.call_endpoint(ENDPOINTS["stream_stats_probe_progress"])
                current = progress.get("current", 0)
                total = progress.get("total", len(stream_ids))
                return (
                    f"Bulk probe still running ({current}/{total}) after the poll "
                    "window — check get_probe_progress / get_probe_results for the "
                    "final outcome."
                )

            # Completed (or cancelled/failed): report real counts from the
            # results envelope, falling back to the final progress snapshot.
            results = await client.call_endpoint(ENDPOINTS["stream_stats_probe_results"])
            success = results.get("success_count", final.get("success_count", 0))
            failed = results.get("failed_count", final.get("failed_count", 0))
            total = final.get("total", len(stream_ids))

            run_status = final.get("status", "completed")
            if run_status == "cancelled":
                header = f"Bulk probe cancelled after {success + failed} of {total} streams:"
            elif run_status == "failed":
                header = f"Bulk probe failed after {success + failed} of {total} streams:"
            else:
                header = f"Bulk probe completed for {total} streams:"

            lines = [
                header,
                f"  Success: {success}",
                f"  Failed: {failed}",
            ]
            failed_streams = results.get("failed_streams", [])
            if failed_streams:
                lines.append("  Failed streams:")
                for r in failed_streams[:20]:
                    name = r.get("name") or f"id={r.get('id', '?')}"
                    error = r.get("error") or "unknown error"
                    lines.append(f"    - {name}: {error}")
                if len(failed_streams) > 20:
                    lines.append(f"    ... and {len(failed_streams) - 20} more failures")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] probe_bulk_streams failed: %s", e)
            return f"Error probing streams in bulk: {e}"

    @mcp.tool()
    async def bulk_search_streams(
        queries: list[str],
        provider_id: int | None = None,
        limit_per_query: int = 10,
    ) -> str:
        """Search for multiple stream names in one call.

        Args:
            queries: List of stream name search terms
            provider_id: Optional M3U provider ID to narrow the search
            limit_per_query: Maximum results per query (default 10)
        """
        try:
            client = get_ecm_client()
            lines = []
            for query in queries:
                result = await client.call_endpoint(
                    ENDPOINTS["streams_list"],
                    query=_stream_query(search=query, m3u_account=provider_id, page_size=min(limit_per_query, 100)),
                )
                streams = result.get("results", result.get("streams", [])) if isinstance(result, dict) else result

                if not streams:
                    lines.append(f'No results for "{query}"')
                else:
                    lines.append(f'Results for "{query}" ({len(streams)} found):')
                    for s in streams:
                        name = s.get("name", "Unknown")
                        sid = s.get("id", "?")
                        lines.append(f"  {name} (id={sid})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_search_streams failed: %s", e)
            return f"Error searching streams in bulk: {e}"

    @mcp.tool()
    async def preview_fuzzy_matches(
        group_ids: list[int],
        min_score: float = 0.6,
    ) -> str:
        """Preview scored stream→channel fuzzy matches WITHOUT writing anything.

        Calls the backend's unified scoring core (the same M1 callsign
        hard-reject + tvg_id override + Locals fuzzy used by auto-creation
        rules) over the given channel groups and returns ranked
        (stream, channel, score) triples. Zero writes — inspection only.

        Args:
            group_ids: Channel group IDs to scope the preview to (non-empty).
            min_score: Minimum score to include (0.0-1.0). May be below the
                0.60 confidence floor — the preview exposes sub-floor scores
                for inspection.
        """
        try:
            client = get_ecm_client()
            triples = await _preview_triples(client, group_ids, min_score)
            if not triples:
                return (f"No scored matches >= {min_score} in groups {group_ids}.")
            lines = [
                f"Scored matches in groups {group_ids} (min_score={min_score}, "
                f"{len(triples)} total — PREVIEW, no writes):"
            ]
            for t in triples[:50]:
                lines.append(
                    f"  stream {t['stream_id']} '{t['stream_name']}' -> "
                    f"channel {t['channel_id']} '{t['channel_name']}' "
                    f"(score={t['score']}, {t['callsign_verdict']}, {t['signal']})"
                )
            if len(triples) > 50:
                lines.append(f"  ... and {len(triples) - 50} more")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] preview_fuzzy_matches failed: %s", e)
            return f"Error previewing fuzzy matches: {e}"

    @mcp.tool()
    async def fuzzy_match_stream(
        group_ids: list[int],
        min_score: float = 0.6,
    ) -> str:
        """Score streams against channels in the given groups (with confidence scores).

        Repointed (jnzst) to the backend scoring core — no client-side scoring.
        Read-only: returns the best-scoring channel per stream above min_score.
        Use ``match_streams_to_channels(apply=true)`` to actually assign.

        Args:
            group_ids: Channel group IDs to score within (non-empty).
            min_score: Minimum confidence score (0.0-1.0) to surface a match.
        """
        try:
            client = get_ecm_client()
            triples = await _preview_triples(client, group_ids, min_score)
            if not triples:
                return f"No matches >= {min_score} in groups {group_ids}."
            # Best channel per stream.
            best_by_stream: dict[int, dict] = {}
            for t in triples:
                cur = best_by_stream.get(t["stream_id"])
                if cur is None or t["score"] > cur["score"]:
                    best_by_stream[t["stream_id"]] = t
            lines = [f"Best fuzzy match per stream in groups {group_ids} "
                     f"(min_score={min_score}):"]
            for t in list(best_by_stream.values())[:50]:
                lines.append(
                    f"  stream {t['stream_id']} '{t['stream_name']}' -> "
                    f"channel {t['channel_id']} '{t['channel_name']}' "
                    f"(score={t['score']})"
                )
            if len(best_by_stream) > 50:
                lines.append(f"  ... and {len(best_by_stream) - 50} more")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] fuzzy_match_stream failed: %s", e)
            return f"Error fuzzy matching stream: {e}"

    @mcp.tool()
    async def match_streams_to_channels(
        group_ids: list[int],
        min_score: float = 0.6,
        apply: bool = False,
        backfill: bool = False,
    ) -> str:
        """Match streams to channels in the given groups using the scored core.

        Repointed (jnzst) to the backend unified scoring core. DEFAULTS TO
        DRY-RUN (Q3): it only assigns streams when ``apply=true``. The M1
        callsign hard-reject, tvg_id override and Q1 no-callsign policy are
        enforced server-side. group_ids is a LIST.

        Args:
            group_ids: Channel group IDs to process (non-empty).
            min_score: Minimum confidence score (0.0-1.0) to admit a match.
            apply: When True, actually assign the best stream per channel.
                Default False = dry-run (ranked candidates + scores, no writes).
            backfill: When True, also match channels that already have streams.
                Default False = only channels with 0 streams (current behavior).
        """
        try:
            client = get_ecm_client()

            # Determine which channels are eligible (0 streams unless backfill).
            eligible: dict[str, dict] = {}
            for gid in group_ids:
                page = 1
                while True:
                    result = await client.call_endpoint(
                        ENDPOINTS["channels_list"],
                        query={"channel_group": gid, "page": page, "page_size": 500},
                    )
                    chans = result.get("results", []) if isinstance(result, dict) else (result or [])
                    for ch in chans:
                        if backfill or len(ch.get("streams", [])) == 0:
                            eligible[str(ch.get("id"))] = ch
                    if not isinstance(result, dict) or not result.get("next"):
                        break
                    page += 1

            triples = await _preview_triples(client, group_ids, min_score)
            # Best stream per eligible channel.
            best_by_channel: dict[str, dict] = {}
            for t in triples:
                cid = t["channel_id"]
                if cid not in eligible:
                    continue
                cur = best_by_channel.get(cid)
                if cur is None or t["score"] > cur["score"]:
                    best_by_channel[cid] = t

            if not best_by_channel:
                return (f"No streams matched any eligible channel in groups "
                        f"{group_ids} at min_score={min_score}.")

            if not apply:
                lines = [
                    f"DRY-RUN: {len(best_by_channel)} channel(s) would be matched "
                    f"in groups {group_ids} (min_score={min_score}). "
                    "Re-run with apply=true to assign."
                ]
                for t in list(best_by_channel.values())[:50]:
                    lines.append(
                        f"  channel {t['channel_id']} '{t['channel_name']}' <- "
                        f"stream {t['stream_id']} '{t['stream_name']}' "
                        f"(score={t['score']})"
                    )
                if len(best_by_channel) > 50:
                    lines.append(f"  ... and {len(best_by_channel) - 50} more")
                return "\n".join(lines)

            # apply=true → assign the best stream per channel.
            assigned, errors = 0, []
            for cid, t in best_by_channel.items():
                try:
                    await client.call_endpoint(
                        ENDPOINTS["channels_add_stream"],
                        path_args={"channel_id": cid},
                        body={"stream_id": t["stream_id"]},
                    )
                    assigned += 1
                except Exception as assign_err:
                    errors.append(f"channel {cid}: {assign_err}")

            lines = [f"Assigned {assigned} of {len(best_by_channel)} channel(s) "
                     f"in groups {group_ids} (min_score={min_score})."]
            if errors:
                lines.append(f"Errors ({len(errors)}):")
                lines.extend(f"  {e}" for e in errors[:20])
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] match_streams_to_channels failed: %s", e)
            return f"Error matching streams to channels: {e}"
