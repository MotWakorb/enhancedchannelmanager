"""
Channel Groups router — group CRUD, hide/restore, orphaned detection/deletion,
auto-created detection, and with-streams endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from database import get_session
from dispatcharr_client import get_client
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channel-groups", tags=["Channel Groups"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateChannelGroupRequest(BaseModel):
    name: str


class DeleteOrphanedGroupsRequest(BaseModel):
    group_ids: list[int] | None = None  # Optional list of group IDs to delete

    class Config:
        # Allow extra fields to be ignored (for future compatibility)
        extra = "ignore"


# ---------------------------------------------------------------------------
# Channel Groups list / create / update
# ---------------------------------------------------------------------------

@router.get("")
async def get_channel_groups():
    """List all channel groups (excluding hidden)."""
    logger.debug("[GROUPS] GET /channel-groups")
    client = get_client()
    try:
        start = time.time()
        groups = await client.get_channel_groups()

        # Filter out hidden groups
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_ids = {h.group_id for h in db.query(HiddenChannelGroup).all()}

        # Get M3U group settings to identify auto-sync groups
        m3u_group_settings = await client.get_all_m3u_group_settings()
        auto_sync_group_ids = {
            gid for gid, settings in m3u_group_settings.items()
            if settings.get("auto_channel_sync")
        }

        # Return groups with is_auto_sync flag, filtered by hidden status
        result = []
        for g in groups:
            if g.get("id") not in hidden_ids:
                group_data = dict(g)
                group_data["is_auto_sync"] = g.get("id") in auto_sync_group_ids
                result.append(group_data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[GROUPS] Fetched %s channel groups in %.1fms", len(result), elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[GROUPS] Failed to fetch channel groups")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_channel_group(request: CreateChannelGroupRequest):
    """Create a channel group."""
    logger.debug("[GROUPS] POST /channel-groups - name=%s", request.name)
    client = get_client()
    try:
        start = time.time()
        result = await client.create_channel_group(request.name)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[GROUPS] Created channel group id=%s name=%s in %.1fms", result.get('id'), result.get('name'), elapsed_ms)
        return result
    except Exception as e:
        error_str = str(e)
        # Check if this is a "group already exists" error from Dispatcharr
        if "400" in error_str or "already exists" in error_str.lower():
            try:
                # Look up the existing group by name
                groups = await client.get_channel_groups()
                for group in groups:
                    if group.get("name") == request.name:
                        logger.info("[GROUPS] Found existing channel group id=%s name=%s", group.get('id'), group.get('name'))
                        return group
                logger.warning("[GROUPS] Group exists error but could not find group by name: %s", request.name)
            except Exception as search_err:
                logger.error("[GROUPS] Error searching for existing group: %s", search_err)
        logger.exception("[GROUPS] Channel group creation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{group_id}")
async def update_channel_group(group_id: int, data: dict):
    """Update a channel group."""
    logger.debug("[GROUPS] PATCH /channel-groups/%s - data=%s", group_id, data)
    client = get_client()
    try:
        start = time.time()
        result = await client.update_channel_group(group_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[GROUPS] Updated channel group id=%s in %.1fms", group_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[GROUPS] Failed to update channel group id=%s", group_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Static routes — MUST be defined before /api/channel-groups/{group_id}
# ---------------------------------------------------------------------------

@router.get("/hidden")
async def get_hidden_channel_groups():
    """Get list of all hidden channel groups."""
    logger.debug("[GROUPS] GET /channel-groups/hidden")
    try:
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_groups = db.query(HiddenChannelGroup).all()
            return [g.to_dict() for g in hidden_groups]
    except Exception as e:
        logger.exception("[GROUPS] Failed to get hidden channel groups: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orphaned")
async def get_orphaned_channel_groups():
    """Find channel groups that are truly orphaned.

    A group is considered orphaned if it has no streams AND no channels.
    M3U groups contain streams, manual groups contain channels.
    """
    logger.debug("[GROUPS-ORPHAN] GET /channel-groups/orphaned")
    client = get_client()
    start = time.time()
    try:
        # Get all channel groups from Dispatcharr
        all_groups = await client.get_channel_groups()

        # Get M3U group settings to see which M3U accounts groups were associated with
        m3u_group_settings = await client.get_all_m3u_group_settings()

        # Get all streams (paginated) to check which groups have streams
        streams = []
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=500)
            page_streams = result.get("results", [])
            streams.extend(page_streams)

            # Check if there are more pages
            if len(page_streams) < 500:
                break
            page += 1

        # Get all channels (paginated) to check which groups have channels
        channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            channels.extend(page_channels)

            # Check if there are more pages
            if len(page_channels) < 500:
                break
            page += 1

        # Build map of group_id -> stream count (streams use group ID, not name)
        group_stream_count = {}
        for stream in streams:
            group_id = stream.get("channel_group")
            if group_id:
                group_stream_count[group_id] = group_stream_count.get(group_id, 0) + 1

        # Build map of group_id -> channel count
        group_channel_count = {}
        for channel in channels:
            group_id = channel.get("channel_group_id")
            if group_id:
                group_channel_count[group_id] = group_channel_count.get(group_id, 0) + 1

        # Build a set of group IDs that are targets of group_override from auto_channel_sync M3U groups
        # These groups may be empty now but will be populated by Auto Channel Sync
        group_override_targets = set()
        for group_id, m3u_info in m3u_group_settings.items():
            if m3u_info.get("auto_channel_sync"):
                custom_props = m3u_info.get("custom_properties", {})
                if custom_props and isinstance(custom_props, dict):
                    group_override = custom_props.get("group_override")
                    if group_override:
                        group_override_targets.add(group_override)

        logger.debug("[GROUPS-ORPHAN] Total streams fetched: %s", len(streams))
        logger.debug("[GROUPS-ORPHAN] Total channels fetched: %s", len(channels))
        logger.debug("[GROUPS-ORPHAN] Groups with streams: %s", len(group_stream_count))
        logger.debug("[GROUPS-ORPHAN] Groups with channels: %s", len(group_channel_count))
        logger.debug("[GROUPS-ORPHAN] Groups that are group_override targets: %s", len(group_override_targets))

        # Find orphaned groups
        # A group is orphaned if it has no streams AND no channels AND is NOT in any M3U account
        # AND is NOT a target of group_override from an auto_channel_sync M3U group
        orphaned_groups = []
        for group in all_groups:
            group_id = group["id"]
            group_name = group["name"]

            stream_count = group_stream_count.get(group_id, 0)
            channel_count = group_channel_count.get(group_id, 0)

            # Check if this group is associated with any M3U account
            m3u_info = m3u_group_settings.get(group_id)

            # Check if this group is a target of group_override (will be populated by Auto Channel Sync)
            is_override_target = group_id in group_override_targets

            # Only consider it orphaned if:
            # 1. It has no streams AND no channels
            # 2. AND it's not in any M3U account (truly orphaned from deleted M3U)
            # 3. AND it's not a target of group_override from an auto_channel_sync M3U group
            if stream_count == 0 and channel_count == 0 and m3u_info is None and not is_override_target:
                # Group is truly orphaned - not in any M3U and has no content
                orphaned_groups.append({
                    "id": group_id,
                    "name": group_name,
                    "reason": "No streams, channels, or M3U association",
                })

        # Sort by name for consistent display
        orphaned_groups.sort(key=lambda g: g["name"].lower())

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[GROUPS-ORPHAN] Found %s orphaned channel groups out of %s total in %.1fms", len(orphaned_groups), len(all_groups), elapsed_ms)
        return {
            "orphaned_groups": orphaned_groups,
            "total_groups": len(all_groups),
            "groups_with_content": len(set(list(group_stream_count.keys()) + list(str(gid) for gid in group_channel_count.keys()))),
        }
    except Exception as e:
        logger.exception("[GROUPS-ORPHAN] Failed to find orphaned channel groups: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orphaned")
async def delete_orphaned_channel_groups(request: DeleteOrphanedGroupsRequest | None = Body(None)):
    """Delete channel groups that are truly orphaned.

    A group is deleted if it has no streams AND no channels.
    M3U groups contain streams, manual groups contain channels.

    Args:
        request: Optional request body with group_ids list. If None or empty, all orphaned groups are deleted.
    """
    logger.debug("[GROUPS-ORPHAN] Request received: %s", request)
    logger.debug("[GROUPS-ORPHAN] Request type: %s", type(request))

    client = get_client()
    group_ids = request.group_ids if request else None
    logger.debug("[GROUPS-ORPHAN] Extracted group_ids: %s", group_ids)

    try:
        # Use the same logic as GET to find orphaned groups
        logger.debug("[GROUPS-ORPHAN] Fetching all channel groups...")
        all_groups = await client.get_channel_groups()
        logger.debug("[GROUPS-ORPHAN] Found %s total channel groups", len(all_groups))

        # Get M3U group settings to see which groups are still in M3U accounts
        m3u_group_settings = await client.get_all_m3u_group_settings()

        # Get all streams (paginated)
        streams = []
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=500)
            page_streams = result.get("results", [])
            streams.extend(page_streams)

            # Check if there are more pages
            if len(page_streams) < 500:
                break
            page += 1

        # Get all channels (paginated)
        channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            channels.extend(page_channels)

            # Check if there are more pages
            if len(page_channels) < 500:
                break
            page += 1

        # Build map of group_id -> stream count (streams use group ID, not name)
        group_stream_count = {}
        for stream in streams:
            group_id = stream.get("channel_group")
            if group_id:
                group_stream_count[group_id] = group_stream_count.get(group_id, 0) + 1

        # Build map of group_id -> channel count
        group_channel_count = {}
        for channel in channels:
            group_id = channel.get("channel_group_id")
            if group_id:
                group_channel_count[group_id] = group_channel_count.get(group_id, 0) + 1

        # Build a set of group IDs that are targets of group_override from auto_channel_sync M3U groups
        # These groups may be empty now but will be populated by Auto Channel Sync
        group_override_targets = set()
        for group_id, m3u_info in m3u_group_settings.items():
            if m3u_info.get("auto_channel_sync"):
                custom_props = m3u_info.get("custom_properties", {})
                if custom_props and isinstance(custom_props, dict):
                    group_override = custom_props.get("group_override")
                    if group_override:
                        group_override_targets.add(group_override)

        # Find orphaned groups
        # A group is orphaned if it has no streams AND no channels AND is NOT in any M3U account
        # AND is NOT a target of group_override from an auto_channel_sync M3U group
        logger.debug("[GROUPS-ORPHAN] Identifying orphaned groups...")
        orphaned_groups = []
        for group in all_groups:
            group_id = group["id"]
            group_name = group["name"]

            stream_count = group_stream_count.get(group_id, 0)
            channel_count = group_channel_count.get(group_id, 0)

            # Check if this group is associated with any M3U account
            m3u_info = m3u_group_settings.get(group_id)

            # Check if this group is a target of group_override (will be populated by Auto Channel Sync)
            is_override_target = group_id in group_override_targets

            # Only consider it orphaned if:
            # 1. It has no streams AND no channels
            # 2. AND it's not in any M3U account (truly orphaned from deleted M3U)
            # 3. AND it's not a target of group_override from an auto_channel_sync M3U group
            if stream_count == 0 and channel_count == 0 and m3u_info is None and not is_override_target:
                # Group is truly orphaned - not in any M3U and has no content
                orphaned_groups.append({
                    "id": group_id,
                    "name": group_name,
                    "reason": "No streams, channels, or M3U association",
                })
                logger.debug("[GROUPS-ORPHAN] Group %s (%s) is orphaned: streams=%s, channels=%s, m3u=%s, override_target=%s", group_id, group_name, stream_count, channel_count, m3u_info is not None, is_override_target)

        logger.debug("[GROUPS-ORPHAN] Found %s orphaned groups", len(orphaned_groups))

        if not orphaned_groups:
            logger.debug("[GROUPS-ORPHAN] No orphaned groups found, returning early")
            return {
                "status": "ok",
                "message": "No orphaned channel groups found",
                "deleted_groups": [],
                "failed_groups": [],
            }

        # Filter to only the specified group IDs if provided
        groups_to_delete = orphaned_groups
        if group_ids is not None:
            logger.debug("[GROUPS-ORPHAN] Filtering to specified group IDs: %s", group_ids)
            groups_to_delete = [g for g in orphaned_groups if g["id"] in group_ids]
            logger.debug("[GROUPS-ORPHAN] After filtering: %s groups to delete", len(groups_to_delete))
            if not groups_to_delete:
                logger.debug("[GROUPS-ORPHAN] No matching groups to delete, returning early")
                return {
                    "status": "ok",
                    "message": "No matching orphaned groups to delete",
                    "deleted_groups": [],
                    "failed_groups": [],
                }

        # Delete each orphaned group
        logger.debug("[GROUPS-ORPHAN] Deleting %s orphaned groups...", len(groups_to_delete))
        deleted_groups = []
        failed_groups = []
        for orphan in groups_to_delete:
            group_id = orphan["id"]
            group_name = orphan["name"]
            try:
                logger.debug("[GROUPS-ORPHAN] Attempting to delete group %s (%s)...", group_id, group_name)
                await client.delete_channel_group(group_id)
                deleted_groups.append({"id": group_id, "name": group_name, "reason": orphan["reason"]})
                logger.info("[GROUPS-ORPHAN] Successfully deleted orphaned channel group: %s (%s) - %s", group_id, group_name, orphan['reason'])
            except Exception as group_err:
                failed_groups.append({"id": group_id, "name": group_name, "error": str(group_err)})
                logger.error("[GROUPS-ORPHAN] Failed to delete orphaned channel group %s (%s): %s", group_id, group_name, group_err)

        # Log to journal
        if deleted_groups:
            journal.log_entry(
                category="channel",
                action_type="cleanup",
                entity_id=None,
                entity_name="Orphaned Groups Cleanup",
                description=f"Deleted {len(deleted_groups)} orphaned channel groups",
                after_value={
                    "deleted_groups": deleted_groups,
                    "failed_groups": failed_groups,
                },
            )

        return {
            "status": "ok",
            "message": f"Deleted {len(deleted_groups)} orphaned channel groups",
            "deleted_groups": deleted_groups,
            "failed_groups": failed_groups,
        }
    except Exception as e:
        logger.exception("[GROUPS-ORPHAN] Failed to delete orphaned channel groups: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auto-created")
async def get_groups_with_auto_created_channels():
    """Find channel groups that contain auto_created channels.

    Returns groups with at least one channel that has auto_created=True.
    """
    logger.debug("[GROUPS] GET /channel-groups/auto-created")
    client = get_client()
    start = time.time()
    try:
        # Get all channel groups
        all_groups = await client.get_channel_groups()
        group_map = {g["id"]: g for g in all_groups}

        # Fetch all channels (paginated) and find auto_created ones
        auto_created_by_group: dict[int, list[dict]] = {}
        page = 1
        total_auto_created = 0

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                if channel.get("auto_created"):
                    total_auto_created += 1
                    group_id = channel.get("channel_group_id")
                    if group_id is not None:
                        if group_id not in auto_created_by_group:
                            auto_created_by_group[group_id] = []
                        auto_created_by_group[group_id].append({
                            "id": channel.get("id"),
                            "name": channel.get("name"),
                            "channel_number": channel.get("channel_number"),
                            "auto_created_by": channel.get("auto_created_by"),
                            "auto_created_by_name": channel.get("auto_created_by_name"),
                        })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        # Build result with group info
        groups_with_auto_created = []
        for group_id, channels in auto_created_by_group.items():
            group_info = group_map.get(group_id, {})
            groups_with_auto_created.append({
                "id": group_id,
                "name": group_info.get("name", f"Unknown Group {group_id}"),
                "auto_created_count": len(channels),
                "sample_channels": channels[:5],  # First 5 as samples
            })

        # Sort by name
        groups_with_auto_created.sort(key=lambda g: g["name"].lower())

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[GROUPS] Found %s groups with %s total auto_created channels in %.1fms", len(groups_with_auto_created), total_auto_created, elapsed_ms)
        return {
            "groups": groups_with_auto_created,
            "total_auto_created_channels": total_auto_created,
        }
    except Exception as e:
        logger.exception("[GROUPS] Failed to find groups with auto_created channels: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/with-streams")
async def get_channel_groups_with_streams():
    """Get all channel groups that have channels with streams.

    Returns groups that have at least one channel containing at least one stream.
    These are the groups that can be probed.
    """
    logger.debug("[GROUPS] GET /channel-groups/with-streams")
    client = get_client()
    start = time.time()
    try:
        # Get all channel groups first
        all_groups = await client.get_channel_groups()
        logger.debug("[GROUPS] Found %s total channel groups", len(all_groups))

        # Build a map of group_id -> group info for easy lookup
        group_map = {g["id"]: g for g in all_groups}

        # Track which groups have channels with streams
        groups_with_streams_ids = set()

        # Fetch all channels and check which groups have channels with streams
        page = 1
        total_channels = 0
        channels_with_streams = 0
        channels_without_streams = 0
        auto_created_count = 0
        sample_channel_groups = []  # Track first 5 for debugging
        sample_channels_no_streams = []  # Track channels without streams
        channels_by_group_id: dict = {}  # Track channel count per group for debugging
        sample_auto_created = []  # Track auto-created channels for debugging

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])
            total_channels += len(page_channels)

            for channel in page_channels:
                channel_group_id = channel.get("channel_group_id")
                channel_number = channel.get("channel_number")
                channel_name = channel.get("name")
                is_auto_created = channel.get("auto_created", False)

                # Track auto-created channels
                if is_auto_created:
                    auto_created_count += 1
                    if len(sample_auto_created) < 10:
                        group_name = group_map.get(channel_group_id, {}).get("name", "Unknown")
                        sample_auto_created.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "group_name": group_name,
                            "auto_created_by": channel.get("auto_created_by"),
                            "auto_created_by_name": channel.get("auto_created_by_name")
                        })

                # Track channels per group
                if channel_group_id is not None:
                    if channel_group_id not in channels_by_group_id:
                        channels_by_group_id[channel_group_id] = {"count": 0, "with_streams": 0, "samples": []}
                    channels_by_group_id[channel_group_id]["count"] += 1
                    if len(channels_by_group_id[channel_group_id]["samples"]) < 3:
                        channels_by_group_id[channel_group_id]["samples"].append(f"#{channel_number} {channel_name}")

                # Check if channel has any streams
                stream_ids = channel.get("streams", [])
                if stream_ids:  # Has at least one stream
                    channels_with_streams += 1
                    if channel_group_id is not None:
                        channels_by_group_id[channel_group_id]["with_streams"] += 1

                    # Collect samples for debugging - dump first channel completely
                    if len(sample_channel_groups) == 0:
                        logger.debug("[GROUPS] First channel with streams (FULL DATA): %s", channel)

                    if len(sample_channel_groups) < 5:
                        sample_channel_groups.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "channel_group_type": type(channel_group_id).__name__,
                            "stream_count": len(stream_ids)
                        })

                    # IMPORTANT: Check for not None instead of truthy to handle group ID 0
                    if channel_group_id is not None:
                        groups_with_streams_ids.add(channel_group_id)
                else:
                    # Track channels WITHOUT streams for debugging
                    channels_without_streams += 1
                    if len(sample_channels_no_streams) < 10:
                        sample_channels_no_streams.append({
                            "channel_id": channel.get("id"),
                            "channel_name": channel_name,
                            "channel_number": channel_number,
                            "channel_group_id": channel_group_id,
                            "streams_field": stream_ids,
                            "streams_field_type": type(stream_ids).__name__
                        })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        # Log samples for debugging
        if sample_channel_groups:
            logger.debug("[GROUPS] Sample channels with streams (first 5): %s", sample_channel_groups)

        # Log channels without streams
        if sample_channels_no_streams:
            logger.warning("[GROUPS] Found %s channels WITHOUT streams. Samples: %s", channels_without_streams, sample_channels_no_streams)

        # Log auto-created channels summary
        logger.debug("[GROUPS] Auto-created channels: %s out of %s total", auto_created_count, total_channels)
        if sample_auto_created:
            logger.debug("[GROUPS] Sample auto-created channels: %s", sample_auto_created)

        # Log groups that have channels but NO streams
        groups_with_channels_no_streams = []
        for gid, data in channels_by_group_id.items():
            if data["with_streams"] == 0 and data["count"] > 0:
                group_name = group_map.get(gid, {}).get("name", "Unknown")
                groups_with_channels_no_streams.append({
                    "group_id": gid,
                    "group_name": group_name,
                    "channel_count": data["count"],
                    "samples": data["samples"]
                })

        if groups_with_channels_no_streams:
            logger.warning("[GROUPS] Groups with channels but NO streams (%s): %s", len(groups_with_channels_no_streams), groups_with_channels_no_streams[:20])

        logger.debug("[GROUPS] Scanned %s channels, found %s with streams", total_channels, channels_with_streams)
        logger.debug("[GROUPS] Found %s groups with channels containing streams", len(groups_with_streams_ids))
        logger.debug("[GROUPS] Group IDs found: %s", sorted(list(groups_with_streams_ids)))

        # Log group names for groups with streams
        groups_with_streams_names = []
        for gid in sorted(groups_with_streams_ids):
            group_name = group_map.get(gid, {}).get("name", "Unknown")
            groups_with_streams_names.append(f"{gid}:{group_name}")
        logger.debug("[GROUPS] Groups with streams (id:name): %s", groups_with_streams_names)

        # Log any groups named "Entertainment" specifically
        entertainment_groups = [g for g in all_groups if "entertainment" in g.get("name", "").lower()]
        logger.debug("[GROUPS] Groups containing 'Entertainment' in name: %s", entertainment_groups)
        logger.debug("[GROUPS] Group IDs in group_map: %s", sorted(list(group_map.keys())))

        # Build the result list
        groups_with_streams = []
        not_in_map = []
        for group_id in groups_with_streams_ids:
            if group_id in group_map:
                group = group_map[group_id]
                groups_with_streams.append({
                    "id": group["id"],
                    "name": group["name"]
                })
            else:
                not_in_map.append(group_id)

        if not_in_map:
            logger.warning("[GROUPS] Found %s group IDs in channels but not in group_map: %s", len(not_in_map), not_in_map)

        # Sort by name for consistent display
        groups_with_streams.sort(key=lambda g: g["name"].lower())

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[GROUPS] Returning %s groups with streams in %.1fms", len(groups_with_streams), elapsed_ms)
        return {
            "groups": groups_with_streams,
            "total_groups": len(all_groups)
        }
    except Exception as e:
        logger.exception("[GROUPS] Failed to get channel groups with streams: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Parameterized routes — must come after all static routes
# ---------------------------------------------------------------------------

@router.delete("/{group_id}")
async def delete_channel_group(group_id: int):
    """Delete a channel group (hides M3U-synced groups instead)."""
    logger.debug("[GROUPS] DELETE /channel-groups/%s", group_id)
    client = get_client()
    try:
        # Check if this group has M3U sync settings
        start = time.time()
        m3u_settings = await client.get_all_m3u_group_settings()
        has_m3u_sync = group_id in m3u_settings

        if has_m3u_sync:
            # Hide the group instead of deleting to preserve M3U sync
            from models import HiddenChannelGroup

            # Get the group name before hiding
            groups = await client.get_channel_groups()
            group_name = next((g.get("name") for g in groups if g.get("id") == group_id), f"Group {group_id}")

            with get_session() as db:
                # Check if already hidden
                existing = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
                if not existing:
                    hidden_group = HiddenChannelGroup(group_id=group_id, group_name=group_name)
                    db.add(hidden_group)
                    db.commit()
                    logger.info("[GROUPS] Hidden channel group id=%s name=%s due to M3U sync settings", group_id, group_name)

            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[GROUPS] Hid channel group %s in %.1fms", group_id, elapsed_ms)
            return {"status": "hidden", "message": "Group hidden (M3U sync active)"}
        else:
            # No M3U sync, safe to delete
            await client.delete_channel_group(group_id)
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[GROUPS] Deleted channel group %s via API in %.1fms", group_id, elapsed_ms)
            logger.info("[GROUPS] Deleted channel group id=%s", group_id)
            return {"status": "deleted"}
    except Exception as e:
        logger.exception("[GROUPS] Failed to delete/hide channel group %s: %s", group_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{group_id}/restore")
async def restore_channel_group(group_id: int):
    """Restore a hidden channel group back to the visible list."""
    logger.debug("[GROUPS] POST /channel-groups/%s/restore", group_id)
    try:
        from models import HiddenChannelGroup

        with get_session() as db:
            hidden_group = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
            if hidden_group:
                db.delete(hidden_group)
                db.commit()
                logger.info("[GROUPS] Restored channel group id=%s name=%s", group_id, hidden_group.group_name)
                return {"status": "restored", "message": f"Group '{hidden_group.group_name}' restored"}
            else:
                raise HTTPException(status_code=404, detail="Group not found in hidden list")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[GROUPS] Failed to restore channel group %s: %s", group_id, e)
        raise HTTPException(status_code=500, detail=str(e))
