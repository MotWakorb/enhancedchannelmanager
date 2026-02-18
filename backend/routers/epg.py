"""
EPG router — Electronic Program Guide sources, data, grid, and LCN lookup endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import asyncio
import gzip
import io
import logging
import time
import xml.etree.ElementTree as ET
import zlib
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from alert_methods import send_alert
from dispatcharr_client import get_client
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/epg", tags=["EPG"])

# Polling configuration for EPG refresh background tasks
REFRESH_POLL_INTERVAL_SECONDS = 5
EPG_REFRESH_MAX_WAIT_SECONDS = 900  # 15 minutes for EPG (larger files)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LCNLookupItem(BaseModel):
    """Single item for LCN lookup."""
    tvg_id: str
    epg_source_id: int | None = None  # If provided, only search this EPG source


class BatchLCNRequest(BaseModel):
    """Request body for batch LCN lookup."""
    items: list[LCNLookupItem]


# ---------------------------------------------------------------------------
# EPG Sources CRUD
# ---------------------------------------------------------------------------

@router.get("/sources")
async def get_epg_sources():
    """List all EPG sources."""
    logger.debug("[EPG] GET /api/epg/sources")
    client = get_client()
    start = time.time()
    try:
        result = await client.get_epg_sources()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG] Fetched %d EPG sources in %.1fms", len(result), elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sources/{source_id}")
async def get_epg_source(source_id: int):
    """Get an EPG source by ID."""
    logger.debug("[EPG] GET /api/epg/sources/%s", source_id)
    client = get_client()
    start = time.time()
    try:
        result = await client.get_epg_source(source_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG] Fetched EPG source id=%s in %.1fms", source_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sources")
async def create_epg_source(request: Request):
    """Create an EPG source (including dummy sources)."""
    logger.debug("[EPG] POST /api/epg/sources")
    client = get_client()
    start = time.time()
    try:
        data = await request.json()
        result = await client.create_epg_source(data)
        elapsed_ms = (time.time() - start) * 1000

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", data.get("name", "Unknown")),
            description=f"Created EPG source '{result.get('name', data.get('name'))}'",
            after_value={"name": result.get("name"), "url": data.get("url")},
        )

        logger.info("[EPG] Created EPG source id=%s name='%s' in %.1fms", result.get("id"), result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/sources/{source_id}")
async def update_epg_source(source_id: int, request: Request):
    """Update an EPG source."""
    logger.debug("[EPG] PATCH /api/epg/sources/%s", source_id)
    client = get_client()
    start = time.time()
    try:
        # Get before state
        before_source = await client.get_epg_source(source_id)
        data = await request.json()
        result = await client.update_epg_source(source_id, data)
        elapsed_ms = (time.time() - start) * 1000

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="update",
            entity_id=source_id,
            entity_name=result.get("name", before_source.get("name", "Unknown")),
            description=f"Updated EPG source '{result.get('name', before_source.get('name'))}'",
            before_value={"name": before_source.get("name"), "enabled": before_source.get("enabled")},
            after_value=data,
        )

        logger.info("[EPG] Updated EPG source id=%s name='%s' in %.1fms", source_id, result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/sources/{source_id}")
async def delete_epg_source(source_id: int):
    """Delete an EPG source."""
    logger.debug("[EPG] DELETE /api/epg/sources/%s", source_id)
    client = get_client()
    start = time.time()
    try:
        # Get source info before deleting
        source = await client.get_epg_source(source_id)
        source_name = source.get("name", "Unknown")

        await client.delete_epg_source(source_id)
        elapsed_ms = (time.time() - start) * 1000

        # Log to journal
        journal.log_entry(
            category="epg",
            action_type="delete",
            entity_id=source_id,
            entity_name=source_name,
            description=f"Deleted EPG source '{source_name}'",
            before_value={"name": source_name},
        )

        logger.info("[EPG] Deleted EPG source id=%s name='%s' in %.1fms", source_id, source_name, elapsed_ms)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# EPG Refresh helpers
# ---------------------------------------------------------------------------

async def _poll_epg_refresh_completion(source_id: int, source_name: str, initial_updated):
    """
    Background task to poll Dispatcharr until EPG refresh completes.

    Polls every REFRESH_POLL_INTERVAL_SECONDS for up to EPG_REFRESH_MAX_WAIT_SECONDS.
    Sends success notification when updated_at changes, warning on timeout.
    Uses longer timeout than M3U since EPG files can be very large.
    """
    from datetime import datetime

    client = get_client()
    wait_start = datetime.utcnow()

    try:
        while True:
            elapsed = (datetime.utcnow() - wait_start).total_seconds()
            if elapsed >= EPG_REFRESH_MAX_WAIT_SECONDS:
                logger.warning("[EPG-REFRESH] Timeout waiting for '%s' refresh after %.0fs", source_name, elapsed)
                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"EPG refresh for '{source_name}' timed out after {int(elapsed)}s - refresh may still be in progress",
                    notification_type="warning",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name, "timeout": True},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return

            await asyncio.sleep(REFRESH_POLL_INTERVAL_SECONDS)

            try:
                current_source = await client.get_epg_source(source_id)
            except Exception as e:
                # Source may have been deleted during refresh
                logger.warning("[EPG-REFRESH] Could not fetch source %s during polling: %s", source_id, e)
                return

            current_updated = current_source.get("updated_at") or current_source.get("last_updated")

            if current_updated and current_updated != initial_updated:
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info("[EPG-REFRESH] '%s' refresh complete in %.1fs", source_name, wait_duration)

                journal.log_entry(
                    category="epg",
                    action_type="refresh",
                    entity_id=source_id,
                    entity_name=source_name,
                    description=f"Refreshed EPG source '{source_name}' in {wait_duration:.1f}s",
                )

                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"Successfully refreshed EPG source '{source_name}' in {wait_duration:.1f}s",
                    notification_type="success",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name, "duration": wait_duration},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return
            elif elapsed > 30 and not initial_updated:
                # After 30 seconds, assume complete if no timestamp field available
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info("[EPG-REFRESH] '%s' - assuming complete after %.0fs (no timestamp field)", source_name, wait_duration)

                journal.log_entry(
                    category="epg",
                    action_type="refresh",
                    entity_id=source_id,
                    entity_name=source_name,
                    description=f"Refreshed EPG source '{source_name}'",
                )

                await send_alert(
                    title=f"EPG Refresh: {source_name}",
                    message=f"EPG source '{source_name}' refresh completed",
                    notification_type="success",
                    source="EPG Refresh",
                    metadata={"source_id": source_id, "source_name": source_name},
                    alert_category="epg_refresh",
                    entity_id=source_id,
                )
                return

    except Exception as e:
        logger.exception("[EPG-REFRESH] Error polling for '%s' completion: %s", source_name, e)


@router.post("/sources/{source_id}/refresh")
async def refresh_epg_source(source_id: int):
    """Trigger refresh for a single EPG source.

    Triggers the refresh and spawns a background task to poll for completion.
    Success notification is sent only when refresh actually completes.
    """
    logger.debug("[EPG-REFRESH] POST /api/epg/sources/%s/refresh", source_id)
    client = get_client()
    try:
        # Get source info and capture initial state for polling
        start = time.time()
        source = await client.get_epg_source(source_id)
        source_name = source.get("name", "Unknown")
        initial_updated = source.get("updated_at") or source.get("last_updated")

        # Trigger the refresh (returns immediately, refresh happens in background)
        result = await client.refresh_epg_source(source_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG-REFRESH] Triggered refresh for source %s in %.1fms", source_id, elapsed_ms)

        # Spawn background task to poll for completion and send notification
        asyncio.create_task(
            _poll_epg_refresh_completion(source_id, source_name, initial_updated)
        )

        logger.info("[EPG-REFRESH] Triggered refresh for '%s', polling for completion in background", source_name)
        return result
    except Exception as e:
        # Send error notification for trigger failure
        try:
            await send_alert(
                title="EPG Refresh Failed",
                message=f"Failed to trigger EPG refresh for source (ID: {source_id}): {str(e)}",
                notification_type="error",
                source="EPG Refresh",
                metadata={"source_id": source_id, "error": str(e)},
                alert_category="epg_refresh",
                entity_id=source_id,
            )
        except Exception:
            pass  # Don't fail the request if notification fails
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import")
async def trigger_epg_import():
    """Trigger EPG data import."""
    logger.debug("[EPG] POST /api/epg/import")
    client = get_client()
    start = time.time()
    try:
        result = await client.trigger_epg_import()
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[EPG] Triggered EPG import in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# EPG Data
# ---------------------------------------------------------------------------

@router.get("/data")
async def get_epg_data(
    page: int = 1,
    page_size: int = 100,
    search: Optional[str] = None,
    epg_source: Optional[int] = None,
):
    """Search EPG data with pagination and filtering."""
    logger.debug("[EPG] GET /api/epg/data - page=%s page_size=%s search=%s epg_source=%s", page, page_size, search, epg_source)
    client = get_client()
    start = time.time()
    try:
        result = await client.get_epg_data(
            page=page,
            page_size=page_size,
            search=search,
            epg_source=epg_source,
        )
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG] Fetched EPG data in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/data/{data_id}")
async def get_epg_data_by_id(data_id: int):
    """Get an individual EPG data entry by ID."""
    logger.debug("[EPG] GET /api/epg/data/%s", data_id)
    client = get_client()
    start = time.time()
    try:
        result = await client.get_epg_data_by_id(data_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG] Fetched EPG data id=%s in %.1fms", data_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/grid")
async def get_epg_grid(start: Optional[str] = None, end: Optional[str] = None):
    """Get EPG grid (programs from previous hour + next 24 hours).

    Optionally accepts start and end datetime parameters in ISO format.
    Time filtering significantly reduces data size and prevents timeouts.
    """
    logger.debug("[EPG] GET /api/epg/grid - start=%s end=%s", start, end)
    client = get_client()
    start_time = time.time()
    try:
        result = await client.get_epg_grid(start=start, end=end)
        elapsed_ms = (time.time() - start_time) * 1000
        logger.debug("[EPG] Fetched EPG grid in %.1fms", elapsed_ms)
        return result
    except httpx.ReadTimeout:
        raise HTTPException(
            status_code=504,
            detail="EPG data request timed out. This usually happens with very large EPG datasets. Try reducing the time range or contact your Dispatcharr administrator to optimize EPG data size."
        )
    except httpx.HTTPStatusError as e:
        # Handle upstream 504 from Dispatcharr
        if e.response.status_code == 504:
            raise HTTPException(
                status_code=504,
                detail="Dispatcharr EPG service timed out. This usually happens with very large channel counts (~2000+). The time range has been reduced to help, but you may need to optimize your EPG sources or reduce the number of channels."
            )
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        logger.exception("[EPG] Error fetching EPG grid: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# LCN (Logical Channel Number) lookup
# ---------------------------------------------------------------------------

@router.get("/lcn")
async def get_epg_lcn_by_tvg_id(tvg_id: str):
    """Get LCN (Logical Channel Number) for a TVG-ID from EPG XML sources.

    Fetches EPG XML from source URLs and extracts the <lcn> value for the given tvg_id.
    Returns the first LCN found across all XMLTV sources.

    Args:
        tvg_id: The TVG-ID to search for (as a query parameter)
    """
    logger.debug("[EPG-LCN] GET /api/epg/lcn - tvg_id=%s", tvg_id)
    client = get_client()
    try:
        # Get all EPG sources
        start = time.time()
        sources = await client.get_epg_sources()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG-LCN] Fetched EPG sources in %.1fms", elapsed_ms)

        # Filter to XMLTV sources that have URLs
        xmltv_sources = [
            s for s in sources
            if s.get("source_type") == "xmltv" and s.get("url")
        ]

        if not xmltv_sources:
            raise HTTPException(status_code=404, detail="No XMLTV EPG sources found")

        # Fetch and parse each XML source looking for the tvg_id
        # For large files, use streaming decompression to only read channel metadata
        MAX_SMALL_FILE = 50 * 1024 * 1024  # 50MB - download fully
        MAX_STREAM_BYTES = 20 * 1024 * 1024  # 20MB - max to stream from large files

        async def parse_xml_for_lcn(content: bytes, source_name: str) -> dict | None:
            """Parse XML content looking for LCN matching tvg_id."""
            xml_stream = io.BytesIO(content)
            root = None
            for event, elem in ET.iterparse(xml_stream, events=["start", "end"]):
                if event == "start" and root is None:
                    root = elem
                if event == "end" and elem.tag == "channel":
                    channel_id = elem.get("id", "")
                    if channel_id == tvg_id:
                        lcn = None
                        for child in elem:
                            if child.tag == "lcn":
                                lcn = child.text
                                break
                        if lcn:
                            logger.info("[EPG-LCN] Found LCN %s for %s in %s", lcn, tvg_id, source_name)
                            return {"tvg_id": tvg_id, "lcn": lcn, "source": source_name}
                    if root is not None:
                        root.clear()
                if event == "end" and elem.tag == "programme":
                    break
            return None

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            for source in xmltv_sources:
                url = source.get("url")
                if not url:
                    continue

                try:
                    logger.debug("[EPG-LCN] Checking EPG XML from %s for LCN lookup...", url)

                    # Check file size first
                    head_response = await http_client.head(url)
                    content_length = head_response.headers.get('content-length')
                    file_size = int(content_length) if content_length else 0

                    if file_size == 0 or file_size <= MAX_SMALL_FILE:
                        # Small file - download fully
                        response = await http_client.get(url)
                        response.raise_for_status()
                        content = response.content
                        logger.debug("[EPG-LCN] Downloaded %s bytes from %s", len(content), url)

                        # Decompress if gzipped
                        if url.endswith('.gz') or response.headers.get('content-encoding') == 'gzip':
                            try:
                                content = gzip.decompress(content)
                                logger.debug("[EPG-LCN] Decompressed to %s bytes", len(content))
                            except gzip.BadGzipFile:
                                pass

                        result = await parse_xml_for_lcn(content, source.get("name"))
                        if result:
                            return result
                    else:
                        # Large file - stream download first portion and decompress incrementally
                        logger.debug("[EPG-LCN] Large file (%s bytes) - streaming first %sMB...", file_size, MAX_STREAM_BYTES//1024//1024)

                        if url.endswith('.gz'):
                            # For gzipped files, download partial and try to decompress
                            # Channel data is typically in first 1-2% of large EPG files
                            download_size = min(file_size, MAX_STREAM_BYTES)
                            headers = {"Range": f"bytes=0-{download_size}"}

                            # Try range request
                            response = await http_client.get(url, headers=headers)
                            partial_content = response.content
                            logger.debug("[EPG-LCN] Downloaded %s bytes (partial)", len(partial_content))

                            # Decompress with decompobj to handle truncated data
                            decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
                            try:
                                decompressed = decompressor.decompress(partial_content)
                                logger.debug("[EPG-LCN] Partially decompressed to %s bytes", len(decompressed))

                                # Try to parse what we have - look for channel data
                                # Add closing tag to make it parseable
                                xml_partial = decompressed
                                if b'<programme' in xml_partial:
                                    # Truncate at first programme to avoid XML parse errors
                                    idx = xml_partial.find(b'<programme')
                                    xml_partial = xml_partial[:idx] + b'</tv>'

                                result = await parse_xml_for_lcn(xml_partial, source.get("name"))
                                if result:
                                    return result
                            except Exception as e:
                                logger.warning("[EPG-LCN] Failed to decompress partial %s: %s", url, e)
                        else:
                            # Non-gzipped large file - just download first portion
                            headers = {"Range": f"bytes=0-{MAX_STREAM_BYTES}"}
                            response = await http_client.get(url, headers=headers)
                            content = response.content
                            logger.debug("[EPG-LCN] Downloaded %s bytes (partial)", len(content))

                            if b'<programme' in content:
                                idx = content.find(b'<programme')
                                content = content[:idx] + b'</tv>'

                            result = await parse_xml_for_lcn(content, source.get("name"))
                            if result:
                                return result

                except httpx.HTTPError as e:
                    logger.warning("[EPG-LCN] Failed to fetch EPG XML from %s: %s", url, e)
                    continue
                except ET.ParseError as e:
                    logger.warning("[EPG-LCN] Failed to parse EPG XML from %s: %s", url, e)
                    continue
                except Exception as e:
                    logger.warning("[EPG-LCN] Error processing EPG XML from %s: %s", url, e)
                    continue

        # Not found in any source
        raise HTTPException(
            status_code=404,
            detail=f"No LCN found for TVG-ID '{tvg_id}' in any EPG source"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[EPG-LCN] Error fetching LCN for %s: %s", tvg_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/lcn/batch")
async def get_epg_lcn_batch(request: BatchLCNRequest):
    """Get LCN (Logical Channel Number) for multiple TVG-IDs from EPG XML sources.

    Each item can specify an EPG source ID. If provided, only that source is searched.
    If not provided, all XMLTV sources are searched (fallback behavior).

    This is more efficient than calling the single endpoint multiple times
    because it fetches and parses each EPG XML source only once.

    Returns a dict mapping tvg_id -> {lcn, source} for found entries.
    """
    logger.debug("[EPG-LCN] POST /api/epg/lcn/batch - %d items", len(request.items))
    if not request.items:
        return {"results": {}}

    # Group items by EPG source
    # Map of epg_source_id -> set of tvg_ids to find in that source
    source_to_tvg_ids: dict[int | None, set[str]] = {}
    for item in request.items:
        if item.epg_source_id not in source_to_tvg_ids:
            source_to_tvg_ids[item.epg_source_id] = set()
        source_to_tvg_ids[item.epg_source_id].add(item.tvg_id)

    client = get_client()
    try:
        # Get all EPG sources
        start = time.time()
        all_sources = await client.get_epg_sources()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[EPG-LCN] Fetched EPG sources for batch in %.1fms", elapsed_ms)

        # Filter to XMLTV sources that have URLs
        all_xmltv_sources = [
            s for s in all_sources
            if s.get("source_type") == "xmltv" and s.get("url")
        ]

        if not all_xmltv_sources:
            return {"results": {}}

        results: dict[str, dict] = {}
        MAX_SMALL_FILE = 50 * 1024 * 1024  # 50MB
        MAX_STREAM_BYTES = 20 * 1024 * 1024  # 20MB

        def parse_xml_for_lcns(content: bytes, source_name: str, tvg_ids: set[str]) -> dict[str, dict]:
            """Parse XML content and extract LCN for all matching tvg_ids."""
            found: dict[str, dict] = {}
            xml_stream = io.BytesIO(content)
            root = None
            for event, elem in ET.iterparse(xml_stream, events=["start", "end"]):
                if event == "start" and root is None:
                    root = elem
                if event == "end" and elem.tag == "channel":
                    channel_id = elem.get("id", "")
                    if channel_id in tvg_ids and channel_id not in found:
                        lcn = None
                        for child in elem:
                            if child.tag == "lcn":
                                lcn = child.text
                                break
                        if lcn:
                            found[channel_id] = {"lcn": lcn, "source": source_name}
                    if root is not None:
                        root.clear()
                if event == "end" and elem.tag == "programme":
                    break
            return found

        logger.debug("[EPG-LCN] Batch LCN lookup for %s items across %s EPG source(s)", len(request.items), len(source_to_tvg_ids))

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            # Process each EPG source group
            for epg_source_id, tvg_ids_for_source in source_to_tvg_ids.items():
                # Determine which sources to search
                if epg_source_id is None:
                    # No EPG source specified - search all sources (fallback)
                    sources_to_search = all_xmltv_sources
                    logger.debug("[EPG-LCN] Searching all EPG sources for %s TVG-ID(s) with no EPG source", len(tvg_ids_for_source))
                else:
                    # Search only the specified EPG source
                    sources_to_search = [s for s in all_xmltv_sources if s.get("id") == epg_source_id]
                    if not sources_to_search:
                        logger.warning("[EPG-LCN] EPG source %s not found or not XMLTV", epg_source_id)
                        continue
                    logger.debug("[EPG-LCN] Searching EPG source %s for %s TVG-ID(s)", epg_source_id, len(tvg_ids_for_source))

                # Track what we still need to find for this source group
                remaining = tvg_ids_for_source.copy()

                for source in sources_to_search:
                    url = source.get("url")
                    if not url:
                        continue

                    # Stop early if all found for this source group
                    if not remaining:
                        break

                    try:
                        # Check file size
                        head_response = await http_client.head(url)
                        content_length = head_response.headers.get('content-length')
                        file_size = int(content_length) if content_length else 0

                        if file_size == 0 or file_size <= MAX_SMALL_FILE:
                            # Small file - download fully
                            response = await http_client.get(url)
                            response.raise_for_status()
                            content = response.content
                            logger.info("[EPG-LCN] Batch: Downloaded %s bytes from %s", len(content), url)

                            if url.endswith('.gz') or response.headers.get('content-encoding') == 'gzip':
                                try:
                                    content = gzip.decompress(content)
                                except gzip.BadGzipFile:
                                    pass

                            found = parse_xml_for_lcns(content, source.get("name"), remaining)
                            results.update(found)
                            remaining -= set(found.keys())
                            if found:
                                logger.info("[EPG-LCN] Batch: Found %s LCNs in %s", len(found), source.get('name'))
                        else:
                            # Large file - stream first portion
                            logger.info("[EPG-LCN] Batch: Large file (%s bytes) - streaming...", file_size)

                            if url.endswith('.gz'):
                                download_size = min(file_size, MAX_STREAM_BYTES)
                                headers = {"Range": f"bytes=0-{download_size}"}
                                response = await http_client.get(url, headers=headers)
                                partial_content = response.content

                                decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
                                try:
                                    decompressed = decompressor.decompress(partial_content)
                                    xml_partial = decompressed
                                    if b'<programme' in xml_partial:
                                        idx = xml_partial.find(b'<programme')
                                        xml_partial = xml_partial[:idx] + b'</tv>'

                                    found = parse_xml_for_lcns(xml_partial, source.get("name"), remaining)
                                    results.update(found)
                                    remaining -= set(found.keys())
                                    if found:
                                        logger.info("[EPG-LCN] Batch: Found %s LCNs in %s (partial)", len(found), source.get('name'))
                                except Exception as e:
                                    logger.warning("[EPG-LCN] Batch: Failed to decompress partial %s: %s", url, e)
                            else:
                                headers = {"Range": f"bytes=0-{MAX_STREAM_BYTES}"}
                                response = await http_client.get(url, headers=headers)
                                content = response.content

                                if b'<programme' in content:
                                    idx = content.find(b'<programme')
                                    content = content[:idx] + b'</tv>'

                                found = parse_xml_for_lcns(content, source.get("name"), remaining)
                                results.update(found)
                                remaining -= set(found.keys())
                                if found:
                                    logger.info("[EPG-LCN] Batch: Found %s LCNs in %s (partial)", len(found), source.get('name'))

                    except httpx.HTTPError as e:
                        logger.warning("[EPG-LCN] Batch: Failed to fetch %s: %s", url, e)
                        continue
                    except ET.ParseError as e:
                        logger.warning("[EPG-LCN] Batch: Failed to parse %s: %s", url, e)
                        continue
                    except Exception as e:
                        logger.warning("[EPG-LCN] Batch: Error processing %s: %s", url, e)
                        continue

        logger.info("[EPG-LCN] Batch LCN lookup complete: %s/%s found", len(results), len(request.items))
        return {"results": results}

    except Exception as e:
        logger.exception("[EPG-LCN] Batch LCN error: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
