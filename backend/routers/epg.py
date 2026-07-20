"""
EPG router — Electronic Program Guide sources, data, grid, and LCN lookup endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import asyncio
import gzip
import io
import logging
import hashlib
import secrets
import time
import xml.etree.ElementTree as ET
import zlib
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from alert_methods import send_alert
from auth import RequireAdminIfEnabled
from auth import get_jwt_secret_key
from cache import get_cache
from config import validate_url_scheme
from dispatcharr_client import get_client, upstream_http_exception
from epg_matching import (
    _epg_source_id,
    batch_find_epg_matches,
    build_source_priority_order,
    find_shared_epg_links,
)
import journal
from services.epg_migration import (
    PreviewTokenError,
    create_preview_token,
    parse_xmltv_lcn_index,
    preview_migration,
    verify_preview_token,
)
from tasks.dbas_sync_client import _PinnedSSRFTransport
from security.ssrf import (
    SSRFError,
    check_redirect_depth,
    get_ssrf_mode,
    validate_redirect,
)

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


class GuideMigrationPreviewRequest(BaseModel):
    target_epg_source_id: int


class GuideMigrationApplyItem(BaseModel):
    channel_id: int
    current_epg_data_id: int
    target_epg_data_id: int
    current_source_id: int
    current_tvg_id: str
    lcn: str
    target_tvg_id: str


class GuideMigrationApplyRequest(BaseModel):
    target_epg_source_id: int
    preview_token: str
    items: list[GuideMigrationApplyItem]


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
    """Create an EPG source (including dummy sources).

    No /match cache invalidation here (bd-41pcv): match_channels_to_epg
    fetches the full EPG source list fresh on every call and rebuilds the
    priority signature from it before checking the cache, so a newly
    created source's id/priority is already reflected in the cache key on
    the very next request — there's no stale-response window to close.
    """
    logger.debug("[EPG] POST /api/epg/sources")
    client = get_client()
    start = time.time()
    try:
        data = await request.json()
        if data.get("url"):
            validate_url_scheme(data["url"], "EPG source URL")
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
    except HTTPException:
        raise
    except Exception as e:
        # Surface actionable Dispatcharr 4xx (e.g. bad/missing fields in the
        # request body) instead of masking it as a generic 500 (bd-1wq7z.22).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] Create EPG source rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[EPG] Failed to create EPG source: %s", e)
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
        if data.get("url"):
            validate_url_scheme(data["url"], "EPG source URL")
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

        # Bust cached /match responses (bd-41pcv): the cache key is derived
        # from channel/source IDs + priority ranks only, not source content —
        # an in-place edit (e.g. URL swap) keeps the same id, so a stale
        # pre-edit response would otherwise be served for up to the cache TTL.
        cache = get_cache()
        cache.invalidate_prefix("epg_match:")

        logger.info("[EPG] Updated EPG source id=%s name='%s' in %.1fms", source_id, result.get("name"), elapsed_ms)
        return result
    except HTTPException:
        raise
    except Exception as e:
        # A missing source id (or bad field) surfaces as an upstream 4xx — map it
        # to a clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] Update EPG source %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to update EPG source %s: %s", source_id, e)
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

        # Bust cached /match responses (bd-41pcv): a match response for this
        # source's id/priority signature must not outlive the source itself.
        cache = get_cache()
        cache.invalidate_prefix("epg_match:")

        logger.info("[EPG] Deleted EPG source id=%s name='%s' in %.1fms", source_id, source_name, elapsed_ms)
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        # A missing source id surfaces as an upstream 404 — return 404, not 500
        # (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] Delete EPG source %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to delete EPG source %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Schedules Direct (SD) — account lineup management + program posters
# ---------------------------------------------------------------------------
# These proxy Dispatcharr's SD endpoints. Dispatcharr authenticates to SD live
# on each call and is rate-limited by SD (lineup adds 6/24h). ECM is a thin
# proxy and must NOT add its own retry/polling — that would amplify SD calls.


class SDLineupRequest(BaseModel):
    """Body for adding/removing an SD lineup."""
    lineup: str


class SDLineupSearchRequest(BaseModel):
    """Body for searching SD headends/lineups by location."""
    country: str
    postalcode: str


@router.get("/sources/{source_id}/sd-lineups")
async def get_sd_lineups(source_id: int):
    """List the SD account's active lineups for a Schedules Direct source."""
    logger.debug("[EPG] GET /api/epg/sources/%s/sd-lineups", source_id)
    client = get_client()
    try:
        return await client.get_sd_lineups(source_id)
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] SD lineups list for %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to list SD lineups for %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sources/{source_id}/sd-lineups")
async def add_sd_lineup(source_id: int, request: SDLineupRequest):
    """Add an SD lineup to the account."""
    logger.debug("[EPG] POST /api/epg/sources/%s/sd-lineups lineup=%s", source_id, request.lineup)
    client = get_client()
    try:
        result = await client.add_sd_lineup(source_id, request.lineup)
        journal.log_entry(
            category="epg",
            action_type="update",
            entity_id=source_id,
            entity_name=request.lineup,
            description=f"Added Schedules Direct lineup '{request.lineup}'",
            after_value={"lineup": request.lineup},
        )
        logger.info("[EPG] Added SD lineup '%s' to source %s", request.lineup, source_id)
        return result
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] SD lineup add for %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to add SD lineup for %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/sources/{source_id}/sd-lineups")
async def delete_sd_lineup(source_id: int, request: SDLineupRequest):
    """Remove an SD lineup from the account."""
    logger.debug("[EPG] DELETE /api/epg/sources/%s/sd-lineups lineup=%s", source_id, request.lineup)
    client = get_client()
    try:
        result = await client.delete_sd_lineup(source_id, request.lineup)
        journal.log_entry(
            category="epg",
            action_type="update",
            entity_id=source_id,
            entity_name=request.lineup,
            description=f"Removed Schedules Direct lineup '{request.lineup}'",
            before_value={"lineup": request.lineup},
        )
        logger.info("[EPG] Removed SD lineup '%s' from source %s", request.lineup, source_id)
        return result
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] SD lineup remove for %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to remove SD lineup for %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sources/{source_id}/sd-lineups/search")
async def search_sd_lineups(source_id: int, request: SDLineupSearchRequest):
    """Search SD headends/lineups by country + postal code."""
    logger.debug(
        "[EPG] POST /api/epg/sources/%s/sd-lineups/search country=%s", source_id, request.country
    )
    client = get_client()
    try:
        return await client.search_sd_lineups(source_id, request.country, request.postalcode)
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] SD lineup search for %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG] Failed to search SD lineups for %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/programs/{program_id}/poster")
async def get_program_poster(program_id: int):
    """Proxy an SD program poster image (bytes + Content-Type) from Dispatcharr."""
    logger.debug("[EPG] GET /api/epg/programs/%s/poster", program_id)
    client = get_client()
    try:
        resp = await client.get_program_poster(program_id)
        media_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=resp.content, media_type=media_type)
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG] Program poster %s rejected by Dispatcharr: %s", program_id, e)
            raise mapped
        logger.exception("[EPG] Failed to fetch program poster %s: %s", program_id, e)
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

                # Bust cached /match responses (bd-41pcv): this is the point
                # where new EPG data has actually landed under the source's
                # unchanged id — the strongest case for staleness, since a
                # match run right after a refresh completes must not serve a
                # pre-refresh cached response.
                get_cache().invalidate_prefix("epg_match:")

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

                # Bust cached /match responses (bd-41pcv) — same reasoning as
                # the timestamp-changed branch above; this path is taken when
                # the source has no updated_at field to compare.
                get_cache().invalidate_prefix("epg_match:")

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
        # A missing source id surfaces as an upstream 404 — return 404, not 500
        # (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG-REFRESH] Refresh EPG source %s rejected by Dispatcharr: %s", source_id, e)
            raise mapped
        logger.exception("[EPG-REFRESH] Failed to refresh EPG source %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import")
async def trigger_epg_import():
    """Trigger EPG data import."""
    logger.debug("[EPG] POST /api/epg/import")
    client = get_client()
    start = time.time()
    try:
        result = await client.trigger_epg_import()
    except Exception as e:
        logger.error("[EPG] EPG import failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    elapsed_ms = (time.time() - start) * 1000
    logger.info("[EPG] Triggered EPG import in %.1fms", elapsed_ms)
    return result


# ---------------------------------------------------------------------------
# EPG Data
# ---------------------------------------------------------------------------

@router.get("/data")
async def get_epg_data(
    # Bounds enforced here (bead enhancedchannelmanager-g4z2h, systemic sibling
    # of 1a5mf): page<1 / page_size<1 were passed straight to the upstream
    # Dispatcharr client, which raised and surfaced as a 500. No caller
    # currently passes an explicit page_size (frontend always uses the
    # default) — upper bound follows the generous headroom pattern used by
    # sibling list endpoints (get_channels, get_logos, get_streams).
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=1000, description="Results per page"),
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
                        # Prefer <gnid> (modern XMLTV Gracenote station id); fall back
                        # to <lcn> for legacy EPGs. Iterate children once so document
                        # order doesn't matter — gnid always wins over lcn.
                        gnid = None
                        lcn = None
                        for child in elem:
                            if child.tag == "gnid" and gnid is None:
                                text = (child.text or "").strip()
                                if text:
                                    gnid = text
                            elif child.tag == "lcn" and lcn is None:
                                text = (child.text or "").strip()
                                if text:
                                    lcn = text
                        gracenote_id = gnid or lcn
                        if gracenote_id:
                            logger.info("[EPG-LCN] Found gracenote id %s for %s in %s", gracenote_id, tvg_id, source_name)
                            # Response key keeps the legacy name "lcn"; the value is the
                            # gracenote station id (from <gnid>, or <lcn> fallback).
                            return {"tvg_id": tvg_id, "lcn": gracenote_id, "source": source_name}
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
                                pass  # Not actually gzipped despite extension/header; use raw content

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
                        # Prefer <gnid> (modern XMLTV Gracenote station id); fall back
                        # to <lcn> for legacy EPGs. Iterate children once so document
                        # order doesn't matter — gnid always wins over lcn.
                        gnid = None
                        lcn = None
                        for child in elem:
                            if child.tag == "gnid" and gnid is None:
                                text = (child.text or "").strip()
                                if text:
                                    gnid = text
                            elif child.tag == "lcn" and lcn is None:
                                text = (child.text or "").strip()
                                if text:
                                    lcn = text
                        gracenote_id = gnid or lcn
                        if gracenote_id:
                            # Response key keeps the legacy name "lcn"; the value is the
                            # gracenote station id (from <gnid>, or <lcn> fallback).
                            found[channel_id] = {"lcn": gracenote_id, "source": source_name}
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
                                    pass  # Not actually gzipped despite extension/header; use raw content

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


# ---------------------------------------------------------------------------
# EPG Matching (v0.15.0 — ported from frontend)
# ---------------------------------------------------------------------------

class EPGMatchRequest(BaseModel):
    channel_ids: list[int] = []
    epg_source_ids: list[int] = []
    # DEPRECATED (v0.18.x): EPG source priority is now resolved server-side from
    # the EPG source records (single source of truth). Any value sent here is
    # ignored and logs a deprecation warning. Scheduled for removal in v0.19.0.
    source_order: list[int] = []


class EPGLinkRequest(BaseModel):
    """Link one chosen EPG candidate to a channel.

    Supply either ``epg_data_id`` (the EPG data row id — preferred, exact) or
    ``tvg_id`` (resolved server-side to its EPG data row). ``epg_data_id`` wins
    if both are given. Both are the fields the ``match`` endpoint already
    surfaces per candidate (``epg_id`` + ``tvg_id``), closing the
    match -> link -> set-logo chain.
    """
    epg_data_id: int | None = None
    tvg_id: str | None = None


# Shared by match_channels_to_epg and audit_epg_duplicates (bead vznut.6).
_CHANNELS_FETCH_PAGE_SIZE = 1000
_CHANNELS_FETCH_MAX_PAGES = 1000  # hard stop so a misbehaving upstream cannot loop forever


async def _fetch_all_channels(client) -> list[dict]:
    """Fetch every channel from Dispatcharr, paging past the API's page-size cap.

    Both ``match_channels_to_epg`` and ``audit_epg_duplicates`` previously
    fetched a single ``page_size=10000`` page, silently truncating fleets
    larger than 10000 channels -- ``total_channels`` then misreported the true
    fleet size (code-review nit #4, bead vznut.1 -> vznut.6). This walks every
    page using the same paged-scan shape as the stale-ids endpoint
    (routers/streams.py) and stream_stats.get_stale_streams
    (routers/stream_stats.py), with a hard page-count stop (precedent:
    routers/backup.py's _CHANNELS_MAX_PAGES) so a misbehaving upstream can't
    loop forever.
    """
    channels: list[dict] = []
    fetched = 0
    page = 1
    while page <= _CHANNELS_FETCH_MAX_PAGES:
        result = await client.get_channels(page=page, page_size=_CHANNELS_FETCH_PAGE_SIZE)
        page_channels = result.get("results", [])
        channels.extend(page_channels)
        fetched += len(page_channels)
        if fetched >= result.get("count", 0) or not page_channels:
            break
        page += 1
    else:
        logger.warning(
            "[EPG] Channel pagination safety stop at %d pages (%d channels "
            "fetched); fleet may exceed the scan ceiling",
            _CHANNELS_FETCH_MAX_PAGES, fetched,
        )
    return channels


_GUIDE_MIGRATION_MAX_CHANNELS = 1000
_GUIDE_MIGRATION_MAX_EPG_ROWS = 50000
_XMLTV_HEADER_MAX_DOWNLOAD = 25 * 1024 * 1024
_XMLTV_HEADER_MAX_DECOMPRESSED = 30 * 1024 * 1024
_GUIDE_MIGRATION_APPLY_LOCK = asyncio.Lock()


def _append_gzip_bounded(
    decompressor: Any,
    chunk: bytes,
    output: bytearray,
) -> None:
    pending = chunk
    while pending:
        remaining = _XMLTV_HEADER_MAX_DECOMPRESSED - len(output)
        if remaining <= 0:
            raise HTTPException(status_code=413, detail="XMLTV channel header is too large.")
        piece = decompressor.decompress(pending, remaining + 1)
        if len(piece) > remaining:
            raise HTTPException(status_code=413, detail="XMLTV channel header is too large.")
        output.extend(piece)
        pending = decompressor.unconsumed_tail
        if not pending:
            break


def _reject_unsafe_xml(content: bytes) -> None:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise HTTPException(
            status_code=422,
            detail="XMLTV DTDs and entity declarations are not supported.",
        )


async def _fetch_migration_channels(client) -> list[dict]:
    channels: list[dict] = []
    page = 1
    while len(channels) <= _GUIDE_MIGRATION_MAX_CHANNELS:
        result = await client.get_channels(
            page=page, page_size=_CHANNELS_FETCH_PAGE_SIZE
        )
        page_rows = result.get("results", [])
        remaining = _GUIDE_MIGRATION_MAX_CHANNELS + 1 - len(channels)
        channels.extend(page_rows[:remaining])
        if (
            len(channels) > _GUIDE_MIGRATION_MAX_CHANNELS
            or not page_rows
            or len(channels) >= result.get("count", 0)
        ):
            break
        page += 1
    return channels


def _migration_actor(admin: Any) -> str:
    if admin is None:
        return "auth-disabled"
    return f"{getattr(admin, 'id', 'unknown')}:{getattr(admin, 'username', 'unknown')}"


def _migration_issuer(secret: str) -> str:
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:16]
    return f"ecm:{fingerprint}:guide-migration"


async def _load_xmltv_migration_index(source: dict):
    """Download only the XMLTV channel header and return its LCN index."""
    url = source.get("url")
    if not url:
        raise HTTPException(
            status_code=400,
            detail=f"XMLTV source {source.get('id')} has no downloadable URL.",
        )
    compressed = url.lower().endswith(".gz")
    downloaded = 0
    output = bytearray()
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16) if compressed else None
    try:
        transport = _PinnedSSRFTransport(verify=True)
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=False, transport=transport
        ) as http_client:
            current_url = url
            depth = 0
            while True:
                async with http_client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(
                                status_code=502,
                                detail="XMLTV source returned an invalid redirect.",
                            )
                        depth += 1
                        check_redirect_depth(depth)
                        next_url = urljoin(current_url, location)
                        validate_redirect(current_url, next_url, get_ssrf_mode())
                        current_url = next_url
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "").lower() == "gzip":
                        compressed = True
                        decompressor = decompressor or zlib.decompressobj(
                            zlib.MAX_WBITS | 16
                        )
                    async for chunk in response.aiter_raw():
                        downloaded += len(chunk)
                        if downloaded > _XMLTV_HEADER_MAX_DOWNLOAD:
                            raise HTTPException(
                                status_code=413,
                                detail="XMLTV channel header download is too large.",
                            )
                        if compressed and decompressor is not None:
                            _append_gzip_bounded(decompressor, chunk, output)
                        else:
                            if len(output) + len(chunk) > _XMLTV_HEADER_MAX_DECOMPRESSED:
                                raise HTTPException(
                                    status_code=413,
                                    detail="XMLTV channel header is too large.",
                                )
                            output.extend(chunk)
                        _reject_unsafe_xml(output)
                        if b"<programme" in output:
                            break
                    if (
                        compressed
                        and decompressor is not None
                        and b"<programme" not in output
                    ):
                        remaining = _XMLTV_HEADER_MAX_DECOMPRESSED - len(output)
                        tail = decompressor.flush(remaining + 1)
                        if len(tail) > remaining:
                            raise HTTPException(
                                status_code=413,
                                detail="XMLTV channel header is too large.",
                            )
                        output.extend(tail)
                break
    except HTTPException:
        raise
    except SSRFError as exc:
        raise HTTPException(
            status_code=400,
            detail="XMLTV source URL is blocked by the outbound security policy.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "[EPG-MIGRATION] XMLTV source id=%s could not be read: %s",
            source.get("id"),
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Could not read XMLTV source {source.get('id')}.",
        ) from exc

    marker = output.find(b"<programme")
    if marker >= 0:
        output = output[:marker]
    if not output:
        raise HTTPException(status_code=422, detail="XMLTV source has no readable header.")
    # The bounded stream stops before the first programme; close the root so
    # ElementTree can parse the complete channel header.
    if not output.rstrip().endswith(b"</tv>"):
        output.extend(b"</tv>")
    try:
        index = parse_xmltv_lcn_index(bytes(output))
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"XMLTV source {source.get('id')} channel header is invalid.",
        ) from exc
    if not index.channel_to_lcn:
        raise HTTPException(
            status_code=422,
            detail=f"XMLTV source {source.get('id')} contains no LCN/Gracenote mappings.",
        )
    return index


@router.post("/migration/preview")
async def preview_guide_migration(
    request: GuideMigrationPreviewRequest,
    _admin=RequireAdminIfEnabled,
):
    """Preview an LCN-based guide migration without mutating channels.

    Plain ``RequireAdminIfEnabled`` is intentional: project auth policy treats
    the MCP service principal as admin-equivalent for ordinary channel
    management. Only settings/secret-rewrite surfaces require the human-admin
    variant; this endpoint neither exposes nor changes those fields.
    """
    client = get_client()
    try:
        sources = await client.get_epg_sources()
        source_by_id = {source.get("id"): source for source in sources}
        target = source_by_id.get(request.target_epg_source_id)
        if target is None or target.get("source_type") not in {
            "xmltv",
            "schedules_direct",
        }:
            raise HTTPException(
                status_code=400,
                detail="Choose an XMLTV or Schedules Direct target source.",
            )

        channels = await _fetch_migration_channels(client)
        if len(channels) > _GUIDE_MIGRATION_MAX_CHANNELS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Guide migration is limited to {_GUIDE_MIGRATION_MAX_CHANNELS} "
                    "channels per operation."
                ),
            )
        epg_data = await client.get_epg_data(
            max_results=_GUIDE_MIGRATION_MAX_EPG_ROWS + 1
        )
        if len(epg_data) > _GUIDE_MIGRATION_MAX_EPG_ROWS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Guide migration is limited to {_GUIDE_MIGRATION_MAX_EPG_ROWS} "
                    "EPG rows per preview."
                ),
            )

        epg_by_id = {row.get("id"): row for row in epg_data}
        needed_source_ids = {
            epg_by_id[channel.get("epg_data_id")].get("epg_source")
            for channel in channels
            if channel.get("epg_data_id") in epg_by_id
            and epg_by_id[channel.get("epg_data_id")].get("epg_source") is not None
        }
        needed_source_ids.add(request.target_epg_source_id)
        xmltv_indexes = {}
        for source_id in sorted(needed_source_ids):
            source = source_by_id.get(source_id)
            if source and source.get("source_type") == "xmltv":
                xmltv_indexes[source_id] = await _load_xmltv_migration_index(source)

        rows = preview_migration(
            channels=channels,
            epg_data=epg_data,
            sources=sources,
            target_source_id=request.target_epg_source_id,
            xmltv_indexes=xmltv_indexes,
        )
        ready = [row for row in rows if row["status"] == "ready"]
        counts = {
            status: sum(row["status"] == status for row in rows)
            for status in (
                "ready",
                "already_target",
                "unassigned",
                "missing_lcn",
                "missing_target",
                "ambiguous_target",
                "unsupported_origin",
            )
        }
        logger.info(
            "[EPG-MIGRATION] Preview target_source=%s channels=%d ready=%d",
            request.target_epg_source_id,
            len(rows),
            len(ready),
        )
        signing_secret = get_jwt_secret_key()
        return {
            "target_source_id": request.target_epg_source_id,
            "target_source_name": target.get("name"),
            "rows": rows,
            "counts": counts,
            "preview_token": create_preview_token(
                secret=signing_secret,
                issuer=_migration_issuer(signing_secret),
                actor=_migration_actor(_admin),
                target_source_id=request.target_epg_source_id,
                rows=ready,
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[EPG-MIGRATION] Preview failed: %s", exc)
        raise HTTPException(status_code=500, detail="Guide migration preview failed")


@router.post("/migration/apply")
async def apply_guide_migration(
    request: GuideMigrationApplyRequest,
    _admin=RequireAdminIfEnabled,
):
    """Apply only the signed, still-current assignments from a preview."""
    if not request.items:
        raise HTTPException(status_code=400, detail="No ready migrations were selected.")
    if len(request.items) > _GUIDE_MIGRATION_MAX_CHANNELS:
        raise HTTPException(status_code=400, detail="Too many migration items.")
    items = [item.model_dump() for item in request.items]
    secret = get_jwt_secret_key()
    try:
        verify_preview_token(
            token=request.preview_token,
            secret=secret,
            issuer=_migration_issuer(secret),
            actor=_migration_actor(_admin),
            target_source_id=request.target_epg_source_id,
            rows=items,
        )
    except PreviewTokenError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc} Preview again.",
        ) from exc

    client = get_client()
    await _GUIDE_MIGRATION_APPLY_LOCK.acquire()
    try:
        sources = await client.get_epg_sources()
        source_by_id = {source.get("id"): source for source in sources}
        target_source = source_by_id.get(request.target_epg_source_id)
        if target_source is None or target_source.get("source_type") not in {
            "xmltv",
            "schedules_direct",
        }:
            raise HTTPException(
                status_code=409,
                detail="Target EPG source changed or is no longer supported.",
            )
        needed_source_ids = {
            item.current_source_id for item in request.items
        } | {request.target_epg_source_id}
        xmltv_indexes = {}
        for source_id in sorted(needed_source_ids):
            source = source_by_id.get(source_id)
            if source and source.get("source_type") == "xmltv":
                xmltv_indexes[source_id] = await _load_xmltv_migration_index(source)

        epg_data = await client.get_epg_data(
            epg_source=request.target_epg_source_id,
            max_results=_GUIDE_MIGRATION_MAX_EPG_ROWS + 1,
        )
        if len(epg_data) > _GUIDE_MIGRATION_MAX_EPG_ROWS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Guide migration is limited to {_GUIDE_MIGRATION_MAX_EPG_ROWS} "
                    "target EPG rows."
                ),
            )
        target_by_tvg: dict[str, list[dict]] = {}
        for candidate in epg_data:
            target_by_tvg.setdefault(str(candidate.get("tvg_id") or ""), []).append(
                candidate
            )
        invalid_target_channels = set()
        for item in request.items:
            if target_source.get("source_type") == "xmltv":
                target_tvgs = xmltv_indexes[
                    request.target_epg_source_id
                ].lcn_to_channels.get(item.lcn, ())
            else:
                target_tvgs = (item.lcn,)
            live_candidates = [
                (candidate.get("id"), str(candidate.get("tvg_id") or ""))
                for tvg_id in target_tvgs
                for candidate in target_by_tvg.get(tvg_id, ())
            ]
            if live_candidates != [(item.target_epg_data_id, item.target_tvg_id)]:
                invalid_target_channels.add(item.channel_id)

        results = []
        updated = 0
        mutated = 0
        audit_failed = 0
        skipped = 0
        failed = 0
        batch_id = secrets.token_hex(16)
        for item in request.items:
            try:
                if item.channel_id in invalid_target_channels:
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "ambiguous_target",
                        }
                    )
                    skipped += 1
                    continue
                current_source = source_by_id.get(item.current_source_id)
                if (
                    current_source is None
                    or current_source.get("source_type")
                    not in {"xmltv", "schedules_direct"}
                ):
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "unsupported_origin",
                        }
                    )
                    skipped += 1
                    continue
                current_mapping_valid = (
                    xmltv_indexes[item.current_source_id].channel_to_lcn.get(
                        item.current_tvg_id
                    )
                    == item.lcn
                    if current_source.get("source_type") == "xmltv"
                    else item.current_tvg_id == item.lcn
                )
                target_mapping_valid = (
                    item.target_tvg_id
                    in xmltv_indexes[
                        request.target_epg_source_id
                    ].lcn_to_channels.get(item.lcn, ())
                    if target_source.get("source_type") == "xmltv"
                    else item.target_tvg_id == item.lcn
                )
                if not current_mapping_valid or not target_mapping_valid:
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "semantic_drift",
                        }
                    )
                    skipped += 1
                    continue
                current_epg = await client.get_epg_data_by_id(
                    item.current_epg_data_id
                )
                target_epg = await client.get_epg_data_by_id(
                    item.target_epg_data_id
                )
                if (
                    current_epg.get("epg_source") != item.current_source_id
                    or str(current_epg.get("tvg_id") or "") != item.current_tvg_id
                    or target_epg.get("epg_source")
                    != request.target_epg_source_id
                    or str(target_epg.get("tvg_id") or "") != item.target_tvg_id
                ):
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "semantic_drift",
                        }
                    )
                    skipped += 1
                    continue
                channel = await client.get_channel(item.channel_id)
                if channel.get("epg_data_id") != item.current_epg_data_id:
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "changed_since_preview",
                        }
                    )
                    skipped += 1
                    continue
                await client.update_channel(
                    item.channel_id, {"epg_data_id": item.target_epg_data_id}
                )
                mutated += 1
                try:
                    entry = journal.log_entry(
                        category="epg",
                        action_type="guide_migration",
                        entity_id=item.channel_id,
                        entity_name=channel.get("name"),
                        description=(
                            f"Migrated channel guide from EPG data "
                            f"{item.current_epg_data_id} to "
                            f"{item.target_epg_data_id}"
                        ),
                        before_value={"epg_data_id": item.current_epg_data_id},
                        after_value={"epg_data_id": item.target_epg_data_id},
                        batch_id=batch_id,
                    )
                except Exception:
                    logger.exception(
                        "[EPG-MIGRATION] Journal write raised after channel=%s "
                        "was already updated",
                        item.channel_id,
                    )
                    entry = None
                if entry is None:
                    audit_failed += 1
                    results.append(
                        {
                            "channel_id": item.channel_id,
                            "status": "updated_audit_failed",
                        }
                    )
                else:
                    results.append(
                        {"channel_id": item.channel_id, "status": "updated"}
                    )
                    updated += 1
            except Exception:
                logger.exception(
                    "[EPG-MIGRATION] Channel update failed channel=%s",
                    item.channel_id,
                )
                results.append({"channel_id": item.channel_id, "status": "failed"})
                failed += 1
        logger.info(
            "[EPG-MIGRATION] Apply target_source=%s mutated=%d updated=%d "
            "audit_failed=%d skipped=%d failed=%d batch=%s",
            request.target_epg_source_id,
            mutated,
            updated,
            audit_failed,
            skipped,
            failed,
            batch_id,
        )
        return {
            "mutated": mutated,
            "updated": updated,
            "audit_failed": audit_failed,
            "skipped": skipped,
            "failed": failed,
            "results": results,
            "batch_id": batch_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[EPG-MIGRATION] Apply failed: %s", exc)
        raise HTTPException(status_code=500, detail="Guide migration failed")
    finally:
        _GUIDE_MIGRATION_APPLY_LOCK.release()


@router.post("/match")
async def match_channels_to_epg(request: EPGMatchRequest):
    """Batch match channels to EPG data with confidence scoring.

    Accepts channel IDs and optional EPG source filter/ordering.
    Returns pre-categorized results: exact, multiple, and none.
    Cached per unique set of channel IDs + EPG source IDs.
    """
    start = time.time()
    logger.info(
        "[EPG-MATCH] POST /match - channels=%d, epg_sources=%d",
        len(request.channel_ids), len(request.epg_source_ids),
    )

    if request.source_order:
        logger.warning(
            "[EPG-MATCH] Ignoring deprecated client-sent source_order "
            "(%d entries); EPG source priority is resolved server-side. "
            "This field is scheduled for removal in v0.19.0.",
            len(request.source_order),
        )

    client = get_client()
    cache = get_cache()

    # Resolve EPG source priority server-side (single source of truth). Sorting
    # by priority desc -> rank 0=best. Built before the cache check so the cache
    # key reflects the current priority ordering (a reorder must bust the cache).
    try:
        epg_sources = await client.get_epg_sources()
    except Exception as e:
        logger.warning("[EPG-MATCH] Failed to fetch EPG sources for priority: %s", e)
        epg_sources = []
    source_order = build_source_priority_order(epg_sources)

    # Build cache key from sorted IDs + the priority signature
    ch_key = ",".join(str(i) for i in sorted(request.channel_ids))
    src_key = ",".join(str(i) for i in sorted(request.epg_source_ids))
    prio_key = ",".join(
        f"{sid}:{rank}" for sid, rank in sorted(source_order.items())
    )
    cache_key = f"epg_match:{hash(ch_key)}:{hash(src_key)}:{hash(prio_key)}"

    cached = cache.get(cache_key)
    if cached is not None:
        elapsed = (time.time() - start) * 1000
        logger.info("[EPG-MATCH] Cache HIT in %.1fms", elapsed)
        return cached

    try:
        # Fetch channels (paginated -- bead vznut.6, see _fetch_all_channels)
        all_channels = await _fetch_all_channels(client)

        # Filter to requested channel IDs (if specified)
        if request.channel_ids:
            channel_id_set = set(request.channel_ids)
            channels = [ch for ch in all_channels if ch.get("id") in channel_id_set]
        else:
            channels = all_channels

        # Fetch all streams (for country detection)
        streams: list[dict] = []
        page = 1
        while True:
            result = await client.get_streams(page=page, page_size=500)
            streams.extend(result.get("results", []))
            if not result.get("next"):
                break
            page += 1

        # Fetch EPG data (optionally filtered by source)
        # get_epg_data returns a flat list (handles pagination internally)
        epg_data: list[dict] = []
        if request.epg_source_ids:
            for src_id in request.epg_source_ids:
                src_data = await client.get_epg_data(epg_source=src_id)
                epg_data.extend(src_data)
            # Defense-in-depth (m4hp1): the per-source fetch relies on
            # Dispatcharr honoring the ``epg_source`` query param, but nothing
            # downstream enforces it. If Dispatcharr ignores the filter (some
            # versions do), candidates leak in from EVERY source. Enforce the
            # user's selection authoritatively in ECM by dropping any entry
            # whose source id is not one of the selected ids.
            allowed_source_ids = set(request.epg_source_ids)
            before = len(epg_data)
            epg_data = [
                e for e in epg_data
                if _epg_source_id(e.get("epg_source")) in allowed_source_ids
            ]
            dropped = before - len(epg_data)
            if dropped:
                logger.warning(
                    "[EPG-MATCH] Dropped %d EPG entries from non-selected "
                    "sources (selected=%s); Dispatcharr did not honor the "
                    "epg_source filter",
                    dropped, sorted(allowed_source_ids),
                )
        else:
            epg_data = await client.get_epg_data()

        fetch_elapsed = (time.time() - start) * 1000
        logger.info(
            "[EPG-MATCH] Fetched %d channels, %d streams, %d EPG entries in %.1fms",
            len(channels), len(streams), len(epg_data), fetch_elapsed,
        )

        # Run matching through ECM's ONE shared NormalizationEngine (bd-xxzxe)
        # so EPG matching strips channel-number noise (and timezone, in matching
        # mode) consistently with the rest of the app. Tag-group lookups are
        # cached on the engine + a module-level cache, so extract_core_name only
        # warms once per batch, not per EPG entry.
        match_start = time.time()
        from database import get_session
        from normalization_engine import NormalizationEngine

        db = get_session()
        try:
            engine = NormalizationEngine(db)
            results = batch_find_epg_matches(
                channels=channels,
                all_streams=streams,
                epg_data=epg_data,
                source_order=source_order or None,
                engine=engine,
            )
        finally:
            db.close()
        match_elapsed = (time.time() - match_start) * 1000

        # Serialize results into pre-categorized buckets
        exact = []
        multiple = []
        none_matches = []

        for r in results:
            matches_list = [
                {
                    "epg_id": ms.epg_id,
                    "epg_name": ms.epg_name,
                    "tvg_id": ms.tvg_id,
                    "epg_source": ms.epg_source,
                    "confidence": ms.confidence,
                    "match_type": ms.match_type,
                }
                for ms in r.matches[:10]  # Limit to top 10
            ]
            best_score = r.matches[0].confidence if r.matches else 0

            # Determine status
            if len(r.matches) == 0:
                status = "none"
            elif len(r.matches) == 1:
                status = "exact"
            else:
                status = "multiple"

            entry = {
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "detected_country": r.detected_country,
                "status": status,
                "best_score": best_score,
                "matches": matches_list,
            }
            if status == "exact":
                exact.append(entry)
            elif status == "multiple":
                multiple.append(entry)
            else:
                none_matches.append(entry)

        # Embed a read-only "shared EPG links" audit over the matched scope
        # (bead vznut.1). Surfaces channels already linked to the SAME
        # epg_data_id — the West-shares-East fingerprint — right where the
        # operator is re-matching. The full-fleet audit lives at
        # GET /api/epg/audit-duplicates.
        shared_links = find_shared_epg_links(channels, epg_data)

        response = {
            "exact": exact,
            "multiple": multiple,
            "none": none_matches,
            "shared_epg_links": shared_links,
            "summary": {
                "total_channels": len(channels),
                "exact_count": len(exact),
                "multiple_count": len(multiple),
                "none_count": len(none_matches),
                "match_time_ms": round(match_elapsed, 1),
                "shared_link_groups": shared_links["summary"]["shared_link_groups"],
            },
        }

        cache.set(cache_key, response)

        total_elapsed = (time.time() - start) * 1000
        logger.info(
            "[EPG-MATCH] Completed: exact=%d, multiple=%d, none=%d "
            "- match=%.1fms, total=%.1fms",
            len(exact), len(multiple), len(none_matches),
            match_elapsed, total_elapsed,
        )
        return response

    except Exception as e:
        logger.exception("[EPG-MATCH] Failed: %s", e)
        raise HTTPException(status_code=500, detail="EPG matching failed")


@router.get("/audit-duplicates")
async def audit_epg_duplicates():
    """Read-only audit: list every set of channels sharing one ``epg_data_id``.

    This is the detector for the West-shares-East linkage bug (bead vznut):
    two or more channels silently linked to the *same* Dispatcharr EPG row, so a
    West feed shows its East counterpart's schedule. ``epg_data_id`` lives on the
    live Dispatcharr channel record (NOT ECM's journal.db), so this is a pure,
    non-mutating aggregation over the channel list — it changes nothing.

    Channels with a NULL / unlinked ``epg_data_id`` are excluded (they are
    unlinked, not mis-linked). Any group of size >= 2 is reported. Output is
    deterministic (sorted by channel id) so it doubles as a repeatable oracle.
    """
    start = time.time()
    logger.info("[EPG-AUDIT] GET /audit-duplicates")
    client = get_client()
    try:
        # Paginated fetch -- bead vznut.6, see _fetch_all_channels.
        channels = await _fetch_all_channels(client)

        # EPG rows are fetched only to enrich each group with the shared row's
        # name / tvg_id / source. A failure here degrades to linkage-only output
        # rather than failing the whole audit.
        try:
            epg_data = await client.get_epg_data()
        except Exception as e:
            logger.warning(
                "[EPG-AUDIT] Could not fetch EPG data for enrichment: %s", e
            )
            epg_data = []

        result = find_shared_epg_links(channels, epg_data)
        elapsed = (time.time() - start) * 1000
        logger.info(
            "[EPG-AUDIT] Completed: %d shared-link group(s) over %d channel(s) "
            "in %.1fms",
            result["summary"]["shared_link_groups"],
            result["summary"]["total_channels"],
            elapsed,
        )
        return result

    except Exception as e:
        logger.exception("[EPG-AUDIT] Failed: %s", e)
        raise HTTPException(status_code=500, detail="EPG duplicate audit failed")


@router.post("/channels/{channel_id}/link")
async def link_channel_to_epg(channel_id: int, request: EPGLinkRequest):
    """Link a channel to a chosen EPG candidate (sets its ``epg_data_id``).

    The ``match`` endpoint reports ``multiple`` candidates per channel but never
    establishes the EPG association — only an exact match would. This endpoint
    closes that seam: given the operator's chosen candidate (by ``epg_data_id``
    or ``tvg_id``, both surfaced by ``match``), it sets the channel's
    ``epg_data_id`` via the *same* ``update_channel`` PATCH the merge path uses,
    so a downstream "Set Logo from EPG" can then resolve the linked entry.

    Returns the updated channel (the linked state).
    """
    if request.epg_data_id is None and not request.tvg_id:
        raise HTTPException(
            status_code=400,
            detail="Provide either epg_data_id or tvg_id to link.",
        )

    client = get_client()
    try:
        epg_data_id = request.epg_data_id

        # Resolve a tvg_id to its EPG data row id when no explicit id was given.
        if epg_data_id is None:
            tvg_id = request.tvg_id
            candidates = await client.get_epg_data(search=tvg_id)
            match = next(
                (e for e in candidates if e.get("tvg_id") == tvg_id),
                None,
            )
            if match is None:
                logger.warning(
                    "[EPG-LINK] No EPG data row found for tvg_id=%r (channel=%s)",
                    tvg_id, channel_id,
                )
                raise HTTPException(
                    status_code=404,
                    detail=f"No EPG data found for tvg_id '{tvg_id}'.",
                )
            epg_data_id = match.get("id")

        # Establish the link with the same mechanism the exact-match/merge path
        # uses: PATCH the channel's epg_data_id.
        result = await client.update_channel(channel_id, {"epg_data_id": epg_data_id})

        journal.log_entry(
            category="channel",
            action_type="update",
            entity_id=channel_id,
            entity_name=result.get("name") if isinstance(result, dict) else None,
            description=f"Linked channel to EPG data id={epg_data_id}",
            after_value={"epg_data_id": epg_data_id},
        )

        logger.info(
            "[EPG-LINK] Linked channel=%s -> epg_data_id=%s", channel_id, epg_data_id,
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[EPG-LINK] Link rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[EPG-LINK] Failed to link channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="EPG link failed")
    _reject_unsafe_xml(output)
