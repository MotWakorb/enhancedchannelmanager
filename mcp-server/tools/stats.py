"""Channel statistics and analytics tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_channel_stats() -> str:
        """Get channel viewing statistics including active viewers and stream status."""
        try:
            client = get_ecm_client()
            stats = await client.get("/api/stats/channels")

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
                    lines.append(f"  {name} — {viewers} viewer(s)")

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
            result = await client.get("/api/stats/top-watched", limit=limit)

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
            b = await client.get("/api/stats/bandwidth")

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
            result = await client.get("/api/stats/popularity/rankings", limit=limit)

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
            params = {"page_size": limit}
            if channel_id:
                params["channel_id"] = channel_id
            if ip_address:
                params["ip_address"] = ip_address
            if days:
                params["days"] = days
            result = await client.get("/api/stats/watch-history", **params)

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
            d = await client.get("/api/stats/unique-viewers")

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
                by_channel = await client.get("/api/stats/unique-viewers-by-channel")
                if by_channel and isinstance(by_channel, list):
                    lines.append(f"\nTop channels by unique viewers:")
                    for c in by_channel[:10]:
                        name = c.get("channel_name", c.get("name", "Unknown"))
                        count = c.get("unique_viewers", c.get("viewer_count", 0))
                        lines.append(f"  {name}: {count} viewers")
            except Exception:
                pass

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
            result = await client.post("/api/stream-stats/compute-sort", json_data={
                "channels": channels,
                "mode": mode,
            }, timeout=60.0)

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
