"""
Channels router — channel CRUD, logos, CSV import/export, stream management,
number assignment, bulk-commit, and clear-auto-created endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import os
import re
import time
import uuid
from datetime import date
from typing import Optional, Literal, Union

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Response
from pydantic import BaseModel

from config import get_settings
from csv_handler import parse_csv, generate_csv, generate_template, CSVParseError
from database import get_session
from dispatcharr_client import get_client
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["Channels"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateChannelRequest(BaseModel):
    name: str
    channel_number: Optional[float] = None
    channel_group_id: Optional[int] = None
    logo_id: Optional[int] = None
    tvg_id: Optional[str] = None
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


class CreateLogoRequest(BaseModel):
    name: str
    url: str


class AddStreamRequest(BaseModel):
    stream_id: int


class RemoveStreamRequest(BaseModel):
    stream_id: int


class ReorderStreamsRequest(BaseModel):
    stream_ids: list[int]


class AssignNumbersRequest(BaseModel):
    channel_ids: list[int]
    starting_number: Optional[float] = None


class ClearAutoCreatedRequest(BaseModel):
    group_ids: list[int]


# Bulk commit operation types
class BulkUpdateChannelOp(BaseModel):
    type: Literal["updateChannel"] = "updateChannel"
    channelId: int
    data: dict


class BulkAddStreamOp(BaseModel):
    type: Literal["addStreamToChannel"] = "addStreamToChannel"
    channelId: int
    streamId: int


class BulkRemoveStreamOp(BaseModel):
    type: Literal["removeStreamFromChannel"] = "removeStreamFromChannel"
    channelId: int
    streamId: int


class BulkReorderStreamsOp(BaseModel):
    type: Literal["reorderChannelStreams"] = "reorderChannelStreams"
    channelId: int
    streamIds: list[int]


class BulkAssignNumbersOp(BaseModel):
    type: Literal["bulkAssignChannelNumbers"] = "bulkAssignChannelNumbers"
    channelIds: list[int]
    startingNumber: Optional[float] = None


class BulkCreateChannelOp(BaseModel):
    type: Literal["createChannel"] = "createChannel"
    tempId: int  # Negative temp ID from frontend
    name: str
    channelNumber: Optional[float] = None
    groupId: Optional[int] = None
    newGroupName: Optional[str] = None
    logoId: Optional[int] = None
    logoUrl: Optional[str] = None
    tvgId: Optional[str] = None
    tvcGuideStationId: Optional[str] = None  # Gracenote ID from M3U tvc-guide-stationid
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


class BulkDeleteChannelOp(BaseModel):
    type: Literal["deleteChannel"] = "deleteChannel"
    channelId: int


class BulkCreateGroupOp(BaseModel):
    type: Literal["createGroup"] = "createGroup"
    name: str


class BulkDeleteGroupOp(BaseModel):
    type: Literal["deleteChannelGroup"] = "deleteChannelGroup"
    groupId: int


class BulkRenameGroupOp(BaseModel):
    type: Literal["renameChannelGroup"] = "renameChannelGroup"
    groupId: int
    newName: str


# Union type for all bulk operations
BulkOperation = Union[
    BulkUpdateChannelOp,
    BulkAddStreamOp,
    BulkRemoveStreamOp,
    BulkReorderStreamsOp,
    BulkAssignNumbersOp,
    BulkCreateChannelOp,
    BulkDeleteChannelOp,
    BulkCreateGroupOp,
    BulkDeleteGroupOp,
    BulkRenameGroupOp,
]


class BulkCommitRequest(BaseModel):
    operations: list[BulkOperation]
    # Groups to create before processing operations (name -> temp group ID mapping)
    groupsToCreate: Optional[list[dict]] = None
    # If true, only validate without executing (returns validation issues)
    validateOnly: Optional[bool] = False
    # If true, continue processing even when individual operations fail
    continueOnError: Optional[bool] = False


class ValidationIssue(BaseModel):
    """Represents a validation issue found during pre-validation"""
    type: str  # 'missing_channel', 'missing_stream', 'invalid_operation', etc.
    severity: str  # 'error', 'warning'
    message: str
    operationIndex: Optional[int] = None
    channelId: Optional[int] = None
    channelName: Optional[str] = None
    streamId: Optional[int] = None
    streamName: Optional[str] = None


class BulkCommitResponse(BaseModel):
    success: bool
    operationsApplied: int
    operationsFailed: int
    errors: list[dict]
    # Map of temp channel IDs to real IDs
    tempIdMap: dict[int, int]
    # Map of group names to real IDs
    groupIdMap: dict[str, int]
    # Validation issues found during pre-validation
    validationIssues: Optional[list[dict]] = None
    # Whether validation passed (no errors, may have warnings)
    validationPassed: Optional[bool] = None


# ---------------------------------------------------------------------------
# Channel list / create
# ---------------------------------------------------------------------------

@router.get("")
async def get_channels(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    channel_group: Optional[int] = None,
):
    """List channels with pagination, search, and group filtering."""
    start_time = time.time()
    logger.debug(
        "[CHANNELS] Fetching channels - page=%s, page_size=%s, "
        "search=%s, group=%s",
        page, page_size, search, channel_group
    )
    client = get_client()
    try:
        fetch_start = time.time()
        result = await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
        fetch_time = (time.time() - fetch_start) * 1000
        total_time = (time.time() - start_time) * 1000
        result_count = len(result.get('results', []))
        total_count = result.get('count', 0)

        # Debug logging: count channels per group_id on first page of unfiltered requests
        if page == 1 and not search and not channel_group:
            channels = result.get('results', [])
            group_counts: dict = {}
            for ch in channels:
                group_id = ch.get('channel_group_id')
                group_name = ch.get('channel_group_name', 'Unknown')
                key = f"{group_id}:{group_name}"
                if key not in group_counts:
                    group_counts[key] = {'count': 0, 'sample_channels': []}
                group_counts[key]['count'] += 1
                # Keep first 3 sample channel names per group for debugging
                if len(group_counts[key]['sample_channels']) < 3:
                    group_counts[key]['sample_channels'].append(
                        f"#{ch.get('channel_number')} {ch.get('name', 'unnamed')}"
                    )

            logger.debug("[CHANNELS] Page 1 stats: %s channels returned, API total=%s", result_count, total_count)
            logger.debug("[CHANNELS] Channels per group_id (page 1 only):")
            for key, data in sorted(group_counts.items(), key=lambda x: -x[1]['count']):
                logger.debug("  %s: %s channels (samples: %s)", key, data['count'], data['sample_channels'])

        logger.debug(
            "[CHANNELS] Fetched %s channels (total=%s, page=%s) "
            "- fetch=%.1fms, total=%.1fms",
            result_count, total_count, page, fetch_time, total_time
        )
        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to retrieve channels: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("")
async def create_channel(request: CreateChannelRequest):
    """Create a new channel."""
    logger.debug("[CHANNELS] POST /channels - name=%s number=%s normalize=%s", request.name, request.channel_number, request.normalize)
    client = get_client()
    try:
        # Apply normalization if requested
        channel_name = request.name
        if request.normalize:
            try:
                from normalization_engine import get_normalization_engine
                with get_session() as db:
                    engine = get_normalization_engine(db)
                    norm_result = engine.normalize(request.name)
                    channel_name = norm_result.normalized
                    if channel_name != request.name:
                        logger.debug("[CHANNELS] Normalized channel name: '%s' -> '%s'", request.name, channel_name)
            except Exception as norm_err:
                logger.warning("[CHANNELS] Failed to normalize channel name '%s': %s", request.name, norm_err)
                # Continue with original name

        data = {"name": channel_name}
        if request.channel_number is not None:
            data["channel_number"] = request.channel_number
        if request.channel_group_id is not None:
            data["channel_group_id"] = request.channel_group_id
        if request.logo_id is not None:
            data["logo_id"] = request.logo_id
        if request.tvg_id is not None:
            data["tvg_id"] = request.tvg_id
        start = time.time()
        result = await client.create_channel(data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Created channel via API in %.1fms", elapsed_ms)
        logger.info("[CHANNELS] Created channel id=%s name=%s number=%s", result.get('id'), result.get('name'), result.get('channel_number'))

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", "Unknown"),
            description=f"Created channel '{result.get('name')}'" + (f" with number {result.get('channel_number')}" if result.get('channel_number') else ""),
            after_value={"channel_number": result.get("channel_number"), "name": result.get("name")},
        )

        return result
    except Exception as e:
        logger.exception("[CHANNELS] Channel creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Logos — MUST be defined before /api/channels/{channel_id} routes
# ---------------------------------------------------------------------------

@router.get("/logos")
async def get_logos(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
):
    """List logos with pagination and search."""
    logger.debug("[CHANNELS-LOGO] GET /channels/logos - page=%s search=%s", page, search)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_logos(page=page, page_size=page_size, search=search)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logos in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logos: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logos/{logo_id}")
async def get_logo(logo_id: int):
    """Get a single logo by ID."""
    logger.debug("[CHANNELS-LOGO] GET /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_logo(logo_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logo id=%s in %.1fms", logo_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logos")
async def create_logo(request: CreateLogoRequest):
    """Create a logo from a URL."""
    logger.debug("[CHANNELS-LOGO] POST /channels/logos - name=%s", request.name)
    client = get_client()
    try:
        start = time.time()
        result = await client.create_logo({"name": request.name, "url": request.url})
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Created logo id=%s name=%s in %.1fms", result.get('id'), result.get('name'), elapsed_ms)
        return result
    except Exception as e:
        error_str = str(e)
        # Check if this is a "logo already exists" error from Dispatcharr
        if "logo with this url already exists" in error_str.lower() or "400" in error_str:
            try:
                existing_logo = await client.find_logo_by_url(request.url)
                if existing_logo:
                    logger.info("[CHANNELS-LOGO] Found existing logo id=%s name=%s", existing_logo.get('id'), existing_logo.get('name'))
                    return existing_logo
                else:
                    logger.warning("[CHANNELS-LOGO] Logo exists but could not find it by URL: %s", request.url)
            except Exception as search_err:
                logger.error("[CHANNELS-LOGO] Error searching for existing logo: %s", search_err)
        logger.exception("[CHANNELS-LOGO] Logo creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logos/upload")
async def upload_logo(request: Request, file: UploadFile = File(...)):
    """Upload a logo image file directly to Dispatcharr."""
    logger.debug("[CHANNELS-LOGO] POST /channels/logos/upload - filename=%s", file.filename)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    contents = await file.read()
    name = os.path.splitext(file.filename or "logo")[0]
    client = get_client()
    try:
        start = time.time()
        result = await client.upload_logo_file(
            name=name,
            filename=file.filename or "logo.png",
            content=contents,
            content_type=file.content_type,
        )
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Uploaded logo id=%s name=%s in %.1fms", result.get('id'), name, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Logo upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/logos/{logo_id}")
async def update_logo(logo_id: int, data: dict):
    """Update a logo."""
    logger.debug("[CHANNELS-LOGO] PATCH /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.update_logo(logo_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Updated logo id=%s in %.1fms", logo_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to update logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/logos/{logo_id}")
async def delete_logo(logo_id: int):
    """Delete a logo."""
    logger.debug("[CHANNELS-LOGO] DELETE /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        await client.delete_logo(logo_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Deleted logo id=%s in %.1fms", logo_id, elapsed_ms)
        return {"success": True}
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to delete logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# CSV Import/Export — MUST be defined before /api/channels/{channel_id} routes
# ---------------------------------------------------------------------------

@router.get("/csv-template")
async def get_csv_template():
    """Download CSV template for channel import."""
    logger.debug("[CHANNELS-CSV] GET /channels/csv-template")
    template_content = generate_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=channel-import-template.csv"
        }
    )


@router.get("/export-csv")
async def export_channels_csv():
    """Export all channels to CSV format."""
    logger.debug("[CHANNELS-CSV] GET /channels/export-csv")
    client = get_client()
    try:
        # Fetch channel groups to build ID -> name lookup
        start = time.time()
        groups = await client.get_channel_groups()
        group_lookup = {g.get("id"): g.get("name", "") for g in groups}

        # Fetch all channels (handle pagination)
        all_channels = []
        page = 1
        page_size = 100
        while True:
            result = await client.get_channels(page=page, page_size=page_size)
            channels = result.get("results", [])
            all_channels.extend(channels)
            if not result.get("next"):
                break
            page += 1

        # Filter out auto-created channels and sort by channel number ascending
        manual_channels = [ch for ch in all_channels if not ch.get("auto_created", False)]
        manual_channels.sort(key=lambda ch: ch.get("channel_number", 0) or 0)

        # Collect all stream IDs from channels
        all_stream_ids = set()
        for ch in manual_channels:
            stream_ids = ch.get("streams", [])
            all_stream_ids.update(stream_ids)

        # Fetch stream details to get URLs (batch by 100)
        stream_url_lookup = {}
        stream_ids_list = list(all_stream_ids)
        for i in range(0, len(stream_ids_list), 100):
            batch = stream_ids_list[i:i+100]
            if batch:
                try:
                    streams = await client.get_streams_by_ids(batch)
                    for s in streams:
                        stream_url_lookup[s.get("id")] = s.get("url", "")
                except Exception as e:
                    logger.warning("[CHANNELS-CSV] Failed to fetch stream batch: %s", e)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-CSV] Fetched export data (%s channels, %s streams) in %.1fms", len(all_channels), len(stream_url_lookup), elapsed_ms)

        # Transform channel data for CSV export
        csv_channels = []
        for ch in manual_channels:
            group_id = ch.get("channel_group_id")
            group_name = group_lookup.get(group_id, "") if group_id else ""

            # Get stream URLs for this channel
            stream_ids = ch.get("streams", [])
            stream_urls = [stream_url_lookup.get(sid, "") for sid in stream_ids if stream_url_lookup.get(sid)]
            stream_urls_str = ";".join(stream_urls) if stream_urls else ""

            csv_channels.append({
                "channel_number": ch.get("channel_number"),
                "name": ch.get("name", ""),
                "group_name": group_name,
                "tvg_id": ch.get("tvg_id", ""),
                "gracenote_id": ch.get("tvc_guide_stationid", ""),
                "logo_url": ch.get("logo_url", ""),
                "stream_urls": stream_urls_str
            })

        csv_content = generate_csv(csv_channels)
        filename = f"channels-export-{date.today().isoformat()}.csv"

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    except Exception as e:
        logger.exception("[CHANNELS-CSV] CSV export failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import-csv")
async def import_channels_csv(file: UploadFile = File(...)):
    """Import channels from CSV file."""
    logger.debug("[CHANNELS-CSV] POST /channels/import-csv - filename=%s", file.filename)
    client = get_client()

    # Read and decode the file
    try:
        content = await file.read()
        csv_content = content.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    # Parse CSV
    try:
        rows, parse_errors = parse_csv(csv_content)
    except CSVParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows and not parse_errors:
        # Empty file or header only
        return {
            "success": True,
            "channels_created": 0,
            "groups_created": 0,
            "errors": [],
            "warnings": []
        }

    # Get existing channel groups for matching
    try:
        start = time.time()
        existing_groups = await client.get_channel_groups()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-CSV] Fetched channel groups in %.1fms", elapsed_ms)
        group_map = {g["name"].lower(): g for g in existing_groups}
    except Exception as e:
        logger.exception("[CHANNELS-CSV] Failed to fetch channel groups: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch channel groups: {e}")

    # Build URL -> stream ID lookup for stream linking
    stream_url_to_id = {}
    try:
        start = time.time()
        page = 1
        page_size = 500
        while True:
            result = await client.get_streams(page=page, page_size=page_size)
            streams = result.get("results", [])
            for s in streams:
                url = s.get("url", "")
                if url:
                    stream_url_to_id[url] = s.get("id")
            if not result.get("next"):
                break
            page += 1
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-CSV] Built stream URL lookup with %s streams in %.1fms", len(stream_url_to_id), elapsed_ms)
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to fetch streams for URL lookup: %s", e)

    # Build EPG tvg_id -> icon_url lookup for logo assignment
    epg_tvg_id_to_icon = {}
    epg_name_to_icon = {}
    try:
        start = time.time()
        epg_data = await client.get_epg_data()
        for entry in epg_data:
            tvg_id = entry.get("tvg_id", "")
            icon_url = entry.get("icon_url", "")
            name = entry.get("name", "")
            if tvg_id and icon_url:
                epg_tvg_id_to_icon[tvg_id.lower()] = icon_url
            if name and icon_url:
                # Normalize name for matching (lowercase, strip common suffixes)
                normalized_name = name.lower().strip()
                for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                    if normalized_name.endswith(suffix):
                        normalized_name = normalized_name[:-len(suffix)]
                epg_name_to_icon[normalized_name] = icon_url
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-CSV] Built EPG logo lookup with %s tvg_id entries and %s name entries in %.1fms", len(epg_tvg_id_to_icon), len(epg_name_to_icon), elapsed_ms)
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to fetch EPG data for logo lookup: %s", e)

    channels_created = 0
    groups_created = 0
    streams_linked = 0
    logos_from_epg = 0
    errors = parse_errors.copy()
    warnings = []

    # Process each valid row
    for i, row in enumerate(rows):
        row_num = i + 2  # Account for header row

        try:
            # Handle group creation/lookup
            group_id = None
            group_name = row.get("group_name", "").strip()
            if group_name:
                group_key = group_name.lower()
                if group_key in group_map:
                    group_id = group_map[group_key]["id"]
                else:
                    # Create new group
                    try:
                        new_group = await client.create_channel_group(group_name)
                        group_id = new_group["id"]
                        group_map[group_key] = new_group
                        groups_created += 1
                        logger.info("[CHANNELS-CSV] Created channel group: %s", group_name)
                    except Exception as ge:
                        warnings.append(f"Row {row_num}: Failed to create group '{group_name}': {ge}")

            # Build channel data
            channel_data = {
                "name": row["name"],
            }

            # Add optional fields
            channel_number = row.get("channel_number", "").strip()
            if channel_number:
                try:
                    channel_data["channel_number"] = float(channel_number)
                except ValueError:
                    pass  # Skip invalid numbers

            if group_id:
                channel_data["channel_group_id"] = group_id

            tvg_id = row.get("tvg_id", "").strip()
            if tvg_id:
                channel_data["tvg_id"] = tvg_id

            gracenote_id = row.get("gracenote_id", "").strip()
            if gracenote_id:
                channel_data["tvc_guide_stationid"] = gracenote_id

            logo_url = row.get("logo_url", "").strip()
            if logo_url:
                channel_data["logo_url"] = logo_url

            # Create the channel
            created_channel = await client.create_channel(channel_data)
            channels_created += 1

            # If no logo_url provided, try to get one from EPG data
            if not logo_url and created_channel:
                epg_icon_url = None
                # First try tvg_id match
                if tvg_id:
                    epg_icon_url = epg_tvg_id_to_icon.get(tvg_id.lower())
                # Fall back to name match
                if not epg_icon_url:
                    channel_name = row["name"].lower().strip()
                    # Try exact match first
                    epg_icon_url = epg_name_to_icon.get(channel_name)
                    # Try without HD/SD suffix
                    if not epg_icon_url:
                        for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                            if channel_name.endswith(suffix):
                                channel_name = channel_name[:-len(suffix)]
                                epg_icon_url = epg_name_to_icon.get(channel_name)
                                break

                if epg_icon_url:
                    try:
                        channel_id = created_channel.get("id")
                        channel_name_for_logo = row["name"]
                        # Find existing logo by URL or create new one
                        existing_logo = await client.find_logo_by_url(epg_icon_url)
                        if existing_logo:
                            logo_id = existing_logo["id"]
                            logger.debug("[CHANNELS-CSV] Row %s: Found existing logo ID %s for EPG icon", row_num, logo_id)
                        else:
                            new_logo = await client.create_logo({"name": channel_name_for_logo, "url": epg_icon_url})
                            logo_id = new_logo["id"]
                            logger.debug("[CHANNELS-CSV] Row %s: Created new logo ID %s for EPG icon", row_num, logo_id)
                        # Update channel with logo_id
                        await client.update_channel(channel_id, {"logo_id": logo_id})
                        logos_from_epg += 1
                        logger.debug("[CHANNELS-CSV] Row %s: Assigned EPG logo to channel '%s'", row_num, row['name'])
                    except Exception as le:
                        warnings.append(f"Row {row_num}: Failed to assign EPG logo: {le}")

            # Handle stream linking if stream_urls provided
            stream_urls_str = row.get("stream_urls", "").strip()
            if stream_urls_str and created_channel:
                stream_urls = [url.strip() for url in stream_urls_str.split(";") if url.strip()]
                stream_ids = []
                for url in stream_urls:
                    stream_id = stream_url_to_id.get(url)
                    if stream_id:
                        stream_ids.append(stream_id)
                    else:
                        warnings.append(f"Row {row_num}: Stream URL not found: {url[:50]}...")

                if stream_ids:
                    try:
                        channel_id = created_channel.get("id")
                        await client.update_channel(channel_id, {"streams": stream_ids})
                        streams_linked += len(stream_ids)
                    except Exception as se:
                        warnings.append(f"Row {row_num}: Failed to link streams: {se}")

        except Exception as e:
            errors.append({"row": row_num, "error": str(e)})

    # Log the import
    logger.info("[CHANNELS-CSV] Import completed: %s channels created, %s groups created, %s streams linked, %s logos from EPG, %s errors", channels_created, groups_created, streams_linked, logos_from_epg, len(errors))

    return {
        "success": len(errors) == 0,
        "channels_created": channels_created,
        "groups_created": groups_created,
        "streams_linked": streams_linked,
        "logos_from_epg": logos_from_epg,
        "errors": errors,
        "warnings": warnings
    }


@router.post("/preview-csv")
async def preview_csv(data: dict):
    """Preview CSV content and validate before import."""
    logger.debug("[CHANNELS-CSV] POST /channels/preview-csv")
    content = data.get("content", "")
    if not content:
        return {"rows": [], "errors": []}

    try:
        rows, errors = parse_csv(content)
        # Convert rows to list of dicts for JSON response
        return {
            "rows": rows,
            "errors": errors
        }
    except CSVParseError as e:
        return {
            "rows": [],
            "errors": [{"row": 1, "error": str(e)}]
        }


# ---------------------------------------------------------------------------
# Static bulk routes — MUST be defined before /api/channels/{channel_id}
# ---------------------------------------------------------------------------

@router.post("/assign-numbers")
async def assign_channel_numbers(request: AssignNumbersRequest):
    """Bulk assign channel numbers."""
    logger.debug("[CHANNELS] POST /channels/assign-numbers - %s channels starting_number=%s", len(request.channel_ids), request.starting_number)
    client = get_client()
    settings = get_settings()

    try:
        # Get current channel data for all affected channels (needed for journal and auto-rename)
        start = time.time()
        batch_id = str(uuid.uuid4())[:8]
        channels_before = {}
        name_updates = {}

        for idx, channel_id in enumerate(request.channel_ids):
            channel = await client.get_channel(channel_id)
            channels_before[channel_id] = {
                "name": channel.get("name", ""),
                "channel_number": channel.get("channel_number"),
            }

            # If auto-rename is enabled, calculate name updates
            if settings.auto_rename_channel_number and request.starting_number is not None:
                old_number = channel.get("channel_number")
                new_number = request.starting_number + idx
                channel_name = channel.get("name", "")

                if old_number is not None and old_number != new_number and channel_name:
                    # Check if channel name contains the old number
                    old_number_str = str(int(old_number) if old_number == int(old_number) else old_number)
                    new_number_str = str(int(new_number) if new_number == int(new_number) else new_number)
                    # Match the number as a standalone value (not part of a larger number)
                    pattern = re.compile(r'(^|[^0-9])' + re.escape(old_number_str) + r'([^0-9]|$)')
                    if pattern.search(channel_name):
                        new_name = pattern.sub(r'\g<1>' + new_number_str + r'\g<2>', channel_name)
                        if new_name != channel_name:
                            name_updates[channel_id] = new_name

        # Call the bulk assign API
        result = await client.assign_channel_numbers(
            request.channel_ids, request.starting_number
        )

        # Apply name updates if any
        for channel_id, new_name in name_updates.items():
            try:
                await client.update_channel(channel_id, {"name": new_name})
            except Exception as e:
                logger.warning("[CHANNELS] Failed to update name for channel %s: %s", channel_id, e)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Assigned numbers to %s channels in %.1fms", len(request.channel_ids), elapsed_ms)

        # Log individual journal entries for each channel
        for idx, channel_id in enumerate(request.channel_ids):
            before_data = channels_before.get(channel_id, {})
            old_number = before_data.get("channel_number")
            new_number = request.starting_number + idx
            channel_name = before_data.get("name", f"Channel {channel_id}")
            new_name = name_updates.get(channel_id, channel_name)

            journal.log_entry(
                category="channel",
                action_type="reorder",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Changed channel number from {old_number} to {new_number}",
                before_value={"channel_number": old_number, "name": channel_name},
                after_value={"channel_number": new_number, "name": new_name},
                batch_id=batch_id,
            )

        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to assign channel numbers: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/bulk-commit")
async def bulk_commit_operations(request: BulkCommitRequest):
    """
    Process multiple channel operations in a single request.

    This endpoint is optimized for bulk changes (1000+ operations) by:
    - Processing all operations in a single HTTP request
    - Tracking temp ID -> real ID mappings for newly created channels
    - Creating groups before processing channel operations that reference them
    - Pre-validating that referenced channels/streams exist

    Options:
    - validateOnly: If true, only validate without executing
    - continueOnError: If true, continue processing even when operations fail

    Returns a response with success status, ID mappings, and validation issues.
    """
    client = get_client()
    batch_id = str(uuid.uuid4())[:8]

    # Count operation types for logging
    op_counts = {}
    for op in request.operations:
        op_counts[op.type] = op_counts.get(op.type, 0) + 1
    op_summary = ", ".join(f"{count} {op_type}" for op_type, count in sorted(op_counts.items()))

    logger.debug("[CHANNELS-BULK] Starting bulk commit (batch=%s): %s operations (%s)", batch_id, len(request.operations), op_summary)
    logger.debug("[CHANNELS-BULK] Options: validateOnly=%s, continueOnError=%s", request.validateOnly, request.continueOnError)
    if request.groupsToCreate:
        logger.debug("[CHANNELS-BULK] Groups to create: %s", [g.get('name') for g in request.groupsToCreate])

    result = {
        "success": True,
        "operationsApplied": 0,
        "operationsFailed": 0,
        "errors": [],
        "tempIdMap": {},  # temp channel ID -> real ID
        "groupIdMap": {},  # group name -> real ID
        "validationIssues": [],
        "validationPassed": True,
    }

    # Helper to resolve temp IDs to real IDs
    def resolve_id(channel_id: int) -> int:
        return result["tempIdMap"].get(channel_id, channel_id)

    # Helper to resolve group ID (could be temp or real, or from new group name)
    def resolve_group_id(group_id: Optional[int], new_group_name: Optional[str]) -> Optional[int]:
        if new_group_name and new_group_name in result["groupIdMap"]:
            return result["groupIdMap"][new_group_name]
        return group_id

    try:
        # Phase 0: Pre-validation - check that referenced entities exist
        logger.debug("[CHANNELS-BULK] Phase 0: Starting pre-validation")

        # Collect all channel IDs that are referenced (not created) in operations
        referenced_channel_ids = set()
        referenced_stream_ids = set()
        channels_to_create = set()  # Temp IDs that will be created

        for idx, op in enumerate(request.operations):
            if op.type == "createChannel":
                # This creates a channel, track its temp ID
                channels_to_create.add(op.tempId)
            elif op.type in ("updateChannel", "deleteChannel"):
                if op.channelId >= 0:  # Only real IDs need validation
                    referenced_channel_ids.add(op.channelId)
            elif op.type == "addStreamToChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "removeStreamFromChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "reorderChannelStreams":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                for sid in op.streamIds:
                    referenced_stream_ids.add(sid)
            elif op.type == "bulkAssignChannelNumbers":
                for cid in op.channelIds:
                    if cid >= 0:
                        referenced_channel_ids.add(cid)

        # Fetch existing channels and streams to validate
        existing_channels = {}  # id -> channel dict
        existing_streams = {}   # id -> stream dict

        logger.debug("[CHANNELS-BULK] Referenced entities: %s channels, %s streams", len(referenced_channel_ids), len(referenced_stream_ids))
        logger.debug("[CHANNELS-BULK] Channels to create: %s (temp IDs: %s)", len(channels_to_create), sorted(channels_to_create))
        if referenced_channel_ids:
            # Log a sample of referenced channel IDs (first 20)
            sample_ids = sorted(referenced_channel_ids)[:20]
            logger.debug("[CHANNELS-BULK] Referenced channel IDs (sample): %s%s", sample_ids, '...' if len(referenced_channel_ids) > 20 else '')

        if referenced_channel_ids:
            try:
                logger.debug("[CHANNELS-BULK] Fetching existing channels for validation...")
                # Fetch all pages of channels to build lookup
                page = 1
                while True:
                    response = await client.get_channels(page=page, page_size=500)
                    for ch in response.get("results", []):
                        existing_channels[ch["id"]] = ch
                    if not response.get("next"):
                        break
                    page += 1
                logger.debug("[CHANNELS-BULK] Loaded %s existing channels", len(existing_channels))
                # Check which referenced channels don't exist
                missing_channels = referenced_channel_ids - set(existing_channels.keys())
                if missing_channels:
                    logger.warning("[CHANNELS-BULK] Missing channels detected: %s (%s total)", sorted(missing_channels), len(missing_channels))
                else:
                    logger.debug("[CHANNELS-BULK] All %s referenced channels exist", len(referenced_channel_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to fetch channels for validation: %s", e)

        if referenced_stream_ids:
            try:
                logger.debug("[CHANNELS-BULK] Fetching %s referenced streams for validation...", len(referenced_stream_ids))
                # Fetch only the specific streams that are referenced (not all streams)
                streams = await client.get_streams_by_ids(list(referenced_stream_ids))
                for s in streams:
                    existing_streams[s["id"]] = s
                logger.debug("[CHANNELS-BULK] Loaded %s of %s referenced streams", len(existing_streams), len(referenced_stream_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to fetch streams for validation: %s", e)

        # Validate each operation
        for idx, op in enumerate(request.operations):
            if op.type in ("updateChannel", "deleteChannel"):
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Channel {op.channelId} does not exist in Dispatcharr",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                    })
                    result["validationPassed"] = False

            elif op.type == "addStreamToChannel":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot add stream to channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False
                elif op.channelId >= 0:
                    ch_name = existing_channels[op.channelId].get("name", f"Channel {op.channelId}")
                    # Check stream exists
                    if op.streamId not in existing_streams:
                        result["validationIssues"].append({
                            "type": "missing_stream",
                            "severity": "error",
                            "message": f"Stream {op.streamId} does not exist",
                            "operationIndex": idx,
                            "channelId": op.channelId,
                            "channelName": ch_name,
                            "streamId": op.streamId,
                        })
                        result["validationPassed"] = False

            elif op.type == "removeStreamFromChannel":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot remove stream from channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False

            elif op.type == "reorderChannelStreams":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot reorder streams for channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                    })
                    result["validationPassed"] = False

            elif op.type == "bulkAssignChannelNumbers":
                for cid in op.channelIds:
                    if cid >= 0 and cid not in existing_channels:
                        result["validationIssues"].append({
                            "type": "missing_channel",
                            "severity": "error",
                            "message": f"Cannot assign number to channel {cid}: channel does not exist",
                            "operationIndex": idx,
                            "channelId": cid,
                        })
                        result["validationPassed"] = False

        # Log validation summary
        logger.debug("[CHANNELS-BULK] Validation complete: passed=%s, issues=%s", result['validationPassed'], len(result['validationIssues']))
        if result['validationIssues']:
            logger.warning("[CHANNELS-BULK] === VALIDATION ISSUES DETAIL ===")
            for i, issue in enumerate(result['validationIssues'][:10]):  # Show first 10
                op_idx = issue.get('operationIndex', '?')
                ch_id = issue.get('channelId', '?')
                stream_id = issue.get('streamId', '?')
                # Get the actual operation for more context
                if op_idx != '?' and op_idx < len(request.operations):
                    op = request.operations[op_idx]
                    logger.warning("[CHANNELS-BULK]   Issue %s: %s - %s", i+1, issue['type'], issue['message'])
                    logger.warning("[CHANNELS-BULK]     Operation[%s]: type=%s, channelId=%s, streamId=%s", op_idx, op.type, op.channelId, getattr(op, 'streamId', None))
                    if op.type == "updateChannel" and op.data:
                        logger.warning("[CHANNELS-BULK]     Update data: name=%s, number=%s", op.data.get('name'), op.data.get('channel_number'))
                else:
                    logger.warning("[CHANNELS-BULK]   Issue %s: %s - %s (channelId=%s, streamId=%s)", i+1, issue['type'], issue['message'], ch_id, stream_id)
            if len(result['validationIssues']) > 10:
                logger.warning("[CHANNELS-BULK]   ... and %s more issues", len(result['validationIssues']) - 10)
            logger.warning("[CHANNELS-BULK] === END VALIDATION ISSUES ===")

        # If validateOnly, return now without executing
        if request.validateOnly:
            logger.info("[CHANNELS-BULK] Validation only mode: %s issues found, returning without executing", len(result['validationIssues']))
            result["success"] = result["validationPassed"]
            return result

        # If validation failed and continueOnError is false, return without executing
        if not result["validationPassed"] and not request.continueOnError:
            logger.warning("[CHANNELS-BULK] Validation failed with %s issues, aborting (continueOnError=false)", len(result['validationIssues']))
            logger.warning("[CHANNELS-BULK] No operations will be executed. Total operations that would have been attempted: %s", len(request.operations))
            # Log a hint about the issue
            if result['validationIssues']:
                first_issue = result['validationIssues'][0]
                logger.warning("[CHANNELS-BULK] First issue: %s", first_issue.get('message', 'Unknown'))
                if first_issue.get('type') == 'missing_channel':
                    logger.warning("[CHANNELS-BULK] Hint: Channel %s may have been deleted from Dispatcharr. Try refreshing the page to sync.", first_issue.get('channelId'))
            result["success"] = False
            return result

        # Log if continuing despite validation issues
        if not result["validationPassed"] and request.continueOnError:
            logger.warning("[CHANNELS-BULK] Continuing despite %s validation issues (continueOnError=true)", len(result['validationIssues']))

        # Phase 1: Create groups first (if any)
        if request.groupsToCreate:
            logger.debug("[CHANNELS-BULK] Phase 1: Creating %s groups", len(request.groupsToCreate))
            for group_info in request.groupsToCreate:
                group_name = group_info.get("name")
                if not group_name:
                    logger.debug("[CHANNELS-BULK] Skipping group with no name")
                    continue
                try:
                    logger.debug("[CHANNELS-BULK] Creating group: '%s'", group_name)
                    # Try to create the group
                    new_group = await client.create_channel_group(group_name)
                    result["groupIdMap"][group_name] = new_group["id"]
                    logger.debug("[CHANNELS-BULK] Created group '%s' -> ID %s", group_name, new_group['id'])
                except Exception as e:
                    error_str = str(e)
                    # If group already exists, try to find it
                    if "400" in error_str or "already exists" in error_str.lower():
                        logger.debug("[CHANNELS-BULK] Group '%s' may already exist, searching...", group_name)
                        try:
                            groups = await client.get_channel_groups()
                            for g in groups:
                                if g.get("name") == group_name:
                                    result["groupIdMap"][group_name] = g["id"]
                                    logger.debug("[CHANNELS-BULK] Found existing group '%s' -> ID %s", group_name, g['id'])
                                    break
                        except Exception as find_err:
                            logger.debug("[CHANNELS-BULK] Failed to search for existing group: %s", find_err)
                    else:
                        # Non-duplicate error - fail the whole operation
                        logger.error("[CHANNELS-BULK] Failed to create group '%s': %s", group_name, e)
                        result["success"] = False
                        result["errors"].append({
                            "operationId": f"create-group-{group_name}",
                            "error": str(e)
                        })
                        return result
            logger.debug("[CHANNELS-BULK] Group creation complete: %s groups mapped", len(result['groupIdMap']))

        # Phase 2: Process operations sequentially
        logger.debug("[CHANNELS-BULK] Phase 2: Processing %s operations", len(request.operations))
        for idx, op in enumerate(request.operations):
            op_id = f"op-{idx}-{op.type}"
            try:
                if op.type == "updateChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] updateChannel: channel_id=%s, data=%s", idx+1, len(request.operations), channel_id, op.data)
                    await client.update_channel(channel_id, op.data)
                    result["operationsApplied"] += 1

                elif op.type == "addStreamToChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] addStreamToChannel: channel_id=%s, stream_id=%s", idx+1, len(request.operations), channel_id, op.streamId)
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId not in current_streams:
                        current_streams.append(op.streamId)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        logger.debug("[CHANNELS-BULK] Added stream %s to channel %s", op.streamId, channel_id)
                    else:
                        logger.debug("[CHANNELS-BULK] Stream %s already in channel %s, skipping", op.streamId, channel_id)
                    result["operationsApplied"] += 1

                elif op.type == "removeStreamFromChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] removeStreamFromChannel: channel_id=%s, stream_id=%s", idx+1, len(request.operations), channel_id, op.streamId)
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId in current_streams:
                        current_streams.remove(op.streamId)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        logger.debug("[CHANNELS-BULK] Removed stream %s from channel %s", op.streamId, channel_id)
                    else:
                        logger.debug("[CHANNELS-BULK] Stream %s not in channel %s, skipping", op.streamId, channel_id)
                    result["operationsApplied"] += 1

                elif op.type == "reorderChannelStreams":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] reorderChannelStreams: channel_id=%s, streams=%s", idx+1, len(request.operations), channel_id, op.streamIds)
                    await client.update_channel(channel_id, {"streams": op.streamIds})
                    result["operationsApplied"] += 1

                elif op.type == "bulkAssignChannelNumbers":
                    resolved_ids = [resolve_id(cid) for cid in op.channelIds]
                    logger.debug("[CHANNELS-BULK] [%s/%s] bulkAssignChannelNumbers: %s channels starting at %s", idx+1, len(request.operations), len(resolved_ids), op.startingNumber)
                    await client.assign_channel_numbers(resolved_ids, op.startingNumber)
                    result["operationsApplied"] += 1

                elif op.type == "createChannel":
                    logger.debug("[CHANNELS-BULK] [%s/%s] createChannel: name='%s', tempId=%s, groupId=%s, newGroupName=%s, normalize=%s", idx+1, len(request.operations), op.name, op.tempId, op.groupId, op.newGroupName, op.normalize)
                    # Resolve group ID
                    group_id = resolve_group_id(op.groupId, op.newGroupName)

                    # Apply normalization if requested
                    channel_name = op.name
                    if op.normalize:
                        try:
                            from normalization_engine import get_normalization_engine
                            with get_session() as db:
                                engine = get_normalization_engine(db)
                                norm_result = engine.normalize(op.name)
                                channel_name = norm_result.normalized
                                if channel_name != op.name:
                                    logger.debug("[CHANNELS-BULK] Normalized channel name: '%s' -> '%s'", op.name, channel_name)
                        except Exception as norm_err:
                            logger.warning("[CHANNELS-BULK] Failed to normalize channel name '%s': %s", op.name, norm_err)
                            # Continue with original name

                    # Handle logo - if logoUrl provided but no logoId, try to find/create logo
                    logo_id = op.logoId
                    if not logo_id and op.logoUrl:
                        try:
                            logger.debug("[CHANNELS-BULK] Looking for logo by URL for channel '%s'", op.name)
                            # Try to find existing logo by URL
                            existing_logo = await client.find_logo_by_url(op.logoUrl)
                            if existing_logo:
                                logo_id = existing_logo["id"]
                                logger.debug("[CHANNELS-BULK] Found existing logo ID %s", logo_id)
                            else:
                                # Create new logo
                                new_logo = await client.create_logo({"name": channel_name, "url": op.logoUrl})
                                logo_id = new_logo["id"]
                                logger.debug("[CHANNELS-BULK] Created new logo ID %s", logo_id)
                        except Exception as logo_err:
                            logger.warning("[CHANNELS-BULK] Failed to create/find logo for channel '%s': %s", channel_name, logo_err)
                            # Continue without logo

                    # Create the channel
                    channel_data = {"name": channel_name}
                    if op.channelNumber is not None:
                        channel_data["channel_number"] = op.channelNumber
                    if group_id is not None:
                        channel_data["channel_group_id"] = group_id
                    if logo_id is not None:
                        channel_data["logo_id"] = logo_id
                    if op.tvgId is not None:
                        channel_data["tvg_id"] = op.tvgId
                    if op.tvcGuideStationId is not None:
                        channel_data["tvc_guide_stationid"] = op.tvcGuideStationId

                    logger.debug("[CHANNELS-BULK] op.tvgId=%s, op.tvcGuideStationId=%s", op.tvgId, op.tvcGuideStationId)
                    logger.debug("[CHANNELS-BULK] Creating channel with data: %s", channel_data)
                    new_channel = await client.create_channel(channel_data)

                    # Track temp ID -> real ID mapping
                    if op.tempId < 0:
                        result["tempIdMap"][op.tempId] = new_channel["id"]

                    result["operationsApplied"] += 1
                    logger.debug("[CHANNELS-BULK] Created channel '%s' (temp: %s -> real: %s)", channel_name, op.tempId, new_channel['id'])

                elif op.type == "deleteChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] deleteChannel: channel_id=%s", idx+1, len(request.operations), channel_id)
                    await client.delete_channel(channel_id)
                    result["operationsApplied"] += 1
                    logger.debug("[CHANNELS-BULK] Deleted channel %s", channel_id)

                elif op.type == "createGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] createGroup: name='%s'", idx+1, len(request.operations), op.name)
                    # Groups should be created in Phase 1, but handle here if needed
                    if op.name not in result["groupIdMap"]:
                        new_group = await client.create_channel_group(op.name)
                        result["groupIdMap"][op.name] = new_group["id"]
                        logger.debug("[CHANNELS-BULK] Created group '%s' -> ID %s", op.name, new_group['id'])
                    else:
                        logger.debug("[CHANNELS-BULK] Group '%s' already exists with ID %s", op.name, result['groupIdMap'][op.name])
                    result["operationsApplied"] += 1

                elif op.type == "deleteChannelGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] deleteChannelGroup: groupId=%s", idx+1, len(request.operations), op.groupId)
                    await client.delete_channel_group(op.groupId)
                    result["operationsApplied"] += 1
                    logger.debug("[CHANNELS-BULK] Deleted group %s", op.groupId)

                elif op.type == "renameChannelGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] renameChannelGroup: groupId=%s, newName='%s'", idx+1, len(request.operations), op.groupId, op.newName)
                    await client.update_channel_group(op.groupId, {"name": op.newName})
                    result["operationsApplied"] += 1
                    logger.debug("[CHANNELS-BULK] Renamed group %s to '%s'", op.groupId, op.newName)

            except Exception as e:
                # Build detailed error info with channel/stream names
                error_details = {
                    "operationId": op_id,
                    "operationType": op.type,
                    "error": str(e),
                }

                # Add context based on operation type
                if hasattr(op, 'channelId'):
                    error_details["channelId"] = op.channelId
                    # Try to get channel name from our lookup
                    if op.channelId in existing_channels:
                        error_details["channelName"] = existing_channels[op.channelId].get("name", f"Channel {op.channelId}")
                    else:
                        error_details["channelName"] = f"Channel {op.channelId}"

                if hasattr(op, 'streamId'):
                    error_details["streamId"] = op.streamId
                    # Try to get stream name from our lookup
                    if op.streamId in existing_streams:
                        error_details["streamName"] = existing_streams[op.streamId].get("name", f"Stream {op.streamId}")
                    else:
                        error_details["streamName"] = f"Stream {op.streamId}"

                if hasattr(op, 'name'):
                    error_details["entityName"] = op.name

                # Log with detailed context
                channel_info = f" (channel: {error_details.get('channelName', 'N/A')})" if 'channelName' in error_details else ""
                stream_info = f" (stream: {error_details.get('streamName', 'N/A')})" if 'streamName' in error_details else ""
                logger.exception("[CHANNELS-BULK] Operation %s failed%s%s: %s", op_id, channel_info, stream_info, e)

                result["operationsFailed"] += 1
                result["errors"].append(error_details)

                # If continueOnError, keep processing; otherwise stop
                if not request.continueOnError:
                    logger.debug("[CHANNELS-BULK] Stopping due to error (continueOnError=false)")
                    result["success"] = False
                    break
                else:
                    logger.debug("[CHANNELS-BULK] Continuing despite error (continueOnError=true)")
                # If continuing, mark as partial failure but keep going
                # success will be determined at the end based on whether any ops succeeded

        # Determine final success status
        # If continueOnError was used, success means at least some operations succeeded
        if request.continueOnError:
            result["success"] = result["operationsFailed"] == 0 or result["operationsApplied"] > 0
        else:
            result["success"] = result["operationsFailed"] == 0

        # Log summary
        logger.debug("[CHANNELS-BULK] Phase 2 complete: %s applied, %s failed", result['operationsApplied'], result['operationsFailed'])
        logger.debug("[CHANNELS-BULK] ID mappings: %s channels, %s groups", len(result['tempIdMap']), len(result['groupIdMap']))

        # Log summary to journal
        journal.log_entry(
            category="channel",
            action_type="bulk_commit",
            entity_id=None,
            entity_name="Bulk Commit",
            description=f"Applied {result['operationsApplied']} operations in bulk commit" +
                        (f" ({result['operationsFailed']} failed)" if result["operationsFailed"] > 0 else ""),
            after_value={
                "operations_applied": result["operationsApplied"],
                "operations_failed": result["operationsFailed"],
                "channels_created": len(result["tempIdMap"]),
                "groups_created": len(result["groupIdMap"]),
                "validation_issues": len(result["validationIssues"]),
                "continue_on_error": request.continueOnError,
            },
            batch_id=batch_id,
        )

        logger.info("[CHANNELS-BULK] Completed (batch=%s): success=%s, applied=%s, failed=%s%s",
                   batch_id, result['success'], result['operationsApplied'], result['operationsFailed'],
                   (", validation_issues=%s" % len(result['validationIssues'])) if result["validationIssues"] else "")
        return result

    except Exception as e:
        logger.exception("[CHANNELS-BULK] Unexpected error (batch=%s): %s", batch_id, e)
        result["success"] = False
        result["errors"].append({
            "operationId": "bulk-commit",
            "error": str(e)
        })
        return result


@router.post("/clear-auto-created")
async def clear_auto_created_flag(request: ClearAutoCreatedRequest):
    """Clear the auto_created flag from all channels in the specified groups.

    This converts auto_created channels to manual channels by setting
    auto_created=False and auto_created_by=None.
    """
    logger.debug("[CHANNELS] POST /channels/clear-auto-created - group_ids=%s", request.group_ids)
    client = get_client()
    group_ids = set(request.group_ids)

    if not group_ids:
        raise HTTPException(status_code=400, detail="No group IDs provided")

    try:
        # Fetch all channels and find auto_created ones in the specified groups
        start = time.time()
        channels_to_update = []
        page = 1

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                if channel.get("auto_created") and channel.get("channel_group_id") in group_ids:
                    channels_to_update.append({
                        "id": channel.get("id"),
                        "name": channel.get("name"),
                        "channel_number": channel.get("channel_number"),
                        "channel_group_id": channel.get("channel_group_id"),
                    })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        if not channels_to_update:
            return {
                "status": "ok",
                "message": "No auto_created channels found in the specified groups",
                "updated_count": 0,
                "updated_channels": [],
                "failed_channels": [],
            }

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched channels for auto_created clearing in %.1fms", elapsed_ms)
        logger.info("[CHANNELS] Clearing auto_created flag from %s channels in groups %s", len(channels_to_update), group_ids)

        # Update each channel via Dispatcharr API
        start = time.time()
        updated_channels = []
        failed_channels = []

        for channel in channels_to_update:
            channel_id = channel["id"]
            try:
                await client.update_channel(channel_id, {
                    "auto_created": False,
                    "auto_created_by": None,
                })
                updated_channels.append(channel)
                logger.debug("[CHANNELS] Cleared auto_created flag from channel %s (%s)", channel_id, channel['name'])
            except Exception as update_err:
                failed_channels.append({**channel, "error": str(update_err)})
                logger.error("[CHANNELS] Failed to clear auto_created flag from channel %s: %s", channel_id, update_err)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Updated %s channels (cleared auto_created) in %.1fms", len(updated_channels), elapsed_ms)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="bulk_update",
            entity_id=None,
            entity_name="Clear Auto-Created Flag",
            description=f"Cleared auto_created flag from {len(updated_channels)} channels in {len(group_ids)} group(s)",
            after_value={
                "group_ids": list(group_ids),
                "updated_count": len(updated_channels),
                "failed_count": len(failed_channels),
            },
        )

        return {
            "status": "ok",
            "message": f"Cleared auto_created flag from {len(updated_channels)} channel(s)",
            "updated_count": len(updated_channels),
            "updated_channels": updated_channels[:20],  # Limit response size
            "failed_channels": failed_channels,
        }
    except Exception as e:
        logger.exception("[CHANNELS] Failed to clear auto_created flags: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Channel by ID routes — must come after all static routes
# ---------------------------------------------------------------------------

@router.get("/{channel_id}")
async def get_channel(channel_id: int):
    """Get channel details by ID."""
    logger.debug("[CHANNELS] GET /channels/%s", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched channel id=%s in %.1fms", channel_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to fetch channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{channel_id}/streams")
async def get_channel_streams(channel_id: int):
    """Get streams assigned to a channel."""
    logger.debug("[CHANNELS] GET /channels/%s/streams", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_streams(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched streams for channel id=%s in %.1fms", channel_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to fetch streams for channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/{channel_id}")
async def update_channel(channel_id: int, data: dict):
    """Update a channel."""
    logger.debug("[CHANNELS] PATCH /channels/%s - data=%s", channel_id, data)
    client = get_client()
    try:
        # Get before state for logging
        start = time.time()
        before_channel = await client.get_channel(channel_id)

        result = await client.update_channel(channel_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Updated channel %s via API in %.1fms", channel_id, elapsed_ms)

        # Determine what changed for description and build before/after values
        changes = []
        before_value = {}
        after_value = {}

        if "name" in data and data["name"] != before_channel.get("name"):
            changes.append(f"name to '{data['name']}'")
            before_value["name"] = before_channel.get("name")
            after_value["name"] = data["name"]

        if "channel_number" in data and data["channel_number"] != before_channel.get("channel_number"):
            changes.append(f"number to {data['channel_number']}")
            before_value["channel_number"] = before_channel.get("channel_number")
            after_value["channel_number"] = data["channel_number"]

        if "tvg_id" in data and data["tvg_id"] != before_channel.get("tvg_id"):
            old_tvg = before_channel.get("tvg_id")
            new_tvg = data["tvg_id"]
            if new_tvg:
                changes.append(f"EPG mapping to '{new_tvg}'")
            else:
                changes.append("cleared EPG mapping")
            before_value["tvg_id"] = old_tvg
            after_value["tvg_id"] = new_tvg

        if "logo_id" in data and data["logo_id"] != before_channel.get("logo_id"):
            old_logo = before_channel.get("logo_id")
            new_logo = data["logo_id"]
            if new_logo:
                changes.append("logo")
            else:
                changes.append("cleared logo")
            before_value["logo_id"] = old_logo
            after_value["logo_id"] = new_logo

        if changes:
            logger.info("[CHANNELS] Updated channel id=%s: %s", channel_id, ', '.join(changes))
            journal.log_entry(
                category="channel",
                action_type="update",
                entity_id=channel_id,
                entity_name=result.get("name", before_channel.get("name", "Unknown")),
                description=f"Updated channel: {', '.join(changes)}",
                before_value=before_value,
                after_value=after_value,
            )
        else:
            logger.debug("[CHANNELS] No changes detected for channel %s", channel_id)

        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to update channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int):
    """Delete a channel."""
    logger.debug("[CHANNELS] DELETE /channels/%s", channel_id)
    client = get_client()
    try:
        # Get channel info before deleting for logging
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")

        await client.delete_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Deleted channel %s via API in %.1fms", channel_id, elapsed_ms)
        logger.info("[CHANNELS] Deleted channel id=%s name=%s", channel_id, channel_name)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="delete",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Deleted channel '{channel_name}'",
            before_value={"name": channel_name, "channel_number": channel.get("channel_number")},
        )

        return {"success": True}
    except Exception as e:
        logger.exception("[CHANNELS] Failed to delete channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest):
    """Add a stream to a channel."""
    logger.debug("[CHANNELS] POST /channels/%s/add-stream - stream_id=%s", channel_id, request.stream_id)
    client = get_client()
    try:
        # Get current channel
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Add stream if not already present
        if request.stream_id not in current_streams:
            before_streams = list(current_streams)
            current_streams.append(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[CHANNELS] Added stream to channel %s via API in %.1fms", channel_id, elapsed_ms)
            logger.info("[CHANNELS] Added stream %s to channel id=%s name=%s", request.stream_id, channel_id, channel_name)

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_add",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Added stream to channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        logger.debug("[CHANNELS] Stream %s already in channel %s", request.stream_id, channel_id)
        return channel
    except Exception as e:
        logger.exception("[CHANNELS] Failed to add stream %s to channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest):
    """Remove a stream from a channel."""
    logger.debug("[CHANNELS] POST /channels/%s/remove-stream - stream_id=%s", channel_id, request.stream_id)
    client = get_client()
    try:
        # Get current channel
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Remove stream if present
        if request.stream_id in current_streams:
            before_streams = list(current_streams)
            current_streams.remove(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[CHANNELS] Removed stream from channel %s via API in %.1fms", channel_id, elapsed_ms)
            logger.info("[CHANNELS] Removed stream %s from channel id=%s name=%s", request.stream_id, channel_id, channel_name)

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_remove",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Removed stream from channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        logger.debug("[CHANNELS] Stream %s not in channel %s", request.stream_id, channel_id)
        return channel
    except Exception as e:
        logger.exception("[CHANNELS] Failed to remove stream %s from channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/reorder-streams")
async def reorder_channel_streams(channel_id: int, request: ReorderStreamsRequest):
    """Reorder streams within a channel."""
    logger.debug("[CHANNELS] POST /channels/%s/reorder-streams - stream_ids=%s", channel_id, request.stream_ids)
    client = get_client()
    try:
        # Get before state
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        before_streams = channel.get("streams", [])

        result = await client.update_channel(channel_id, {"streams": request.stream_ids})
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Reordered streams for channel %s via API in %.1fms", channel_id, elapsed_ms)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="stream_reorder",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Reordered streams in channel '{channel_name}'",
            before_value={"streams": before_streams},
            after_value={"streams": request.stream_ids},
        )

        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to reorder streams for channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")
