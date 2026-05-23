"""Channel statistics and analytics tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_channel_stats() -> str:
        """Get channel viewing statistics including active viewers, stream status, and media-server attribution.

        When present, the response includes Emby/Plex/Jellyfin viewer names, client IPs,
        and provider names added in v0.17.1.
        """
        try:
            client = get_ecm_client()
            stats = await client.call_endpoint(ENDPOINTS["stats_channels"])

            if not stats:
                return "No channel statistics available."

            channels = stats if isinstance(stats, list) else stats.get("channels", [])

            if not channels:
                return "No active channels."

            active = [c for c in channels if c.get("active_connections", 0) > 0]

            lines = [f"Channel Stats ({len(active)} active of {len(channels)} total):"]

            if active:
                lines.append("\nActive channels:")
                for c in active:
                    name = c.get("channel_name", c.get("name", "Unknown"))
                    viewers = c.get("active_connections", 0)
                    line = f"  {name} — {viewers} viewer(s)"

                    # co5wh.1: surface 0.17.1 attribution fields when present
                    attribution_parts = []
                    emby = c.get("emby_user_name")
                    plex = c.get("plex_user_name")
                    jellyfin = c.get("jellyfin_user_name")
                    if emby:
                        attribution_parts.append(f"Emby: {emby}")
                    if plex:
                        attribution_parts.append(f"Plex: {plex}")
                    if jellyfin:
                        attribution_parts.append(f"Jellyfin: {jellyfin}")
                    provider = c.get("provider_name")
                    if provider:
                        attribution_parts.append(f"provider: {provider}")
                    if attribution_parts:
                        line += f" [{', '.join(attribution_parts)}]"

                    lines.append(line)

                    # Per-client details (attribution + IPs)
                    clients = c.get("clients") or []
                    for cl in clients:
                        cl_ip = cl.get("ip_address", "?")
                        cl_emby = cl.get("emby_user_name")
                        cl_plex = cl.get("plex_user_name")
                        cl_jellyfin = cl.get("jellyfin_user_name")
                        cl_client_ip = cl.get("client_ip")
                        cl_client_ips = cl.get("client_ips") or []
                        cl_attrs = []
                        if cl_emby:
                            cl_attrs.append(f"Emby: {cl_emby}")
                        if cl_plex:
                            cl_attrs.append(f"Plex: {cl_plex}")
                        if cl_jellyfin:
                            cl_attrs.append(f"Jellyfin: {cl_jellyfin}")
                        if cl_client_ip:
                            cl_attrs.append(f"client_ip: {cl_client_ip}")
                        elif cl_client_ips:
                            cl_attrs.append(f"client_ips: {', '.join(cl_client_ips)}")
                        cl_line = f"    - {cl_ip}"
                        if cl_attrs:
                            cl_line += f" ({', '.join(cl_attrs)})"
                        lines.append(cl_line)

                    # Multi-viewer lists at channel level
                    for source, key in [("Emby", "emby_viewers"), ("Plex", "plex_viewers"), ("Jellyfin", "jellyfin_viewers")]:
                        viewers_list = c.get(key) or []
                        if viewers_list:
                            vnames = [v.get("user_name", "?") for v in viewers_list]
                            lines.append(f"    {source} viewers: {', '.join(vnames)}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel_stats failed: %s", e)
            return f"Error getting channel stats: {e}"

    @mcp.tool()
    async def get_top_watched(limit: int = 10) -> str:
        """Get the most-watched channels ranked by total viewing time.

        Args:
            limit: Number of top channels to return (default 10)
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stats_top_watched"], query={"limit": limit})

            items = result if isinstance(result, list) else result.get("channels", [])

            if not items:
                return "No watch data available."

            lines = [f"Top {len(items)} most-watched channels:"]
            for i, c in enumerate(items[:limit], 1):
                name = c.get("channel_name", c.get("name", "Unknown"))
                watch_time = c.get("total_watch_seconds", c.get("total_watch_time", 0))
                hours = watch_time / 3600 if watch_time else 0
                viewers = c.get("unique_viewers", c.get("viewer_count", "?"))
                lines.append(f"  {i}. {name} — {hours:.1f}h watched, {viewers} unique viewers")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_top_watched failed: %s", e)
            return f"Error getting top watched: {e}"

    @mcp.tool()
    async def get_bandwidth() -> str:
        """Get current bandwidth usage statistics across all channels."""
        try:
            client = get_ecm_client()
            b = await client.call_endpoint(ENDPOINTS["stats_bandwidth"])

            def fmt(bytes_val):
                if not bytes_val:
                    return "0 B"
                for unit in ["B", "KB", "MB", "GB", "TB"]:
                    if abs(bytes_val) < 1024:
                        return f"{bytes_val:.1f} {unit}"
                    bytes_val /= 1024
                return f"{bytes_val:.1f} PB"

            lines = [
                "Bandwidth Usage:",
                f"  Today: {fmt(b.get('today', 0))}",
                f"  This Week: {fmt(b.get('this_week', 0))}",
                f"  This Month: {fmt(b.get('this_month', 0))}",
                f"  All Time: {fmt(b.get('all_time', 0))}",
            ]

            peak_in = b.get("today_peak_bitrate_in", 0)
            peak_out = b.get("today_peak_bitrate_out", 0)
            if peak_in or peak_out:
                lines.append(f"\n  Today's Peak: {fmt(peak_in)}/s in, {fmt(peak_out)}/s out")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_bandwidth failed: %s", e)
            return f"Error getting bandwidth: {e}"

    @mcp.tool()
    async def get_popularity_rankings(limit: int = 10) -> str:
        """Get channel popularity rankings with scores and trending data.

        Args:
            limit: Number of channels to return (default 10)
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["stats_popularity_rankings"], query={"limit": limit})

            rankings = result.get("rankings", []) if isinstance(result, dict) else result
            total = result.get("total", len(rankings)) if isinstance(result, dict) else len(rankings)

            if not rankings:
                return "No popularity data available. Channels need viewing activity first."

            lines = [f"Channel Popularity Rankings ({total} total, showing top {min(len(rankings), limit)}):"]
            for r in rankings[:limit]:
                name = r.get("channel_name", r.get("name", "Unknown"))
                score = r.get("score", r.get("popularity_score", 0))
                trend = r.get("trend", "")
                trend_icon = " ↑" if trend == "up" else " ↓" if trend == "down" else ""
                lines.append(f"  {name} — score: {score:.1f}{trend_icon}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_popularity_rankings failed: %s", e)
            return f"Error getting popularity rankings: {e}"

    @mcp.tool()
    async def get_watch_history(
        limit: int = 20,
        channel_id: str | None = None,
        ip_address: str | None = None,
        days: int | None = None,
    ) -> str:
        """Get recent channel watch history with optional filters.

        Args:
            limit: Number of history entries to return (default 20)
            channel_id: Filter by specific channel UUID
            ip_address: Filter by specific IP address
            days: Filter to last N days (e.g. 1, 7, 30)
        """
        try:
            client = get_ecm_client()
            query = {"page_size": limit}
            if channel_id:
                query["channel_id"] = channel_id
            if ip_address:
                query["ip_address"] = ip_address
            if days:
                query["days"] = days
            result = await client.call_endpoint(ENDPOINTS["stats_watch_history"], query=query)

            entries = result.get("history", []) if isinstance(result, dict) else result
            total = result.get("total", len(entries)) if isinstance(result, dict) else len(entries)
            summary = result.get("summary", {}) if isinstance(result, dict) else {}

            if not entries:
                return "No watch history available."

            lines = [f"Watch History ({total} total, showing {len(entries)}):"]
            if summary:
                lines.append(f"  Summary: {summary.get('unique_channels', 0)} channels, "
                             f"{summary.get('unique_ips', 0)} viewers, "
                             f"{(summary.get('total_watch_seconds', 0) / 3600):.1f}h total")
            lines.append("")
            for e in entries:
                name = e.get("channel_name", "Unknown")
                connected = e.get("connected_at", "?")
                duration = e.get("watch_seconds", 0)
                mins = duration / 60 if duration else 0
                ip = e.get("ip_address", "")
                username = e.get("username")
                user_info = f" ({username})" if username else ""
                status = "watching" if not e.get("disconnected_at") else "done"
                lines.append(f"  {name} — {mins:.0f}min from {ip}{user_info} [{status}] ({connected})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_watch_history failed: %s", e)
            return f"Error getting watch history: {e}"

    @mcp.tool()
    async def get_unique_viewers() -> str:
        """Get unique viewer counts and connection statistics."""
        try:
            client = get_ecm_client()
            d = await client.call_endpoint(ENDPOINTS["stats_unique_viewers"])

            lines = [
                "Unique Viewers:",
                f"  Total unique viewers: {d.get('total_unique_viewers', 0)}",
                f"  Today's unique viewers: {d.get('today_unique_viewers', 0)}",
                f"  Total connections: {d.get('total_connections', 0)}",
            ]

            avg = d.get("avg_watch_seconds", 0)
            if avg:
                lines.append(f"  Average watch time: {avg / 60:.1f} minutes")

            # Per-channel breakdown if available
            try:
                by_channel = await client.call_endpoint(ENDPOINTS["stats_unique_viewers_by_channel"])
                if by_channel and isinstance(by_channel, list):
                    lines.append("\nTop channels by unique viewers:")
                    for c in by_channel[:10]:
                        name = c.get("channel_name", c.get("name", "Unknown"))
                        count = c.get("unique_viewers", c.get("viewer_count", 0))
                        lines.append(f"  {name}: {count} viewers")
            except Exception as channel_breakdown_err:
                # Per-channel breakdown is supplementary detail; if Dispatcharr lacks the
                # /unique-viewers-by-channel endpoint we still return the totals above.
                logger.debug(
                    "[MCP] get_unique_viewers: per-channel breakdown unavailable: %s",
                    channel_breakdown_err,
                )

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_unique_viewers failed: %s", e)
            return f"Error getting unique viewers: {e}"

    @mcp.tool()
    async def compute_stream_sort(
        channels: list[dict],
        mode: str = "smart",
    ) -> str:
        """Compute optimal stream sort order for channels using smart sorting criteria.

        Uses server-side sort settings (priority, enabled criteria, M3U priorities) to
        determine the best stream order. Supports video_codec as a sort criterion.

        Args:
            channels: List of channel dicts, each with 'channel_id' (int) and 'stream_ids' (list of int).
                Example: [{"channel_id": 1, "stream_ids": [10, 20, 30]}]
            mode: Sort mode — 'smart' (uses all enabled criteria), 'resolution', 'bitrate',
                  'framerate', 'video_codec', 'm3u_priority', or 'audio_channels'
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stream_stats_compute_sort"],
                body={"channels": channels, "mode": mode},
                timeout=60.0,
            )

            results = result.get("results", []) if isinstance(result, dict) else result

            if not results:
                return "No sort results."

            changed_count = sum(1 for r in results if r.get("changed"))
            lines = [f"Stream Sort Results ({len(results)} channels, {changed_count} changed):"]
            for r in results:
                cid = r.get("channel_id", "?")
                changed = "changed" if r.get("changed") else "unchanged"
                ids = r.get("sorted_stream_ids", [])
                lines.append(f"  Channel {cid}: [{', '.join(str(i) for i in ids)}] ({changed})")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] compute_stream_sort failed: %s", e)
            return f"Error computing stream sort: {e}"

    # -------------------------------------------------------------------------
    # co5wh.2 — per-provider stats (Providers panel)
    # -------------------------------------------------------------------------

    @mcp.tool()
    async def get_provider_stats(
        metric: str = "buffering",
        window: str = "7d",
        bucket: str = "hour",
        top_n: int = 50,
    ) -> str:
        """Get per-provider statistics from the Stats v2 Providers panel.

        Wraps four backend endpoints selectable via the ``metric`` parameter.

        Args:
            metric: One of ``buffering`` (channel events time-series),
                    ``watch_time`` (total watch-time per provider),
                    ``channel_heatmap`` (provider × channel byte grid),
                    or ``bitrate`` (derived bitrate time-series).
            window: Time window — ``7d``, ``30d``, or ``90d``. Default ``7d``.
            bucket: Time bucket for time-series metrics — ``hour`` or ``day``.
                    Applies to ``buffering`` and ``bitrate``. Default ``hour``.
            top_n: For ``channel_heatmap`` — cap at top-N channels by bytes.
                   Default 50.
        """
        _VALID_METRICS = {"buffering", "watch_time", "channel_heatmap", "bitrate"}
        if metric not in _VALID_METRICS:
            return f"Invalid metric '{metric}'. Choose from: {', '.join(sorted(_VALID_METRICS))}"

        try:
            client = get_ecm_client()

            if metric == "buffering":
                result = await client.call_endpoint(
                    ENDPOINTS["stats_providers_buffering"],
                    query={"window": window, "bucket": bucket},
                )
                data = result.get("data", []) if isinstance(result, dict) else result
                meta = result.get("meta", {}) if isinstance(result, dict) else {}
                if not data:
                    return f"No buffering event data for window={window}."
                lines = [
                    f"Provider Buffering Events (window={window}, bucket={bucket},"
                    f" {meta.get('total_rows', len(data))} rows):"
                ]
                for row in data[:20]:
                    pid = row.get("provider_id") or "Unknown"
                    tb = row.get("time_bucket", "?")
                    total = row.get("total_event_count", 0)
                    buf = row.get("buffer_event_count", 0)
                    recon = row.get("reconnect_event_count", 0)
                    err = row.get("error_event_count", 0)
                    sw = row.get("switch_event_count", 0)
                    lines.append(
                        f"  provider={pid} [{tb}] total={total}"
                        f" (buf={buf} reconnect={recon} err={err} switch={sw})"
                    )
                if len(data) > 20:
                    lines.append(f"  ... and {len(data) - 20} more rows")
                return "\n".join(lines)

            elif metric == "watch_time":
                result = await client.call_endpoint(
                    ENDPOINTS["stats_providers_watch_time"],
                    query={"window": window},
                )
                data = result.get("data", []) if isinstance(result, dict) else result
                meta = result.get("meta", {}) if isinstance(result, dict) else {}
                if not data:
                    return f"No watch-time data for window={window}."
                lines = [
                    f"Provider Watch-Time (window={window},"
                    f" {meta.get('total_rows', len(data))} providers):"
                ]
                for row in data:
                    pid = row.get("provider_id") or "Unknown"
                    secs = row.get("total_watch_seconds", 0)
                    hrs = secs / 3600 if secs else 0
                    lines.append(f"  provider={pid}: {hrs:.1f}h")
                return "\n".join(lines)

            elif metric == "channel_heatmap":
                result = await client.call_endpoint(
                    ENDPOINTS["stats_providers_channel_heatmap"],
                    query={"window": window, "top_n": top_n},
                )
                data = result.get("data", []) if isinstance(result, dict) else result
                meta = result.get("meta", {}) if isinstance(result, dict) else {}
                if not data:
                    return f"No heatmap data for window={window}."

                def _fmt_bytes(b):
                    for unit in ["B", "KB", "MB", "GB", "TB"]:
                        if abs(b) < 1024:
                            return f"{b:.1f} {unit}"
                        b /= 1024
                    return f"{b:.1f} PB"

                lines = [
                    f"Provider × Channel Byte Heatmap (window={window},"
                    f" top_n={meta.get('top_n', top_n)}, {meta.get('total_rows', len(data))} cells):"
                ]
                for row in data[:30]:
                    pid = row.get("provider_id") or "Unknown"
                    cname = row.get("channel_name", row.get("channel_id", "?"))
                    byt = row.get("bytes", 0)
                    stream = row.get("latest_stream_name")
                    cell = f"  provider={pid} / {cname}: {_fmt_bytes(byt)}"
                    if stream:
                        cell += f" (latest stream: {stream})"
                    lines.append(cell)
                if len(data) > 30:
                    lines.append(f"  ... and {len(data) - 30} more cells")
                return "\n".join(lines)

            else:  # bitrate
                result = await client.call_endpoint(
                    ENDPOINTS["stats_providers_bitrate"],
                    query={"window": window, "bucket": bucket},
                )
                data = result.get("data", []) if isinstance(result, dict) else result
                meta = result.get("meta", {}) if isinstance(result, dict) else {}
                if not data:
                    return f"No bitrate data for window={window}."
                lines = [
                    f"Provider Bitrate (window={window}, bucket={bucket},"
                    f" {meta.get('total_rows', len(data))} rows):"
                ]
                for row in data[:20]:
                    pid = row.get("provider_id") or "Unknown"
                    tb = row.get("time_bucket", "?")
                    bps = row.get("bitrate_bps", 0)
                    mbps = bps / 1_000_000 if bps else 0
                    lines.append(f"  provider={pid} [{tb}] {mbps:.2f} Mbps")
                if len(data) > 20:
                    lines.append(f"  ... and {len(data) - 20} more rows")
                return "\n".join(lines)

        except Exception as e:
            logger.error("[MCP] get_provider_stats failed: %s", e)
            return f"Error getting provider stats: {e}"

    # -------------------------------------------------------------------------
    # co5wh.3 — per-user watch-time tools
    # -------------------------------------------------------------------------

    @mcp.tool()
    async def get_user_watch_time(
        group_by: str = "total",
        user_id: int | None = None,
    ) -> str:
        """Get per-user watch-time totals from the Stats v2 session data (GH-62).

        Args:
            group_by: ``total`` (one row per user, default) or ``day``
                      (one row per user-day pair).
            user_id: Optional — filter to a single Dispatcharr user ID.
        """
        if group_by not in {"total", "day"}:
            return "Invalid group_by. Choose 'total' or 'day'."

        try:
            client = get_ecm_client()
            query_params: dict = {"group_by": group_by}
            if user_id is not None:
                query_params["user_id"] = user_id

            result = await client.call_endpoint(
                ENDPOINTS["stats_watch_time"],
                query=query_params,
            )

            # Response envelope: {data: [...], meta: {...}, pagination: null}
            data = result.get("data", []) if isinstance(result, dict) else result
            meta = result.get("meta", {}) if isinstance(result, dict) else {}
            total_rows = meta.get("total_rows", len(data))

            if not data:
                return "No watch-time data available."

            from_iso = meta.get("from_iso") or "all time"
            to_iso = meta.get("to_iso") or "now"

            if group_by == "total":
                lines = [
                    f"User Watch-Time Totals ({total_rows} users, {from_iso} → {to_iso}):"
                ]
                for row in data[:20]:
                    uid = row.get("user_id", "?")
                    username = row.get("username") or "Unknown"
                    source = row.get("attribution_source", "dispatcharr")
                    secs = row.get("total_watch_seconds", 0)
                    hrs = secs / 3600 if secs else 0
                    last = row.get("last_watched", "?")
                    lines.append(
                        f"  user_id={uid} ({username} via {source}):"
                        f" {hrs:.1f}h — last watched {last}"
                    )
                if len(data) > 20:
                    lines.append(f"  ... and {len(data) - 20} more users")
            else:  # day
                lines = [
                    f"User Watch-Time by Day ({total_rows} rows, {from_iso} → {to_iso}):"
                ]
                for row in data[:30]:
                    uid = row.get("user_id", "?")
                    username = row.get("username") or "Unknown"
                    source = row.get("attribution_source", "dispatcharr")
                    day = row.get("day", "?")
                    secs = row.get("watch_seconds", 0)
                    mins = secs / 60 if secs else 0
                    lines.append(
                        f"  {day} user_id={uid} ({username} via {source}): {mins:.0f}min"
                    )
                if len(data) > 30:
                    lines.append(f"  ... and {len(data) - 30} more rows")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_user_watch_time failed: %s", e)
            return f"Error getting user watch-time: {e}"

    @mcp.tool()
    async def get_user_channel_breakdown(
        user_id: str,
        source: str = "dispatcharr",
    ) -> str:
        """Get per-channel watch-time breakdown for a specific user (GH-62).

        Args:
            user_id: The user identifier. For ``source=dispatcharr`` this is the
                     integer ECM user_id. For ``source=emby`` this is the Emby
                     user GUID string.
            source: ``dispatcharr`` (default) or ``emby``.
        """
        if source not in {"dispatcharr", "emby"}:
            return "Invalid source. Choose 'dispatcharr' or 'emby'."

        try:
            client = get_ecm_client()

            if source == "dispatcharr":
                result = await client.call_endpoint(
                    ENDPOINTS["stats_users_dispatcharr"],
                    path_params={"user_id": user_id},
                )
            else:  # emby
                result = await client.call_endpoint(
                    ENDPOINTS["stats_users_emby"],
                    path_params={"emby_user_id": user_id},
                )

            # Response envelope: {data: [...], meta: {...}, pagination: null}
            data = result.get("data", []) if isinstance(result, dict) else result
            meta = result.get("meta", {}) if isinstance(result, dict) else {}
            total_rows = meta.get("total_rows", len(data))

            if not data:
                return f"No channel breakdown data for {source} user_id={user_id}."

            from_iso = meta.get("from_iso") or "all time"
            to_iso = meta.get("to_iso") or "now"

            lines = [
                f"Channel Breakdown for {source} user_id={user_id}"
                f" ({total_rows} channels, {from_iso} → {to_iso}):"
            ]
            for row in data:
                cname = row.get("channel_name", row.get("channel_id", "?"))
                secs = row.get("total_watch_seconds", 0)
                hrs = secs / 3600 if secs else 0
                sessions = row.get("session_count", "?")
                last = row.get("last_watched", "?")
                stream = row.get("latest_stream_name")
                line = f"  {cname}: {hrs:.1f}h ({sessions} sessions) — last {last}"
                if stream:
                    line += f" [{stream}]"
                lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_user_channel_breakdown failed: %s", e)
            return f"Error getting user channel breakdown: {e}"

    # -------------------------------------------------------------------------
    # co5wh.4 — popularity trending + per-channel score
    # -------------------------------------------------------------------------

    @mcp.tool()
    async def get_trending(
        direction: str = "up",
        limit: int = 10,
    ) -> str:
        """Get channels that are trending up or down in popularity.

        Args:
            direction: ``up`` (gaining popularity, default) or ``down`` (losing it).
            limit: Number of channels to return (default 10).
        """
        if direction not in {"up", "down"}:
            return "Invalid direction. Choose 'up' or 'down'."

        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stats_popularity_trending"],
                query={"direction": direction, "limit": limit},
            )

            # Backend returns a list of ChannelPopularityScore.to_dict()
            channels = result if isinstance(result, list) else result.get("channels", result.get("data", []))

            if not channels:
                return f"No channels trending {direction}. Run a popularity calculation first."

            arrow = "↑" if direction == "up" else "↓"
            lines = [f"Trending {direction.upper()} Channels ({len(channels)} total):"]
            for ch in channels[:limit]:
                name = ch.get("channel_name", ch.get("name", "Unknown"))
                score = ch.get("score", 0)
                trend_pct = ch.get("trend_percent", 0)
                rank = ch.get("rank")
                line = f"  {arrow} {name} — score={score:.1f} trend={trend_pct:+.1f}%"
                if rank:
                    line += f" (rank #{rank})"
                lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_trending failed: %s", e)
            return f"Error getting trending channels: {e}"

    @mcp.tool()
    async def get_channel_popularity(channel_id: str) -> str:
        """Get the popularity score and metrics for a specific channel.

        Args:
            channel_id: The channel UUID.
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stats_popularity_channel"],
                path_params={"channel_id": channel_id},
            )

            if not result:
                return f"No popularity data for channel {channel_id}."

            name = result.get("channel_name", channel_id)
            score = result.get("score", 0)
            rank = result.get("rank")
            trend = result.get("trend", "stable")
            trend_pct = result.get("trend_percent", 0)
            watch_count = result.get("watch_count_7d", 0)
            watch_time = result.get("watch_time_7d", 0)
            unique_viewers = result.get("unique_viewers_7d", 0)
            bandwidth = result.get("bandwidth_7d", 0)

            def _fmt_bytes(b):
                for unit in ["B", "KB", "MB", "GB", "TB"]:
                    if abs(b) < 1024:
                        return f"{b:.1f} {unit}"
                    b /= 1024
                return f"{b:.1f} PB"

            lines = [
                f"Channel Popularity: {name}",
                f"  Score: {score:.1f}  Rank: #{rank or 'N/A'}  Trend: {trend} ({trend_pct:+.1f}%)",
                f"  7-day stats: {watch_count} sessions, {watch_time // 3600:.1f}h watched,"
                f" {unique_viewers} unique viewers, {_fmt_bytes(bandwidth)} bandwidth",
            ]
            calc = result.get("calculated_at")
            if calc:
                lines.append(f"  Last calculated: {calc}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel_popularity failed: %s", e)
            return f"Error getting channel popularity: {e}"

    # -------------------------------------------------------------------------
    # co5wh.5 — activity feed + per-channel bandwidth
    # -------------------------------------------------------------------------

    @mcp.tool()
    async def get_activity(
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> str:
        """Get recent system activity events (channel start/stop, buffering, client connections).

        Args:
            limit: Number of events to return (default 50, max 1000).
            offset: Pagination offset.
            event_type: Optional filter by event type (e.g. ``channel_start``,
                        ``channel_stop``, ``client_connected``, ``channel_buffering``).
        """
        try:
            client = get_ecm_client()
            query_params: dict = {"limit": min(limit, 1000), "offset": offset}
            if event_type:
                query_params["event_type"] = event_type

            result = await client.call_endpoint(
                ENDPOINTS["stats_activity"],
                query=query_params,
            )

            # Dispatcharr returns {count, next, previous, results:[...]}
            if isinstance(result, dict):
                events = result.get("results", result.get("events", []))
                total = result.get("count", len(events))
            else:
                events = result if isinstance(result, list) else []
                total = len(events)

            if not events:
                return "No activity events found."

            filter_note = f" (filtered: {event_type})" if event_type else ""
            lines = [f"Activity Events ({total} total{filter_note}, showing {len(events)}):"]
            for ev in events[:limit]:
                ev_type = ev.get("event_type", ev.get("type", "?"))
                timestamp = ev.get("timestamp", ev.get("created_at", "?"))
                channel = ev.get("channel_name", ev.get("channel_id", ""))
                detail = ev.get("detail", ev.get("message", ""))
                line = f"  [{timestamp}] {ev_type}"
                if channel:
                    line += f" — {channel}"
                if detail:
                    line += f": {detail}"
                lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_activity failed: %s", e)
            return f"Error getting activity: {e}"

    @mcp.tool()
    async def get_channel_bandwidth(
        days: int = 7,
        limit: int = 20,
        sort_by: str = "bytes",
    ) -> str:
        """Get per-channel bandwidth statistics.

        Args:
            days: Number of days to aggregate (default 7).
            limit: Maximum number of channels to return (default 20).
            sort_by: Sort by ``bytes``, ``connections``, or ``watch_time``.
                     Default ``bytes``.
        """
        if sort_by not in {"bytes", "connections", "watch_time"}:
            return "Invalid sort_by. Choose 'bytes', 'connections', or 'watch_time'."

        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["stats_channel_bandwidth"],
                query={"days": days, "limit": limit, "sort_by": sort_by},
            )

            # Backend returns a list of channel bandwidth dicts
            channels = result if isinstance(result, list) else result.get("channels", result.get("data", []))

            if not channels:
                return f"No channel bandwidth data for the last {days} days."

            def _fmt_bytes(b):
                for unit in ["B", "KB", "MB", "GB", "TB"]:
                    if abs(b) < 1024:
                        return f"{b:.1f} {unit}"
                    b /= 1024
                return f"{b:.1f} PB"

            lines = [f"Channel Bandwidth (last {days} days, top {len(channels)}, sorted by {sort_by}):"]
            for ch in channels:
                cname = ch.get("channel_name", ch.get("channel_id", "Unknown"))
                byt = ch.get("total_bytes", 0)
                conns = ch.get("total_connections", 0)
                watch = ch.get("total_watch_seconds", 0)
                peak = ch.get("peak_clients", 0)
                hours = watch / 3600 if watch else 0
                lines.append(
                    f"  {cname}: {_fmt_bytes(byt)}, {conns} connections,"
                    f" {hours:.1f}h watched, peak {peak} clients"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel_bandwidth failed: %s", e)
            return f"Error getting channel bandwidth: {e}"
