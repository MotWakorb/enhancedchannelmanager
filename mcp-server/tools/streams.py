"""Stream management and health tools."""
import logging
import re

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


# ---- Shared fuzzy matching helpers (used by both streams and channels tools) ----

def _strip_stream_prefix(name: str) -> str:
    """Strip common prefixes like 'US : ', 'US: ', 'US :' from stream names."""
    return re.sub(r'^(US|UK|CA|AU)\s*:\s*', '', name).strip()

def _normalize(name: str) -> str:
    """Normalize a name for comparison: lowercase, strip prefixes, hyphens, extra spaces."""
    s = _strip_stream_prefix(name).lower()
    s = s.replace("-", " ").replace("&amp;", "&")
    return " ".join(s.split())

_ABBREVIATIONS = {
    "fs1": "FOX Sports 1",
    "fs2": "FOX Sports 2",
    "espn2": "ESPN 2",
    "espnu": "ESPN U",
    "espnews": "ESPN News",
    "hln": "HLN",
    "tbs": "TBS",
    "tnt": "TNT",
    "tbn": "TBN",
    "cmt": "CMT",
    "bet": "BET",
    "mtv": "MTV",
    "mtv2": "MTV 2",
    "vh1": "VH1",
    "ifc": "IFC",
    "fx": "FX",
    "fxx": "FXX",
    "fxm": "FX Movie Channel",
    "hbo": "HBO",
    "qvc": "QVC",
    "hsn": "HSN",
    "nesn": "NESN",
    "msg": "MSG",
    "masn": "MASN",
    "btn": "Big Ten Network",
    "secn": "SEC Network",
    "accn": "ACC Network",
    "cbssn": "CBS Sports Network",
    "sundancetv": "Sundance TV",
    "babyfirst tv": "Baby First",
    "nick jr.": "Nick Jr",
    "tmc xtra": "Movie Channel Extra",
    "actionmax": "Cinemax Action",
    "sho x bet": "SHOxBET",
    "bloomberg tv": "Bloomberg",
    "nbc sports chicago": "Chicago Sports Network",
    "sportsnet pittsburgh": "AT&T SportsNet Pittsburgh",
}

# Prefix abbreviations: if a channel name starts with one of these,
# expand the prefix to generate an additional search form.
# e.g., "MC - Blues" -> "Music Choice Blues"
_PREFIX_EXPANSIONS = {
    "mc": "Music Choice",
    "hbo": "HBO",
    "fcs": "Fox College Sports",
}

def _generate_variants(name: str) -> list[str]:
    """Generate search variants from a channel name."""
    variants = []
    base = name.strip()
    upper = base.upper()

    # Extract call sign from parenthetical like "WISC (CBS Madison)"
    paren_match = re.search(r'\(([^)]+)\)', base)
    if paren_match:
        inner = paren_match.group(1)
        outer = base[:paren_match.start()].strip()
        variants.append(outer)
        parts = inner.split()
        if len(parts) >= 2:
            variants.append(f"{parts[-1]} {parts[0]}")
            variants.append(f"{parts[0]} {outer}")

    # Check abbreviation map
    lower = base.lower().strip()
    if lower in _ABBREVIATIONS:
        variants.append(_ABBREVIATIONS[lower])

    # Expand prefix abbreviations: "MC - Blues" -> "Music Choice Blues"
    for prefix, expansion in _PREFIX_EXPANSIONS.items():
        pattern = re.compile(r'^' + re.escape(prefix) + r'[\s\-:]+(.+)$', re.IGNORECASE)
        m = pattern.match(base)
        if m:
            remainder = m.group(1).strip()
            variants.append(f"{expansion} {remainder}")

    # Base name
    variants.append(base)

    # Hyphen/space normalization
    dehyphenated = base.replace("-", " ")
    if dehyphenated != base:
        variants.append(dehyphenated)

    # Split merged words: "SundanceTV" -> "Sundance TV"
    split_tv = re.sub(r'([a-z])TV$', r'\1 TV', base)
    if split_tv != base:
        variants.append(split_tv)

    # Add/remove common suffixes
    for suffix in (" TV", " Channel", " Network"):
        if upper.endswith(suffix.upper()):
            variants.append(base[:-len(suffix)])
        else:
            variants.append(f"{base}{suffix}")

    # Market + quality variants
    variants.extend([f"{base} East", f"{base} West", f"{base} HD", f"{base} FHD"])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for v in variants:
        v_stripped = v.strip()
        if v_stripped and v_stripped.lower() not in seen:
            seen.add(v_stripped.lower())
            unique.append(v_stripped)
    return unique

def _normalize_channel(name: str) -> list[str]:
    """Generate normalized forms of a channel name for scoring comparison."""
    base = _normalize(name)
    forms = {base}
    # Apply abbreviation expansion (full name match)
    lower = name.lower().strip()
    if lower in _ABBREVIATIONS:
        forms.add(_normalize(_ABBREVIATIONS[lower]))
    # Apply prefix expansions: "mc - blues" -> "music choice blues"
    for prefix, expansion in _PREFIX_EXPANSIONS.items():
        # Match prefix followed by separator (space, dash, colon) or end
        pattern = re.compile(r'^' + re.escape(prefix) + r'[\s\-:]+(.+)$', re.IGNORECASE)
        m = pattern.match(base)
        if m:
            remainder = m.group(1).strip()
            forms.add(_normalize(f"{expansion} {remainder}"))
    # Strip/split common suffixes
    for suffix in ("tv", "channel", "network"):
        if base.endswith(f" {suffix}"):
            forms.add(base[:-(len(suffix) + 1)].strip())
        # Handle merged: "sundancetv" -> "sundance"
        if base.endswith(suffix) and not base.endswith(f" {suffix}"):
            forms.add(base[:-len(suffix)].strip())
    # Extract from parenthetical: "wisc (cbs madison)" -> "wisc"
    paren = re.search(r'^([^(]+)\s*\(', base)
    if paren:
        forms.add(paren.group(1).strip())
    return list(forms)

def _score_match(channel_name: str, stream_name: str, market: str = "east") -> int:
    """Score how well a stream name matches a channel name."""
    ch_forms = _normalize_channel(channel_name)
    st_norm = _normalize(stream_name)
    score = 0

    # Check all normalized forms of the channel name
    best_form_score = 0
    for ch_norm in ch_forms:
        form_score = 0
        # Exact match
        if st_norm == ch_norm:
            form_score = max(form_score, 50)
        # Word-set match
        ch_words = set(ch_norm.split())
        st_words = set(st_norm.split())
        if ch_words and ch_words.issubset(st_words):
            form_score = max(form_score, 25)
        # Whole-word match in stream
        pattern = r'\b' + re.escape(ch_norm) + r'\b'
        if re.search(pattern, st_norm):
            form_score = max(form_score, 20)
        elif ch_norm in st_norm:
            form_score = max(form_score, 5)
        # Reverse: stream name as whole word in channel
        if st_norm != ch_norm:
            st_pattern = r'\b' + re.escape(st_norm) + r'\b'
            if re.search(st_pattern, ch_norm):
                form_score = max(form_score, 15)
        best_form_score = max(best_form_score, form_score)
    score += best_form_score

    # Use primary normalized form for remaining checks
    ch_norm = ch_forms[0] if ch_forms else _normalize(channel_name)

    # Local station bonus: if channel has parenthetical like "(NBC Madison)",
    # boost streams containing the network affiliation and/or market name
    paren_match = re.search(r'\(([^)]+)\)', channel_name)
    if paren_match:
        paren_content = paren_match.group(1).lower()
        paren_words = paren_content.split()
        # Network affiliations to look for
        networks = {"cbs", "nbc", "abc", "fox", "cw", "pbs", "my", "ion"}
        for word in paren_words:
            if word in networks and word in st_norm:
                score += 15  # Stream mentions the right network
            elif word not in networks and word in st_norm:
                score += 10  # Stream mentions the market (e.g., "madison")
        # Penalize streams with WRONG network affiliation
        stream_networks = {n for n in networks if n in st_norm}
        channel_networks = {n for n in networks if n in paren_content}
        if channel_networks and stream_networks and not channel_networks.intersection(stream_networks):
            score -= 20  # Stream has a different network than expected

    # Penalize Radio streams for TV channels
    if "radio" in st_norm and "radio" not in ch_norm:
        score -= 30

    # Penalize sub-channels when searching for main channel
    if st_norm != ch_norm and len(st_norm) > len(ch_norm) + 5:
        score -= 3

    # Market preference
    st_upper = stream_name.upper()
    preferred = market.upper()
    opposite = "WEST" if preferred == "EAST" else "EAST"
    if preferred in st_upper:
        score += 10
    if opposite in st_upper:
        score -= 5

    # Quality bonus
    if "FHD" in st_upper:
        score += 7
    elif " HD" in st_upper or st_upper.endswith("HD"):
        score += 5
    if " SD" in st_upper:
        score -= 3

    return score

# Export helpers for use by channels.py
_fuzzy_helpers = {
    "generate_variants": _generate_variants,
    "score_match": _score_match,
}


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_streams(
        group: str | None = None,
        provider_id: int | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> str:
        """List streams with optional filtering.

        Args:
            group: Filter by stream group name
            provider_id: Filter by M3U provider/account ID
            search: Search streams by name
            page: Page number (default 1)
            page_size: Results per page (default 50, max 100)
        """
        try:
            client = get_ecm_client()
            result = await client.get(
                "/api/streams",
                group=group,
                provider_id=provider_id,
                search=search,
                page=page,
                page_size=min(page_size, 100),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
                total = len(streams)

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
            summary = await client.get("/api/stream-stats/summary")

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
            result = await client.post("/api/stream-stats/probe/all", timeout=300.0)
            return f"Stream probe started. {result.get('message', 'Check progress in ECM.')}"
        except Exception as e:
            logger.error("[MCP] probe_streams failed: %s", e)
            return f"Error starting stream probe: {e}"

    @mcp.tool()
    async def get_probe_progress() -> str:
        """Check the progress of an ongoing stream probe."""
        try:
            client = get_ecm_client()
            p = await client.get("/api/stream-stats/probe/progress")

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

        Args:
            stream_id: The stream ID to probe
        """
        try:
            client = get_ecm_client()
            result = await client.post(f"/api/stream-stats/probe/{stream_id}")
            status = result.get("status", result.get("probe_status", "unknown"))
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
            result = await client.get("/api/stream-stats/struck-out")

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
    async def cleanup_struck_out_streams(delete_empty_channels: bool = False) -> str:
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
            struck_result = await client.get("/api/stream-stats/struck-out")
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
            affected_channels = {}  # ch_id -> {name, stream_ids_in_channel, struck_ids}
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
            remove_result = await client.post(
                "/api/stream-stats/struck-out/remove",
                json_data={"stream_ids": stream_ids},
            )
            removed_count = remove_result.get("removed_from_channels", 0)

            lines = [
                f"Cleaned up {len(stream_ids)} struck-out streams (threshold: {threshold}):",
                f"  Removed from channels: {removed_count} stream-channel links",
            ]

            # Delete empty channels if requested
            deleted_channels = []
            if delete_empty_channels and affected_channels:
                for ch_id, info in affected_channels.items():
                    try:
                        ch_data = await client.get(f"/api/channels/{ch_id}")
                        remaining = len(ch_data.get("streams", []))
                        if remaining == 0:
                            await client.delete(f"/api/channels/{ch_id}")
                            deleted_channels.append(f"{info['name']} (id={ch_id})")
                    except Exception:
                        pass  # Channel may already be gone

                if deleted_channels:
                    lines.append(f"  Deleted {len(deleted_channels)} empty channels:")
                    for ch in deleted_channels:
                        lines.append(f"    - {ch}")
                else:
                    lines.append("  No channels were left empty.")

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
            ch = await client.get(f"/api/channels/{channel_id}")
            current_streams = ch.get("streams", [])

            remove_set = set(stream_ids)
            filtered = [sid for sid in current_streams if sid not in remove_set]
            actually_removed = len(current_streams) - len(filtered)

            if actually_removed == 0:
                return f"None of the specified streams were in channel {channel_id}."

            await client.patch(f"/api/channels/{channel_id}", json_data={"streams": filtered})
            return (
                f"Removed {actually_removed} streams from channel {channel_id}. "
                f"Remaining: {len(filtered)} streams."
            )
        except Exception as e:
            logger.error("[MCP] bulk_remove_streams failed: %s", e)
            return f"Error removing streams from channel {channel_id}: {e}"

    @mcp.tool()
    async def cancel_probe() -> str:
        """Cancel the currently running stream probe."""
        try:
            client = get_ecm_client()
            result = await client.post("/api/stream-stats/probe/cancel")
            return f"Probe cancelled. {result.get('message', '')}"
        except Exception as e:
            logger.error("[MCP] cancel_probe failed: %s", e)
            return f"Error cancelling probe: {e}"

    @mcp.tool()
    async def get_probe_results() -> str:
        """Get results from the most recent completed probe run."""
        try:
            client = get_ecm_client()
            result = await client.get("/api/stream-stats/probe/results")

            if not result:
                return "No probe results available."

            if isinstance(result, dict):
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
    async def get_streams_for_channel(channel_id: int) -> str:
        """Get detailed stream information for a specific channel, including stream names and metadata.

        Args:
            channel_id: The channel ID to get streams for
        """
        try:
            client = get_ecm_client()
            result = await client.get(f"/api/channels/{channel_id}/streams")

            streams = result if isinstance(result, list) else result.get("streams", result.get("results", []))

            if not streams:
                return f"Channel {channel_id} has no streams assigned."

            lines = [f"Channel {channel_id} has {len(streams)} streams:"]
            for i, s in enumerate(streams, 1):
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group = s.get("group", "")
                provider = s.get("provider_name", s.get("m3u_account", ""))
                info = f" [{group}]" if group else ""
                info += f" from {provider}" if provider else ""
                lines.append(f"  {i}. {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_streams_for_channel failed: %s", e)
            return f"Error getting streams for channel {channel_id}: {e}"

    @mcp.tool()
    async def search_streams(
        query: str,
        provider_id: int | None = None,
        limit: int = 25,
    ) -> str:
        """Search for streams by name across all providers.

        Args:
            query: Search term to match against stream names
            provider_id: Optional M3U provider ID to narrow the search
            limit: Maximum results to return (default 25)
        """
        try:
            client = get_ecm_client()
            result = await client.get(
                "/api/streams",
                search=query,
                provider_id=provider_id,
                page_size=min(limit, 100),
            )

            if isinstance(result, dict):
                streams = result.get("results", result.get("streams", []))
                total = result.get("count", len(streams))
            else:
                streams = result
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
                lines.append(f"  {name} (id={sid}){info}")

            if total > limit:
                lines.append(f"  ... and {total - limit} more results")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] search_streams failed: %s", e)
            return f"Error searching streams: {e}"

    @mcp.tool()
    async def get_streams_by_ids(stream_ids: list[int]) -> str:
        """Fetch detailed stream information for specific stream IDs.

        Args:
            stream_ids: List of stream IDs to look up
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                "/api/streams/by-ids",
                json_data={"stream_ids": stream_ids},
            )

            streams = result if isinstance(result, list) else result.get("streams", result.get("results", []))

            if not streams:
                return f"No streams found for the given {len(stream_ids)} IDs."

            lines = [f"Found {len(streams)} of {len(stream_ids)} requested streams:"]
            for s in streams:
                name = s.get("name", "Unknown")
                sid = s.get("id", "?")
                group = s.get("group", "")
                provider = s.get("provider_name", "")
                info = f" [{group}]" if group else ""
                info += f" from {provider}" if provider else ""
                lines.append(f"  {name} (id={sid}){info}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_streams_by_ids failed: %s", e)
            return f"Error fetching streams by IDs: {e}"

    @mcp.tool()
    async def probe_bulk_streams(stream_ids: list[int]) -> str:
        """Probe multiple streams at once and return health results summary.

        Args:
            stream_ids: List of stream IDs to probe
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                "/api/stream-stats/probe/bulk",
                json_data={"stream_ids": stream_ids},
                timeout=300.0,
            )

            if isinstance(result, dict):
                total = result.get("total", len(stream_ids))
                success = result.get("success", 0)
                failed = result.get("failed", 0)
                lines = [
                    f"Bulk probe completed for {total} streams:",
                    f"  Success: {success}",
                    f"  Failed: {failed}",
                ]
                results_list = result.get("results", [])
                if results_list:
                    failed_streams = [r for r in results_list if r.get("status") == "failed"]
                    if failed_streams:
                        lines.append("  Failed streams:")
                        for r in failed_streams[:20]:
                            name = r.get("name", f"id={r.get('stream_id', '?')}")
                            error = r.get("error", "unknown error")
                            lines.append(f"    - {name}: {error}")
                        if len(failed_streams) > 20:
                            lines.append(f"    ... and {len(failed_streams) - 20} more failures")
                return "\n".join(lines)

            return f"Bulk probe started for {len(stream_ids)} streams. {result}"
        except Exception as e:
            logger.error("[MCP] probe_bulk_streams failed: %s", e)
            return f"Error probing streams in bulk: {e}"

    async def _fuzzy_search(
        client,
        name: str,
        provider_id: int | None = None,
        market: str = "east",
    ) -> tuple[dict | None, list[dict]]:
        """Search for a stream using multiple name variants. Returns (best_match, all_results)."""
        variants = _generate_variants(name)

        seen_ids = set()
        all_results = []
        for variant in variants:
            try:
                params = {"search": variant, "page_size": 10}
                if provider_id is not None:
                    params["provider_id"] = provider_id
                result = await client.get("/api/streams", **params)
                streams = result.get("results", result.get("streams", [])) if isinstance(result, dict) else result
                for s in streams:
                    sid = s.get("id")
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        all_results.append(s)
            except Exception:
                continue

        if not all_results:
            return None, []

        # Score and sort results
        scored = []
        for s in all_results:
            sname = s.get("name", "")
            score = _score_match(name, sname, market)
            scored.append((score, s))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Only return best match if score is positive (avoid garbage matches)
        if scored[0][0] <= 0:
            return None, [s for _, s in scored]

        best = scored[0][1]
        alternatives = [s for _, s in scored[1:]]
        return best, alternatives

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
                params = {"search": query, "page_size": min(limit_per_query, 100)}
                if provider_id is not None:
                    params["provider_id"] = provider_id
                result = await client.get("/api/streams", **params)
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
    async def fuzzy_match_stream(
        name: str,
        provider_id: int | None = None,
        market: str = "east",
    ) -> str:
        """Search for a stream using multiple name variants automatically (with TV, HD, East/West suffixes, etc).

        Args:
            name: Stream name to search for
            provider_id: Optional M3U provider ID to narrow the search
            market: Preferred market - "east" or "west" (default "east")
        """
        try:
            client = get_ecm_client()
            best, alternatives = await _fuzzy_search(client, name, provider_id, market)

            if not best:
                upper = name.upper()
                variants = [upper]
                if not upper.endswith(" TV"):
                    variants.append(f"{upper} TV")
                if not upper.endswith(" CHANNEL"):
                    variants.append(f"{upper} Channel")
                variants.append(f"{upper} HD")
                variants.append(f"{upper} {market.capitalize()}")
                return f'No match found for "{name}" (tried: {", ".join(variants)})'

            best_name = best.get("name", "Unknown")
            best_id = best.get("id", "?")
            lines = [f'Best match for "{name}": {best_name} (id={best_id})']
            if alternatives:
                lines.append("  Also found:")
                for s in alternatives[:10]:
                    lines.append(f"    {s.get('name', 'Unknown')} (id={s.get('id', '?')})")
                if len(alternatives) > 10:
                    lines.append(f"    ... and {len(alternatives) - 10} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] fuzzy_match_stream failed: %s", e)
            return f"Error fuzzy matching stream: {e}"

    @mcp.tool()
    async def match_streams_to_channels(
        group_id: int,
        provider_id: int | None = None,
        market: str = "east",
    ) -> str:
        """Auto-match streams to unassigned channels in a group using fuzzy name matching.

        Gets all channels in the group that have 0 streams, fuzzy-matches each channel
        name to a stream, and assigns the best match.

        Args:
            group_id: Channel group ID to process
            provider_id: Optional M3U provider ID to narrow the search
            market: Preferred market - "east" or "west" (default "east")
        """
        try:
            client = get_ecm_client()

            # Paginate through all channels in the group
            all_channels = []
            page = 1
            while True:
                result = await client.get(
                    "/api/channels",
                    group_id=group_id,
                    page=page,
                    page_size=500,
                )
                if isinstance(result, dict):
                    channels = result.get("results", [])
                    all_channels.extend(channels)
                    if not result.get("next"):
                        break
                    page += 1
                else:
                    all_channels.extend(result)
                    break

            if not all_channels:
                return f"No channels found in group {group_id}."

            # Filter to channels with 0 streams
            unassigned = []
            for ch in all_channels:
                stream_count = len(ch.get("streams", []))
                if stream_count == 0:
                    unassigned.append(ch)

            if not unassigned:
                return f"All {len(all_channels)} channels in group {group_id} already have streams assigned."

            matched = []
            unmatched = []
            for ch in unassigned:
                ch_name = ch.get("name", "")
                ch_id = ch.get("id")
                ch_num = ch.get("channel_number", "?")

                best, _ = await _fuzzy_search(client, ch_name, provider_id, market)
                if best:
                    stream_id = best.get("id")
                    stream_name = best.get("name", "Unknown")
                    try:
                        await client.post(
                            f"/api/channels/{ch_id}/add-stream",
                            json_data={"stream_id": stream_id},
                        )
                        matched.append((ch_num, ch_name, stream_name, stream_id))
                    except Exception as assign_err:
                        unmatched.append((ch_num, ch_name, f"assign failed: {assign_err}"))
                else:
                    unmatched.append((ch_num, ch_name, "no streams found"))

            lines = [f"Matched {len(matched)} of {len(unassigned)} unassigned channels in group {group_id}:"]
            for i, (num, ch_name, s_name, sid) in enumerate(matched):
                if i >= 50:
                    lines.append(f"  ... and {len(matched) - 50} more")
                    break
                lines.append(f"  #{num} {ch_name} → {s_name} (id={sid})")

            if unmatched:
                lines.append(f"Unmatched ({len(unmatched)}):")
                for num, ch_name, reason in unmatched:
                    lines.append(f"  #{num} {ch_name} — {reason}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] match_streams_to_channels failed: %s", e)
            return f"Error matching streams to channels: {e}"
