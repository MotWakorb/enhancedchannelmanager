"""Channel management tools."""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
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
                    result = await client.call_endpoint(
                        ENDPOINTS["channels_list"],
                        query={"channel_group": group_id, "search": search,
                               "page": page, "page_size": 500},
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
                result = await client.call_endpoint(
                    ENDPOINTS["channels_list"],
                    query={"channel_group": group_id, "search": search, "page_size": limit},
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
            c = await client.call_endpoint(ENDPOINTS["channels_get"], path_args={"channel_id": channel_id})

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
                # Backend POST /api/channels expects ``channel_group_id``;
                # a bare ``group_id`` is silently dropped (bd-7q9l3 / GH #221).
                payload["channel_group_id"] = group_id

            result = await client.call_endpoint(ENDPOINTS["channels_create"], body=payload)
            # Report the resulting object (not the request) so the caller can
            # see what actually got created.
            cid = result.get("id", "?")
            rname = result.get("name", name)
            rnum = result.get("channel_number", "?")
            rgrp = result.get("channel_group_id")
            grp_info = f", group_id={rgrp}" if rgrp is not None else ""
            return f"Channel created: #{rnum}: {rname} (id={cid}{grp_info})"
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
                # Backend PATCH /api/channels/{id} forwards keys straight to
                # Dispatcharr, whose channel field is ``channel_group_id``;
                # a bare ``group_id`` is silently dropped (bd-7q9l3 / GH #221).
                payload["channel_group_id"] = group_id

            if not payload:
                return "No changes specified."

            result = await client.call_endpoint(
                ENDPOINTS["channels_update"], path_args={"channel_id": channel_id}, body=payload,
            )
            # Report the *resulting* state from the response, not the request —
            # so a 200-with-no-effect can't fool the caller (the #221 failure mode).
            if isinstance(result, dict):
                rname = result.get("name", "?")
                rnum = result.get("channel_number", "?")
                rgrp = result.get("channel_group_id")
                return f"Channel {channel_id} updated: name='{rname}', channel_number={rnum}, group_id={rgrp}"
            return f"Channel {channel_id} updated."
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
            await client.call_endpoint(ENDPOINTS["channels_delete"], path_args={"channel_id": channel_id})
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
                    await client.call_endpoint(ENDPOINTS["channels_delete"], path_args={"channel_id": cid})
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
            await client.call_endpoint(
                ENDPOINTS["channels_add_stream"],
                path_args={"channel_id": channel_id},
                body={"stream_id": stream_id},
            )
            return f"Stream {stream_id} added to channel {channel_id}."
        except Exception as e:
            logger.error("[MCP] add_stream_to_channel failed: %s", e)
            return f"Error adding stream {stream_id} to channel {channel_id}: {e}"

    @mcp.tool()
    async def add_stream(
        stream_name: str,
        group_id: int,
        dedup_action: str = "prompt",
    ) -> str:
        """Create a channel from a stream name and assign it to a group, with deduplication control.

        The ``dedup_action`` parameter governs how a potential duplicate channel
        (identified by the BD-A matcher via ``GET /api/channel-merges/candidates``)
        is handled before the new channel is created.  Three modes are supported
        (ADR-008 §D7):

        * ``prompt`` (default) — call the candidates endpoint; if a match above
          the operator-configured threshold is found, return the candidate to the
          agent so it can call ``accept_channel_merge`` or ``dismiss_channel_merge``
          on the existing pending-merges row, or retry with a different
          ``dedup_action``.  If no candidate is found, proceed with normal channel
          creation.
        * ``force_new`` — skip dedup entirely; always create a new channel
          regardless of any existing match.
        * ``merge_if_found`` — call the candidates endpoint; if a match at or
          above the operator threshold is found, add the stream to the candidate
          channel directly (auto-accept).  If the confidence falls below the
          threshold (but above the ADR-008 §D2 hard floor — the matcher never
          emits below the floor), fall back to ``prompt`` semantics and return
          the candidate for the agent to decide.  If no candidate is found,
          proceed with normal channel creation.

        In all cases where channel creation proceeds, the tool creates a new
        channel with the stream name as the channel name, assigns it to
        ``group_id``, finds the stream by name, and attaches it to the new
        channel.

        Args:
            stream_name: The stream name to use as the channel name and to
                search for in the stream list.
            group_id: Channel group ID to assign the new channel to.
            dedup_action: One of ``'prompt'``, ``'force_new'``, or
                ``'merge_if_found'`` (default ``'prompt'``).
        """
        _VALID_DEDUP_ACTIONS = {"prompt", "force_new", "merge_if_found"}
        if dedup_action not in _VALID_DEDUP_ACTIONS:
            return (
                f"Invalid dedup_action '{dedup_action}'. "
                f"Must be one of: {', '.join(sorted(_VALID_DEDUP_ACTIONS))}"
            )

        try:
            client = get_ecm_client()

            # ------------------------------------------------------------------
            # Dedup branch: skip candidate lookup when force_new requested.
            # ------------------------------------------------------------------
            if dedup_action != "force_new":
                try:
                    candidates_resp = await client.call_endpoint(
                        ENDPOINTS["channel_merges_candidates"],
                        query={"stream_name": stream_name, "group_id": group_id},
                    )
                    candidates = candidates_resp.get("candidates", []) if isinstance(candidates_resp, dict) else []
                except Exception as cand_err:
                    logger.warning("[MCP] add_stream candidates lookup failed: %s", cand_err)
                    candidates = []

                if candidates:
                    top = candidates[0]
                    candidate_channel_id = top.get("channel_id", "?")
                    candidate_channel_name = top.get("channel_name", "?")
                    confidence = top.get("confidence", 0.0)

                    if dedup_action == "merge_if_found":
                        # Auto-accept: add the stream directly to the candidate channel.
                        # This requires resolving the stream id first.
                        stream_id = await _resolve_stream_id(client, stream_name)
                        if stream_id is None:
                            return (
                                f"merge_if_found: candidate channel found "
                                f"('{candidate_channel_name}', id={candidate_channel_id}, "
                                f"confidence={confidence:.0%}) but stream '{stream_name}' "
                                f"could not be resolved to a stream ID — "
                                f"use force_new or prompt to proceed."
                            )
                        try:
                            await client.call_endpoint(
                                ENDPOINTS["channels_add_stream"],
                                path_args={"channel_id": candidate_channel_id},
                                body={"stream_id": stream_id},
                            )
                            return (
                                f"merge_if_found: stream '{stream_name}' (id={stream_id}) "
                                f"added to existing channel '{candidate_channel_name}' "
                                f"(id={candidate_channel_id}, confidence={confidence:.0%})."
                            )
                        except Exception as merge_err:
                            logger.warning("[MCP] add_stream merge_if_found failed: %s", merge_err)
                            return (
                                f"merge_if_found: auto-merge to '{candidate_channel_name}' "
                                f"(id={candidate_channel_id}) failed: {merge_err}"
                            )

                    # prompt (or merge_if_found fallback when below threshold —
                    # but the matcher never emits below the ADR-008 §D2 floor, so
                    # any returned candidate is already above the floor; the
                    # operator threshold comparison is intentionally delegated to
                    # the backend matcher).
                    return (
                        f"action=pending_merge — candidate channel found for "
                        f"'{stream_name}':\n"
                        f"  candidate_channel_id: {candidate_channel_id}\n"
                        f"  candidate_channel_name: {candidate_channel_name}\n"
                        f"  confidence: {confidence:.0%}\n"
                        f"Call add_stream(stream_name='{stream_name}', group_id={group_id}, "
                        f"dedup_action='force_new') to create a new channel anyway, or "
                        f"add_stream(stream_name='{stream_name}', group_id={group_id}, "
                        f"dedup_action='merge_if_found') to add the stream to the "
                        f"candidate channel."
                    )

            # ------------------------------------------------------------------
            # No candidate (or force_new): create a new channel and add stream.
            # ------------------------------------------------------------------
            created = await client.call_endpoint(
                ENDPOINTS["channels_create"],
                body={"name": stream_name, "channel_group_id": group_id},
            )
            channel_id = created.get("id")
            channel_name = created.get("name", stream_name)
            if channel_id is None:
                return f"Channel creation returned no id for '{stream_name}'."

            stream_id = await _resolve_stream_id(client, stream_name)
            if stream_id is None:
                return (
                    f"Channel '{channel_name}' created (id={channel_id}) but stream "
                    f"'{stream_name}' could not be found — assign a stream manually."
                )

            await client.call_endpoint(
                ENDPOINTS["channels_add_stream"],
                path_args={"channel_id": channel_id},
                body={"stream_id": stream_id},
            )
            action_word = "force_new: " if dedup_action == "force_new" else ""
            return (
                f"{action_word}Channel '{channel_name}' (id={channel_id}) created in "
                f"group {group_id} with stream '{stream_name}' (id={stream_id}) assigned."
            )

        except Exception as e:
            logger.error("[MCP] add_stream failed: %s", e)
            return f"Error in add_stream: {e}"

    async def _resolve_stream_id(client, stream_name: str) -> int | None:
        """Find the stream id for a given name via a name-search lookup.

        Returns the id of the first result or ``None`` when no match is found.
        This is a best-effort lookup — the caller decides how to handle
        ``None``.
        """
        try:
            result = await client.call_endpoint(
                ENDPOINTS["streams_list"],
                query={"search": stream_name, "page_size": 10},
            )
            streams = (
                result.get("results", result.get("streams", []))
                if isinstance(result, dict)
                else result
            )
            return streams[0].get("id") if streams else None
        except Exception as e:
            logger.warning("[MCP] _resolve_stream_id(%r) failed: %s", stream_name, e)
            return None

    @mcp.tool()
    async def bulk_add_streams_to_channel(channel_id: int, stream_ids: list[int]) -> str:
        """Add multiple streams to a channel in a single backend call.

        Uses POST /api/channels/{id}/add-streams, which fetches the channel
        once and PUTs once (one Dispatcharr roundtrip total) — not one HTTP
        request per stream — so batches of ~10 streams stay well under the
        tool-call budget even on slow hardware (bd-02xjj / GH #223).
        Streams already on the channel are skipped; order is preserved.

        Args:
            channel_id: The channel to add streams to
            stream_ids: List of stream IDs to add
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["channels_add_streams"],
                path_args={"channel_id": channel_id},
                body={"stream_ids": stream_ids},
                timeout=120.0,
            )
            added = result.get("added", []) if isinstance(result, dict) else []
            skipped = result.get("skipped", []) if isinstance(result, dict) else []
            total = result.get("total_streams") if isinstance(result, dict) else None
            lines = [f"Added {len(added)} stream(s) to channel {channel_id}"
                     + (f" ({len(skipped)} already present)" if skipped else "")
                     + (f"; channel now has {total} streams." if total is not None else ".")]
            if added:
                lines.append(f"  Added: {added[:20]}{'...' if len(added) > 20 else ''}")
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
                    await client.call_endpoint(
                        ENDPOINTS["channels_update"], path_args={"channel_id": cid}, body={"tvg_id": tvg},
                    )
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
            await client.call_endpoint(
                ENDPOINTS["channels_remove_stream"],
                path_args={"channel_id": channel_id},
                body={"stream_id": stream_id},
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
            await client.call_endpoint(
                ENDPOINTS["channels_reorder_streams"],
                path_args={"channel_id": channel_id},
                body={"stream_ids": stream_ids},
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
            await client.call_endpoint(ENDPOINTS["channels_assign_numbers"], body=payload)
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
            # Routes through POST /api/channels/bulk-merge with a single merge
            # item — the backend's POST /api/channels/merge endpoint creates a
            # *new* channel from a `target_name` and never accepted
            # `target_channel_id` (the old payload here was silently 422'd —
            # contract drift fixed in bd-vtghg Phase 1). bulk-merge has the
            # "keep target channel, absorb sources" semantics this tool wants.
            await client.call_endpoint(
                ENDPOINTS["channels_bulk_merge"],
                body={"merges": [{
                    "target_channel_id": target_channel_id,
                    "source_channel_ids": source_channel_ids,
                }]},
                timeout=300.0,
            )
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
            result = await client.call_endpoint(ENDPOINTS["channels_clear_auto_created"], body=payload)
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
            result = await client.call_endpoint(ENDPOINTS["channels_find_duplicates"], timeout=120.0)
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
            result = await client.call_endpoint(
                ENDPOINTS["channels_bulk_merge"],
                body={"merges": merges},
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
            result = await client.call_endpoint(ENDPOINTS["channels_bulk_commit"], body=payload)
            success = result.get("success", False)
            # Per-operation result list is available at result["errors"] but not
            # surfaced in the response below — the operator gets aggregate status,
            # the temp-id → real-id map, and validation issues only.
            # (Backend BulkCommitResponse exposes `tempIdMap`/`groupIdMap`, not
            # `idMappings` — the old key here always read empty: contract drift
            # fixed in bd-vtghg Phase 1.)
            id_mappings = {**(result.get("tempIdMap") or {}), **(result.get("groupIdMap") or {})}
            issues = result.get("validationIssues") or []

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

            # contract-exempt: composes per-channel reads + an epg-domain read
            # (/api/epg/data/{id}) + a logo create + a channel PATCH — a
            # multi-call/cross-domain flow that doesn't reduce to one Endpoint.
            for cid in channel_ids:
                try:
                    channel = await client.get(f"/api/channels/{cid}")  # contract-exempt: see above
                    epg_data_id = channel.get("epg_data_id")
                    if not epg_data_id:
                        skipped_no_epg += 1
                        continue

                    epg_entry = await client.get(f"/api/epg/data/{epg_data_id}")  # contract-exempt: part of set_logo_from_epg cross-domain flow (no MCP tool hits /api/epg/data directly)
                    icon_url = epg_entry.get("icon_url") or epg_entry.get("icon")
                    if not icon_url:
                        skipped_no_icon += 1
                        continue

                    if icon_url in logo_cache:
                        logo_id = logo_cache[icon_url]
                    else:
                        logo = await client.post(  # contract-exempt: see above
                            "/api/channels/logos",
                            json_data={"name": channel.get("name") or f"channel-{cid}", "url": icon_url},
                        )
                        logo_id = logo.get("id")
                        if logo_id is None:
                            errors.append(f"channel {cid}: logo create returned no id")
                            continue
                        logo_cache[icon_url] = logo_id

                    await client.patch(f"/api/channels/{cid}", json_data={"logo_id": logo_id})  # contract-exempt: see above
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

            # contract-exempt: orchestrates bulk-commit + paginated channel
            # reads + streams-domain search + per-channel add-stream — a
            # multi-step/cross-domain flow that doesn't reduce to one Endpoint.
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
            bulk_result = await client.post("/api/channels/bulk-commit", json_data={  # contract-exempt: see above
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
                result = await client.get(  # contract-exempt: see above
                    "/api/channels", channel_group=group_id, page=page, page_size=500,
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
                        # Backend GET /api/streams filters by ``m3u_account``
                        # (a bare ``provider_id`` was silently ignored — bd-vtghg).
                        params["m3u_account"] = provider_id
                    try:
                        sr = await client.get("/api/streams", **params)  # contract-exempt: part of build_channel_lineup multi-step/cross-domain flow
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
                    await client.post(  # contract-exempt: see above
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
