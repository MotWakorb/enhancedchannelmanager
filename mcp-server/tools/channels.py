"""Channel management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_channels(
        group_id: int | None = None,
        search: str | None = None,
        max_streams: int | None = None,
        min_streams: int | None = None,
        limit: int = 50,
        compact: bool = False,
    ) -> str:
        """List all channels, optionally filtered by group, search, or stream count.

        Args:
            group_id: Filter by channel group ID
            search: Search channels by name
            max_streams: Only show channels with at most this many streams (e.g., 0 for empty channels)
            min_streams: Only show channels with at least this many streams
            limit: Maximum results to return (default 50)
            compact: If True, output pipe-delimited format optimized for agent consumption (ignores limit, shows all)
        """
        try:
            client = get_ecm_client()
            filtering_by_streams = max_streams is not None or min_streams is not None

            # When filtering by stream count or compact mode, fetch all pages
            if filtering_by_streams or compact:
                all_channels = []
                page = 1
                while True:
                    result = await client.get(
                        "/api/channels", group_id=group_id, search=search,
                        page=page, page_size=500,
                    )
                    if isinstance(result, dict):
                        batch = result.get("results", result.get("channels", []))
                    else:
                        batch = result
                    if not batch:
                        break
                    all_channels.extend(batch)
                    # Stop if no next page
                    if isinstance(result, dict) and not result.get("next"):
                        break
                    page += 1

                # Apply stream count filters
                filtered = all_channels
                if min_streams is not None:
                    filtered = [c for c in filtered if len(c.get("streams", [])) >= min_streams]
                if max_streams is not None:
                    filtered = [c for c in filtered if len(c.get("streams", [])) <= max_streams]

                channels = filtered
                total = len(channels)
            else:
                result = await client.get(
                    "/api/channels", group_id=group_id, search=search, page_size=limit,
                )
                if isinstance(result, dict):
                    channels = result.get("results", result.get("channels", []))
                    total = result.get("count", len(channels))
                else:
                    channels = result
                    total = len(channels)

            if not channels:
                filter_desc = ""
                if max_streams is not None:
                    filter_desc += f" with at most {max_streams} streams"
                if min_streams is not None:
                    filter_desc += f" with at least {min_streams} streams"
                return f"No channels found{filter_desc}."

            if compact:
                lines = ["number|id|name|streams"]
                for c in channels:
                    num = c.get("channel_number", "?")
                    cid = c.get("id", "?")
                    name = c.get("name", "Unknown")
                    stream_count = len(c.get("streams", []))
                    lines.append(f"{num}|{cid}|{name}|{stream_count}")
                return "\n".join(lines)

            shown = channels[:limit]
            lines = [f"Found {total} channels (showing {len(shown)}):"]
            for c in shown:
                num = c.get("channel_number", "?")
                name = c.get("name", "Unknown")
                cid = c.get("id", "?")
                stream_count = len(c.get("streams", []))
                lines.append(f"  #{num}: {name} (id={cid}) — {stream_count} streams")

            if total > len(shown):
                lines.append(f"  ... and {total - len(shown)} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_channels failed: %s", e)
            return f"Error listing channels: {e}"

    @mcp.tool()
    async def get_channel(channel_id: int) -> str:
        """Get detailed information about a specific channel.

        Args:
            channel_id: The channel ID to look up
        """
        try:
            client = get_ecm_client()
            c = await client.get(f"/api/channels/{channel_id}")

            stream_ids = c.get("streams", [])
            lines = [
                f"Channel: {c.get('name', 'Unknown')}",
                f"  ID: {c.get('id')}",
                f"  Number: {c.get('channel_number', 'N/A')}",
                f"  Group ID: {c.get('channel_group_id', 'None')}",
                f"  EPG TVG ID: {c.get('tvg_id', 'None')}",
                f"  Logo: {'Yes' if c.get('logo_id') else 'No'}",
                f"  Streams: {len(stream_ids)} (IDs: {stream_ids[:10]}{'...' if len(stream_ids) > 10 else ''})",
                f"  Auto-created: {c.get('auto_created', False)}",
            ]

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_channel failed: %s", e)
            return f"Error getting channel {channel_id}: {e}"

    @mcp.tool()
    async def create_channel(
        name: str,
        channel_number: int | None = None,
        group_id: int | None = None,
    ) -> str:
        """Create a new channel.

        Args:
            name: Channel name
            channel_number: Optional channel number
            group_id: Optional channel group ID to assign the channel to
        """
        try:
            client = get_ecm_client()
            payload = {"name": name}
            if channel_number is not None:
                payload["channel_number"] = channel_number
            if group_id is not None:
                payload["group_id"] = group_id

            result = await client.post("/api/channels", json_data=payload)
            cid = result.get("id", "?")
            return f"Channel created: #{result.get('channel_number', '?')}: {name} (id={cid})"
        except Exception as e:
            logger.error("[MCP] create_channel failed: %s", e)
            return f"Error creating channel: {e}"

    @mcp.tool()
    async def update_channel(
        channel_id: int,
        name: str | None = None,
        channel_number: int | None = None,
        group_id: int | None = None,
    ) -> str:
        """Update an existing channel.

        Args:
            channel_id: The channel ID to update
            name: New channel name
            channel_number: New channel number
            group_id: New channel group ID
        """
        try:
            client = get_ecm_client()
            payload = {}
            if name is not None:
                payload["name"] = name
            if channel_number is not None:
                payload["channel_number"] = channel_number
            if group_id is not None:
                payload["group_id"] = group_id

            if not payload:
                return "No changes specified."

            result = await client.patch(f"/api/channels/{channel_id}", json_data=payload)
            return f"Channel {channel_id} updated: {result.get('name', 'OK')}"
        except Exception as e:
            logger.error("[MCP] update_channel failed: %s", e)
            return f"Error updating channel {channel_id}: {e}"

    @mcp.tool()
    async def delete_channel(channel_id: int) -> str:
        """Delete a channel.

        Args:
            channel_id: The channel ID to delete
        """
        try:
            client = get_ecm_client()
            await client.delete(f"/api/channels/{channel_id}")
            return f"Channel {channel_id} deleted."
        except Exception as e:
            logger.error("[MCP] delete_channel failed: %s", e)
            return f"Error deleting channel {channel_id}: {e}"

    @mcp.tool()
    async def bulk_delete_channels(channel_ids: list[int]) -> str:
        """Delete multiple channels at once.

        Args:
            channel_ids: List of channel IDs to delete
        """
        try:
            client = get_ecm_client()
            deleted = 0
            errors = 0
            error_details = []
            for cid in channel_ids:
                try:
                    await client.delete(f"/api/channels/{cid}")
                    deleted += 1
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        error_details.append(f"  Channel {cid}: {e}")

            lines = [f"Bulk delete complete: {deleted} deleted, {errors} errors out of {len(channel_ids)} requested."]
            if error_details:
                lines.append("First errors:")
                lines.extend(error_details)
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_delete_channels failed: %s", e)
            return f"Error in bulk delete: {e}"

    @mcp.tool()
    async def add_stream_to_channel(channel_id: int, stream_id: int) -> str:
        """Add a stream to a channel.

        Args:
            channel_id: The channel to add the stream to
            stream_id: The stream ID to add
        """
        try:
            client = get_ecm_client()
            await client.post(
                f"/api/channels/{channel_id}/add-stream",
                json_data={"stream_id": stream_id},
            )
            return f"Stream {stream_id} added to channel {channel_id}."
        except Exception as e:
            logger.error("[MCP] add_stream_to_channel failed: %s", e)
            return f"Error adding stream {stream_id} to channel {channel_id}: {e}"

    @mcp.tool()
    async def bulk_add_streams_to_channel(channel_id: int, stream_ids: list[int]) -> str:
        """Add multiple streams to a channel at once.

        Args:
            channel_id: The channel to add streams to
            stream_ids: List of stream IDs to add
        """
        try:
            client = get_ecm_client()
            added = 0
            errors = []
            for sid in stream_ids:
                try:
                    await client.post(
                        f"/api/channels/{channel_id}/add-stream",
                        json_data={"stream_id": sid},
                    )
                    added += 1
                except Exception as e:
                    errors.append(f"stream {sid}: {e}")

            lines = [f"Added {added}/{len(stream_ids)} streams to channel {channel_id}."]
            if errors:
                lines.append(f"Errors ({len(errors)}):")
                for err in errors[:10]:
                    lines.append(f"  - {err}")
                if len(errors) > 10:
                    lines.append(f"  ... and {len(errors) - 10} more")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_add_streams_to_channel failed: %s", e)
            return f"Error adding streams to channel {channel_id}: {e}"

    @mcp.tool()
    async def bulk_assign_epg(mappings: list[dict]) -> str:
        """Assign EPG IDs (tvg_id) to multiple channels at once.

        Args:
            mappings: List of dicts, each with 'channel_id' (int) and 'tvg_id' (str).
                Example: [{"channel_id": 1, "tvg_id": "ESPN.us"}, {"channel_id": 2, "tvg_id": "CNN.us"}]
                Set tvg_id to "" to clear the EPG assignment.
        """
        try:
            client = get_ecm_client()
            updated = 0
            errors = []
            for m in mappings:
                cid = m.get("channel_id")
                tvg = m.get("tvg_id", "")
                if cid is None:
                    errors.append("missing channel_id in mapping")
                    continue
                try:
                    await client.patch(f"/api/channels/{cid}", json_data={"tvg_id": tvg})
                    updated += 1
                except Exception as e:
                    errors.append(f"channel {cid}: {e}")

            lines = [f"Updated EPG assignments for {updated}/{len(mappings)} channels."]
            if errors:
                lines.append(f"Errors ({len(errors)}):")
                for err in errors[:10]:
                    lines.append(f"  - {err}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_assign_epg failed: %s", e)
            return f"Error assigning EPG IDs: {e}"

    @mcp.tool()
    async def remove_stream_from_channel(channel_id: int, stream_id: int) -> str:
        """Remove a stream from a channel.

        Args:
            channel_id: The channel to remove the stream from
            stream_id: The stream ID to remove
        """
        try:
            client = get_ecm_client()
            await client.post(
                f"/api/channels/{channel_id}/remove-stream",
                json_data={"stream_id": stream_id},
            )
            return f"Stream {stream_id} removed from channel {channel_id}."
        except Exception as e:
            logger.error("[MCP] remove_stream_from_channel failed: %s", e)
            return f"Error removing stream {stream_id} from channel {channel_id}: {e}"

    @mcp.tool()
    async def reorder_streams(channel_id: int, stream_ids: list[int]) -> str:
        """Reorder streams within a channel. The order of stream_ids defines the new priority.

        Args:
            channel_id: The channel whose streams to reorder
            stream_ids: Ordered list of stream IDs (first = highest priority)
        """
        try:
            client = get_ecm_client()
            await client.post(
                f"/api/channels/{channel_id}/reorder-streams",
                json_data={"stream_ids": stream_ids},
            )
            return f"Streams reordered for channel {channel_id}. New order: {stream_ids}"
        except Exception as e:
            logger.error("[MCP] reorder_streams failed: %s", e)
            return f"Error reordering streams for channel {channel_id}: {e}"

    @mcp.tool()
    async def assign_channel_numbers(
        channel_ids: list[int],
        starting_number: int | None = None,
    ) -> str:
        """Bulk-assign sequential channel numbers to a list of channels.

        Args:
            channel_ids: List of channel IDs to number
            starting_number: Starting number (auto-assigned if omitted)
        """
        try:
            client = get_ecm_client()
            payload = {"channel_ids": channel_ids}
            if starting_number is not None:
                payload["starting_number"] = starting_number
            await client.post("/api/channels/assign-numbers", json_data=payload)
            return f"Assigned numbers to {len(channel_ids)} channels starting from {starting_number or 'auto'}."
        except Exception as e:
            logger.error("[MCP] assign_channel_numbers failed: %s", e)
            return f"Error assigning channel numbers: {e}"

    @mcp.tool()
    async def merge_channels(
        target_channel_id: int,
        source_channel_ids: list[int],
    ) -> str:
        """Merge multiple channels into one, combining all their streams.

        Args:
            target_channel_id: The channel to merge INTO (keeps this channel)
            source_channel_ids: Channels to merge FROM (these get deleted after merge)
        """
        try:
            client = get_ecm_client()
            await client.post("/api/channels/merge", json_data={
                "target_channel_id": target_channel_id,
                "source_channel_ids": source_channel_ids,
            })
            merged = len(source_channel_ids)
            return f"Merged {merged} channels into channel {target_channel_id}."
        except Exception as e:
            logger.error("[MCP] merge_channels failed: %s", e)
            return f"Error merging channels: {e}"

    @mcp.tool()
    async def clear_auto_created(group_ids: list[int] | None = None) -> str:
        """Clear channels that were created by the auto-creation pipeline.

        Args:
            group_ids: Optional list of group IDs to limit clearing to specific groups.
                       If omitted, clears all auto-created channels.
        """
        try:
            client = get_ecm_client()
            payload = {}
            if group_ids is not None:
                payload["group_ids"] = group_ids
            result = await client.post("/api/channels/clear-auto-created", json_data=payload)
            deleted = result.get("deleted", result.get("count", 0))
            scope = f"in {len(group_ids)} groups" if group_ids else "across all groups"
            return f"Cleared {deleted} auto-created channels {scope}."
        except Exception as e:
            logger.error("[MCP] clear_auto_created failed: %s", e)
            return f"Error clearing auto-created channels: {e}"

    @mcp.tool()
    async def find_duplicate_channels() -> str:
        """Scan all channels for duplicates by applying normalization rules to names.

        Returns groups of channels that resolve to the same normalized name.
        Useful for finding channels like "ESPN" and "◉ ESPN" that should be merged.
        """
        try:
            client = get_ecm_client()
            result = await client.post("/api/channels/find-duplicates", timeout=120.0)
            groups = result.get("groups", [])

            if not groups:
                return "No duplicate channels found."

            total = result.get("total_duplicate_channels", 0)
            lines = [f"Found {len(groups)} duplicate groups ({total} channels total):\n"]
            for g in groups[:30]:
                norm = g.get("normalized_name", "?")
                channels = g.get("channels", [])
                lines.append(f"  '{norm}' ({len(channels)} channels):")
                for ch in channels:
                    num = ch.get("channel_number", "?")
                    name = ch.get("name", "?")
                    streams = ch.get("stream_count", 0)
                    cid = ch.get("id", "?")
                    group_name = ch.get("channel_group_name", "")
                    group_info = f" [{group_name}]" if group_name else ""
                    lines.append(f"    #{num}: {name} (id={cid}, {streams} streams){group_info}")

            if len(groups) > 30:
                lines.append(f"\n  ... and {len(groups) - 30} more groups")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] find_duplicate_channels failed: %s", e)
            return f"Error finding duplicates: {e}"

    @mcp.tool()
    async def bulk_merge_duplicate_channels(
        merges: list[dict],
    ) -> str:
        """Merge groups of duplicate channels.

        Args:
            merges: List of merge operations. Each dict has:
                target_channel_id (int): Channel to keep
                source_channel_ids (list[int]): Channels to absorb and delete
            Example: [{"target_channel_id": 100, "source_channel_ids": [101, 102]}]
        """
        try:
            client = get_ecm_client()
            result = await client.post(
                "/api/channels/bulk-merge",
                json_data={"merges": merges},
                timeout=300.0,
            )
            merged = result.get("merged", 0)
            failed = result.get("failed", 0)

            lines = [f"Bulk merge complete: {merged} merged, {failed} failed."]
            for r in result.get("results", []):
                if r.get("success"):
                    lines.append(f"  ✓ '{r.get('target_name')}': absorbed {r.get('sources_deleted', 0)} channels, {r.get('total_streams', 0)} streams")
                else:
                    lines.append(f"  ✗ Channel {r.get('target_channel_id')}: {r.get('error', 'unknown')}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_merge_duplicate_channels failed: %s", e)
            return f"Error in bulk merge: {e}"

    @mcp.tool()
    async def bulk_commit_channels(
        operations: list[dict],
        validate_only: bool = False,
        continue_on_error: bool = False,
    ) -> str:
        """Commit a batch of channel operations atomically.

        Supported operation types: createChannel, updateChannel, deleteChannel,
        addStreamToChannel, removeStreamFromChannel, reorderChannelStreams,
        createGroup, deleteChannelGroup, renameChannelGroup.

        Args:
            operations: List of operation dicts, each with a "type" key and type-specific fields.
            validate_only: If True, validate without applying changes.
            continue_on_error: If True, keep processing after individual operation failures.
        """
        try:
            client = get_ecm_client()
            payload = {
                "operations": operations,
                "validateOnly": validate_only,
                "continueOnError": continue_on_error,
            }
            result = await client.post("/api/channels/bulk-commit", json_data=payload)
            success = result.get("success", False)
            # Per-operation result list is available at result["results"] but not
            # surfaced in the response below — the operator gets aggregate status,
            # id_mappings, and validation issues only.
            id_mappings = result.get("idMappings", {})
            issues = result.get("validationIssues", [])

            lines = []
            status = "SUCCESS" if success else "FAILED"
            lines.append(f"Bulk commit {status}: {len(operations)} operations submitted.")
            if validate_only:
                lines.append("(validate-only mode — no changes applied)")
            if id_mappings:
                mapped = ", ".join(f"{k} -> {v}" for k, v in id_mappings.items())
                lines.append(f"ID mappings: {mapped}")
            if issues:
                lines.append(f"Validation issues ({len(issues)}):")
                for issue in issues[:10]:
                    lines.append(f"  - {issue}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] bulk_commit_channels failed: %s", e)
            return f"Error in bulk commit: {e}"

    @mcp.tool()
    async def set_logo_from_epg(channel_ids: list[int]) -> str:
        """Set channel logos from their linked EPG entry's icon_url.

        For each channel: reads its epg_data_id, fetches the linked EPG entry from
        Dispatcharr (/api/epg/data/{id}), creates-or-finds a Logo from the entry's
        icon_url, then PATCHes the channel with the resulting logo_id. Mirrors the
        UI's "Set Logo from EPG" bulk action and uses Dispatcharr's APIs end to end.

        Args:
            channel_ids: List of channel IDs to assign EPG-sourced logos to.
                         Pass a single-element list for the single-channel case.
        """
        try:
            client = get_ecm_client()
            assigned = 0
            skipped_no_epg = 0
            skipped_no_icon = 0
            errors: list[str] = []
            logo_cache: dict[str, int] = {}

            for cid in channel_ids:
                try:
                    channel = await client.get(f"/api/channels/{cid}")
                    epg_data_id = channel.get("epg_data_id")
                    if not epg_data_id:
                        skipped_no_epg += 1
                        continue

                    epg_entry = await client.get(f"/api/epg/data/{epg_data_id}")
                    icon_url = epg_entry.get("icon_url") or epg_entry.get("icon")
                    if not icon_url:
                        skipped_no_icon += 1
                        continue

                    if icon_url in logo_cache:
                        logo_id = logo_cache[icon_url]
                    else:
                        logo = await client.post(
                            "/api/channels/logos",
                            json_data={"name": channel.get("name") or f"channel-{cid}", "url": icon_url},
                        )
                        logo_id = logo.get("id")
                        if logo_id is None:
                            errors.append(f"channel {cid}: logo create returned no id")
                            continue
                        logo_cache[icon_url] = logo_id

                    await client.patch(f"/api/channels/{cid}", json_data={"logo_id": logo_id})
                    assigned += 1
                except Exception as e:
                    errors.append(f"channel {cid}: {e}")

            lines = [
                f"Set logos from EPG: {assigned} assigned, "
                f"{skipped_no_epg} skipped (no EPG link), "
                f"{skipped_no_icon} skipped (no icon_url), "
                f"{len(errors)} errors out of {len(channel_ids)} requested.",
            ]
            if errors:
                lines.append("First errors:")
                for err in errors[:5]:
                    lines.append(f"  - {err}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] set_logo_from_epg failed: %s", e)
            return f"Error setting logos from EPG: {e}"

    @mcp.tool()
    async def build_channel_lineup(
        channels: list[dict],
        group_id: int,
        provider_id: int | None = None,
        market: str = "east",
    ) -> str:
        """Build a channel lineup: bulk-create channels, then fuzzy-match and assign streams.

        Args:
            channels: List of dicts with 'name' (str) and 'number' (int/float) for each channel.
            group_id: Channel group ID to create channels in.
            provider_id: Optional M3U provider ID to limit stream search.
            market: Market preference for stream matching ('east' or 'west'). Default 'east'.
        """
        try:
            client = get_ecm_client()

            # Step 1: Bulk-create all channels
            operations = []
            for i, ch in enumerate(channels):
                operations.append({
                    "type": "createChannel",
                    "tempId": -(i + 1),
                    "name": ch["name"],
                    "channelNumber": ch["number"],
                    "groupId": group_id,
                })
            bulk_result = await client.post("/api/channels/bulk-commit", json_data={
                "operations": operations,
                "continueOnError": True,
            })
            if not bulk_result.get("success"):
                issues = bulk_result.get("validationIssues", [])
                return f"Bulk create failed: {issues[:5]}"

            # Step 2: Fetch newly created channels from the group
            created_channels = []
            page = 1
            while True:
                result = await client.get(
                    "/api/channels", group_id=group_id, page=page, page_size=500,
                )
                if isinstance(result, dict):
                    batch = result.get("results", result.get("channels", []))
                else:
                    batch = result
                if not batch:
                    break
                created_channels.extend(batch)
                if isinstance(result, dict) and not result.get("next"):
                    break
                page += 1

            # Step 3: For each channel with 0 streams, fuzzy-match a stream
            # Import shared fuzzy matching from streams module
            from tools.streams import _fuzzy_helpers
            _generate_variants = _fuzzy_helpers["generate_variants"]
            _score_match = _fuzzy_helpers["score_match"]

            matches = []
            unmatched = []

            for ch in created_channels:
                if len(ch.get("streams", [])) > 0:
                    continue

                ch_name = ch.get("name", "")
                best_stream = None
                best_score = -999

                variants = _generate_variants(ch_name)
                seen_streams = set()

                for variant in variants:
                    params = {"search": variant, "page_size": 10}
                    if provider_id is not None:
                        params["provider_id"] = provider_id
                    try:
                        sr = await client.get("/api/streams", **params)
                        if isinstance(sr, dict):
                            stream_list = sr.get("results", sr.get("streams", []))
                        else:
                            stream_list = sr
                    except Exception:
                        continue

                    for s in stream_list:
                        sid = s.get("id")
                        if sid in seen_streams:
                            continue
                        seen_streams.add(sid)
                        s_name = s.get("name", "")
                        score = _score_match(ch_name, s_name, market)
                        if score > best_score:
                            best_score = score
                            best_stream = s

                if best_stream and best_score > 0:
                    matches.append((ch, best_stream, best_score))
                else:
                    unmatched.append(ch)

            # Step 4: Assign matched streams
            assign_errors = 0
            for ch, stream, _score in matches:
                try:
                    await client.post(
                        f"/api/channels/{ch['id']}/add-stream",
                        json_data={"stream_id": stream["id"]},
                    )
                except Exception:
                    assign_errors += 1

            # Step 5: Build summary
            lines = [
                f"Lineup built: {len(created_channels)} channels created, "
                f"{len(matches)} streams matched, {len(unmatched)} unmatched.",
            ]
            if assign_errors:
                lines.append(f"  ({assign_errors} stream assignments failed)")

            if matches:
                lines.append("Matches:")
                for ch, stream, score in matches[:30]:
                    s_name = stream.get("name", "?")
                    lines.append(f"  #{ch.get('channel_number', '?')} {ch.get('name')} -> {s_name} (score={score})")
                if len(matches) > 30:
                    lines.append(f"  ... and {len(matches) - 30} more")

            if unmatched:
                lines.append("Unmatched:")
                for ch in unmatched[:30]:
                    lines.append(f"  #{ch.get('channel_number', '?')} {ch.get('name')}")
                if len(unmatched) > 30:
                    lines.append(f"  ... and {len(unmatched) - 30} more")

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] build_channel_lineup failed: %s", e)
            return f"Error building channel lineup: {e}"
