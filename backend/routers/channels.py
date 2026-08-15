"""
Channels router — channel CRUD, logos, CSV import/export, stream management,
number assignment, bulk-commit, and clear-auto-created endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import os
import re
import time
import uuid
from datetime import date
from typing import Optional, Literal, Union
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

from auth import RequireAdminIfEnabled
from channel_number import (
    CHANNEL_NUMBER_RULE_MESSAGE,
    ChannelNumber,
    InvalidChannelNumberError,
    parse_channel_number_text,
    validate_channel_number_in_payload,
)
from channel_group_reparent import (
    UNGROUPED_TARGET_GROUP_NAME,
    reparent_group_channels,
)
from concurrency import run_cpu_bound
from config import get_settings
from csv_handler import parse_csv, generate_csv, generate_template, CSVParseError
from database import get_session
from dispatcharr_client import get_client, upstream_http_exception
from match_fold import fold_match_key
from normalization_engine import get_normalization_engine
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["Channels"])


def validate_stream_permutation(
    current_stream_ids: list[int], new_stream_ids: list[int]
) -> Optional[str]:
    """Validate that ``new_stream_ids`` is a true permutation of the channel's
    current streams (a pure reorder), guarding against the replace-semantics
    data loss of Dispatcharr's ``streams`` field.

    Updating a channel's ``streams`` to a partial list silently DETACHES any
    omitted streams. A reorder must therefore contain exactly the same set of
    stream IDs as the channel currently has — no missing, unknown, or duplicate
    IDs (bd-1wq7z.3 single-channel reorder; bd-1wq7z.25 bulk-commit reorder).

    Returns ``None`` if ``new_stream_ids`` is a valid permutation, otherwise a
    human-readable message describing the problem (suitable for an HTTP 400
    detail or a per-op bulk error).
    """
    current = list(current_stream_ids)
    new = list(new_stream_ids)

    if len(new) != len(set(new)):
        seen: set[int] = set()
        dupes = sorted({sid for sid in new if sid in seen or seen.add(sid)})
        return f"streamIds contains duplicate ids: {dupes}"

    current_set = set(current)
    new_set = set(new)

    missing = sorted(current_set - new_set)  # would be detached
    unknown = sorted(new_set - current_set)  # not attached to this channel

    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"would detach attached streams {missing}")
        if unknown:
            parts.append(f"includes streams not on the channel {unknown}")
        return (
            "streamIds is not a permutation of the channel's current streams "
            f"({'; '.join(parts)})"
        )

    return None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateChannelRequest(BaseModel):
    name: str
    # `ChannelNumber` is the canonical domain (bead enhancedchannelmanager-ic884.1):
    # non-negative, at most one decimal place. Every channel-number field in this
    # module uses it so no entry point re-implements the check.
    channel_number: Optional[ChannelNumber] = None
    channel_group_id: Optional[int] = None
    logo_id: Optional[int] = None
    tvg_id: Optional[str] = None
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


class CreateLogoRequest(BaseModel):
    name: str
    url: str


class AddStreamRequest(BaseModel):
    stream_id: int


class AddStreamsRequest(BaseModel):
    stream_ids: list[int]


class RemoveStreamRequest(BaseModel):
    stream_id: int


class ReorderStreamsRequest(BaseModel):
    stream_ids: list[int]


class AssignNumbersRequest(BaseModel):
    channel_ids: list[int]
    starting_number: Optional[ChannelNumber] = None


class MergeChannelsRequest(BaseModel):
    source_channel_ids: list[int]
    target_name: str
    target_channel_number: Optional[ChannelNumber] = None
    target_channel_group_id: Optional[int] = None
    target_logo_id: Optional[int] = None
    target_tvg_id: Optional[str] = None
    target_epg_data_id: Optional[int] = None
    target_stream_profile_id: Optional[int] = None


class ClearAutoCreatedRequest(BaseModel):
    group_ids: list[int]


class FindDuplicatesRequest(BaseModel):
    """Optional scope for POST /channels/find-duplicates (enhancedchannelmanager-uahp6).

    ``channel_ids`` absent — or the request body omitted entirely — scans all
    channels (global, backward-compatible default for MCP/script callers).
    When present, the scan is restricted to exactly those channel ids (used
    by the frontend's checkbox-selection-scoped "Find Duplicates" action).
    An explicit empty list is a valid scope of "no channels": it returns an
    empty result (0 groups) rather than silently falling back to a global
    scan — a caller that asked to scope to nothing should not get everything.

    ``fold_match_key`` (GH #645 / bead 0vao3): when True, channels are
    grouped by the shared canonical fold key — casefold + strip ALL
    whitespace (``match_fold.fold_match_key``) applied to the normalized
    name — so "Eurosport 2" and "Eurosport2" land in one duplicate group,
    matching what an auto-creation rule with its own ``fold_match_key``
    flag would merge. Default False preserves the existing grouping
    (normalized name, case-insensitive). Comparison key only — displayed
    names are never altered.
    """
    channel_ids: Optional[list[int]] = None
    fold_match_key: bool = False


class BulkMergeItem(BaseModel):
    """A single merge operation: keep target, absorb sources."""
    target_channel_id: int
    source_channel_ids: list[int]


class BulkMergeRequest(BaseModel):
    merges: list[BulkMergeItem]


class NormalizePreviewBatchItem(BaseModel):
    """Single row in a batch normalize-preview request.

    The frontend already knows the current channel name from the list
    response — passing it here avoids an extra Dispatcharr roundtrip per
    row. bd-eio04.13.
    """
    channel_id: int
    name: str


class NormalizePreviewBatchRequest(BaseModel):
    """bd-eio04.13 — batch preview of would_normalize for currently-visible channel rows.

    Accepts either:
      - `channels`: list of {channel_id, name} pairs (preferred — no
        Dispatcharr roundtrip, O(rules × M) instead of O(rules × M) +
        M HTTP calls).
      - `channel_ids`: list of ids (fallback — the backend fetches each
        name from Dispatcharr). Retained for deep-link scenarios where
        the caller only has IDs.
    Exactly one of the two must be populated.
    """
    channels: Optional[list[NormalizePreviewBatchItem]] = None
    channel_ids: Optional[list[int]] = None


# Upper bound on a single batch to keep per-request cost O(rules × 100).
# Frontend pages above this if more rows are visible. See bd-eio04.13.
NORMALIZE_PREVIEW_BATCH_MAX = 100


# Bulk commit operation types
class BulkUpdateChannelOp(BaseModel):
    type: Literal["updateChannel"] = "updateChannel"
    channelId: int
    data: dict

    @field_validator("data")
    @classmethod
    def _check_channel_number(cls, value: dict) -> dict:
        """`data` is a free-form field bag, so the contract is applied by key.

        Absent means "not changing the number"; explicit `None` means "clear
        it". Anything else must be in contract (bead
        enhancedchannelmanager-ic884.1).
        """
        try:
            validate_channel_number_in_payload(value)
        except InvalidChannelNumberError:
            raise PydanticCustomError("channel_number", CHANNEL_NUMBER_RULE_MESSAGE) from None
        return value


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
    startingNumber: Optional[ChannelNumber] = None


class BulkCreateChannelOp(BaseModel):
    type: Literal["createChannel"] = "createChannel"
    tempId: int  # Negative temp ID from frontend
    name: str
    channelNumber: Optional[ChannelNumber] = None
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
    # If true, consolidate redundant operations before executing
    consolidate: Optional[bool] = False


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
    # Bounds enforced here (bead 1a5mf): page<1 / page_size<1 were passed
    # straight to the upstream Dispatcharr client, which raised and surfaced as
    # a 500. FastAPI Query validation now returns 422 for out-of-range values.
    # Upper bound is generous — App.tsx legitimately requests page_size=5000.
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=10000, description="Results per page"),
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
async def create_channel(request: CreateChannelRequest, _admin=RequireAdminIfEnabled):
    """Create a new channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] POST /channels - name=%s number=%s normalize=%s", request.name, request.channel_number, request.normalize)
    client = get_client()
    try:
        # Apply normalization if requested.
        #
        # A failure here used to be swallowed: a log warning, the raw name, and
        # a 200 that was observationally IDENTICAL to `normalize=false`. The
        # caller asked for a capability, did not get it, and had no way to tell
        # (bead enhancedchannelmanager-e9e5o). The create still succeeds — a
        # channel that exists must not start reporting as a failure, and the
        # affected callers are third-party MCP/REST clients we cannot see — but
        # the response now SAYS what happened. See the `normalization` block
        # below the create call.
        channel_name = request.name
        normalization_error: Optional[str] = None
        if request.normalize:
            try:
                with get_session() as db:
                    engine = get_normalization_engine(db)
                    # Offload normalization off event loop (bd-w3z4h)
                    norm_result = await run_cpu_bound(engine.normalize, request.name)
                    channel_name = norm_result.normalized
                    if channel_name != request.name:
                        logger.debug("[CHANNELS] Normalized channel name: '%s' -> '%s'", request.name, channel_name)
            except Exception as norm_err:
                normalization_error = str(norm_err)
                logger.warning("[CHANNELS] Failed to normalize channel name '%s': %s", request.name, norm_err)
                # Continue with the original name, and disclose it below.

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

        # Say whether the normalization the caller asked for actually ran
        # (bead enhancedchannelmanager-e9e5o). Present ONLY when `normalize`
        # was requested, so a caller that never asked for it sees exactly the
        # response body it saw before. `applied` is the flag to branch on:
        # False means the engine did not run and `nameApplied` is the raw name.
        if request.normalize and isinstance(result, dict):
            result["normalization"] = {
                "requested": True,
                "applied": normalization_error is None,
                "nameApplied": result.get("name", channel_name),
                "error": normalization_error,
            }

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
    except HTTPException:
        raise
    except Exception as e:
        # Surface actionable Dispatcharr 4xx (e.g. non-existent channel_group_id)
        # instead of masking it as a generic 500 (bd-1wq7z.22).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Channel creation rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[CHANNELS] Channel creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Logos — MUST be defined before /api/channels/{channel_id} routes
# ---------------------------------------------------------------------------

# Fallback browser-cache policy for the logo image proxy when Dispatcharr
# sends no Cache-Control of its own (live-observed it sends 3600s for
# remote-origin logos / 14400s for local files). Long max-age is safe because
# the rewritten cache_url preserves Dispatcharr's ?v=<hash> cache-buster, so
# a replaced logo gets a new URL. Without browser caching every channel-list
# render would round-trip ECM -> Dispatcharr once per logo.
_LOGO_IMAGE_DEFAULT_CACHE_CONTROL = "public, max-age=86400"


def _ecm_origin(request: Request) -> str:
    """Browser-facing origin (``scheme://host[:port]``) for absolute ECM URLs.

    Honors ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` (first value of a
    comma list wins) so reverse-proxied deployments produce the public
    scheme/host — mirroring main.py's existing X-Forwarded-For convention —
    and otherwise falls back to the request's own scheme and Host header.
    The Host fallback is browser-reachable by construction: it is the host
    the browser itself dialed. Always yields an http(s) URL, which matters
    because LogoModal.tsx's preview sanitizer silently drops any other
    scheme (GH #662 / bead enhancedchannelmanager-hhmat).
    """
    proto = request.headers.get("x-forwarded-proto")
    scheme = (proto.split(",")[0].strip() if proto else request.url.scheme) or "http"
    if scheme not in ("http", "https"):
        scheme = "http"
    fwd_host = request.headers.get("x-forwarded-host")
    host = fwd_host.split(",")[0].strip() if fwd_host else request.url.netloc
    return f"{scheme}://{host}"


def _rewrite_logo_cache_url(logo, origin: str):
    """Point a logo dict's ``cache_url`` at ECM's same-origin image proxy.

    Dispatcharr builds ``cache_url`` from the Host header it saw on ECM's
    server-side request — often a docker-internal hostname or LAN IP the
    operator's browser cannot resolve, which breaks every logo (GH #662).
    Rewriting to ``{origin}/api/channels/logos/{id}/image`` keeps the bytes
    flowing through ECM's authenticated upstream client instead.

    Preserves Dispatcharr's ``?v=<hash>`` cache-buster so long browser
    caching stays correct across logo replacements. Leaves a falsy
    ``cache_url`` untouched (the frontend then falls back to ``logo.url``,
    which may be a browser-reachable external URL) and never touches
    ``url`` itself. Mutates and returns ``logo``.
    """
    if not isinstance(logo, dict):
        return logo
    logo_id = logo.get("id")
    cache_url = logo.get("cache_url")
    if logo_id is None or not cache_url:
        return logo
    version = parse_qs(urlsplit(str(cache_url)).query).get("v", [None])[0]
    suffix = f"?v={quote(version, safe='')}" if version else ""
    logo["cache_url"] = f"{origin}/api/channels/logos/{logo_id}/image{suffix}"
    return logo


@router.get("/logos")
async def get_logos(
    request: Request,
    # Bounds enforced here (bead enhancedchannelmanager-g4z2h, systemic sibling
    # of 1a5mf): page<1 / page_size<1 were passed straight to the upstream
    # Dispatcharr client, which raised and surfaced as a 500. Upper bound
    # matches sibling get_channels (channels.py) — App.tsx's getAllLogos()
    # direct calls legitimately request page_size=10000 (services/api.ts).
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=10000, description="Results per page"),
    search: Optional[str] = None,
    sort_by: Optional[Literal["name", "channel_count"]] = Query(
        None, description="Column to sort by. Requires ECM-side aggregation (see below)."
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort direction"),
    unused_only: bool = Query(False, description="Only return logos with channel_count == 0"),
):
    """List logos with pagination, search, sort, and an unused-only filter.

    Emits a single-line INFO diagnostic per request (bd-nh50y) so operators
    can grep one log line per request and see page/page_size/search/result
    count/elapsed/next-flag without enabling DEBUG. Per-row logging is
    intentionally avoided — at page_size=500 this is the channel-edit-modal
    fetch path and per-row would flood the log.

    Sort/filter (bead enhancedchannelmanager-09x38.13): Dispatcharr's
    LogoViewSet has no ordering support and its ``search`` query param is a
    no-op (only ``name``/``used``/``ids`` are read — confirmed by reading
    apps/channels/api_views.py in the live dispatcharr container). So when
    ``sort_by``, ``unused_only``, or a non-empty ``search`` is requested,
    this endpoint fetches the COMPLETE logo list from Dispatcharr in one
    call (client.get_all_logos_raw(), via Dispatcharr's `no_pagination=true`
    escape hatch) and sorts/filters/paginates it locally in Python — this
    is what makes sort and the unused-only filter truthful across pages.
    Requests using none of those three params take the original zero-
    overhead single-Dispatcharr-page passthrough (unchanged, backward
    compatible with LogoModal's picker, AutoSyncSettingsModal, GuideTab,
    and getAllLogos()).
    """
    logger.debug("[CHANNELS-LOGO] GET /channels/logos - page=%s search=%s", page, search)
    client = get_client()
    try:
        start = time.time()
        if sort_by is not None or unused_only or search:
            all_logos = await client.get_all_logos_raw()
            if search:
                search_lower = search.lower()
                all_logos = [
                    logo for logo in all_logos
                    if search_lower in (logo.get("name") or "").lower()
                ]
            if unused_only:
                all_logos = [logo for logo in all_logos if (logo.get("channel_count") or 0) == 0]

            reverse = sort_order == "desc"
            if sort_by == "channel_count":
                all_logos.sort(key=lambda logo: logo.get("channel_count") or 0, reverse=reverse)
            else:
                all_logos.sort(key=lambda logo: (logo.get("name") or "").lower(), reverse=reverse)

            total = len(all_logos)
            start_idx = (page - 1) * page_size
            page_items = all_logos[start_idx:start_idx + page_size]
            result = {
                "count": total,
                "next": "true" if start_idx + page_size < total else None,
                "previous": "true" if page > 1 else None,
                "results": page_items,
            }
        else:
            result = await client.get_logos(page=page, page_size=page_size, search=search)
        # Same-origin logo proxy rewrite (GH #662 / bead hhmat): see
        # _rewrite_logo_cache_url. Applied to both branches so the
        # passthrough and aggregate-and-sort paths stay consistent.
        origin = _ecm_origin(request)
        if isinstance(result, dict):
            for logo in result.get("results", []):
                _rewrite_logo_cache_url(logo, origin)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logos in %.1fms", elapsed_ms)
        # Single-line INFO diagnostic for the operator-grepable trace (bd-nh50y).
        # Pulls result-count from `results` (DRF paginated shape) and `next` from
        # the paginated envelope. Both can be missing in unusual responses, so
        # default defensively rather than KeyError on a malformed payload.
        results_count = (
            len(result.get("results", []))
            if isinstance(result, dict)
            else 0
        )
        has_next = bool(result.get("next")) if isinstance(result, dict) else False
        logger.info(
            "[CHANNELS-LOGO] GET /logos page=%s page_size=%s search=%s "
            "returned %s logos in %.1fms next=%s",
            page,
            page_size,
            search,
            results_count,
            elapsed_ms,
            "true" if has_next else "false",
        )
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logos: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logos/{logo_id}")
async def get_logo(logo_id: int, request: Request):
    """Get a single logo by ID."""
    logger.debug("[CHANNELS-LOGO] GET /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_logo(logo_id)
        _rewrite_logo_cache_url(result, _ecm_origin(request))
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logo id=%s in %.1fms", logo_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logos/{logo_id}/image")
async def get_logo_image(logo_id: int, request: Request):
    """Proxy the logo image bytes from Dispatcharr's cache endpoint.

    Browsers frequently cannot reach the host in Dispatcharr's ``cache_url``
    (GH #662 / bead enhancedchannelmanager-hhmat): Dispatcharr builds it from
    the Host header it saw on ECM's server-side request, which is often a
    docker-internal hostname or LAN IP. get_logos/get_logo rewrite
    ``cache_url`` to point here; this endpoint fetches the bytes server-side
    over ECM's authenticated Dispatcharr client and serves them same-origin.

    Live-observed upstream behavior (2026-07-18, against the deployed
    Dispatcharr): ``/api/channels/logos/{id}/cache/`` returns 200 + bytes for
    BOTH local-file and external-URL logos (it downloads and caches remote
    originals server-side), 404 JSON for unknown ids, emits Cache-Control
    (3600s remote / 14400s local) and no ETag, and never redirects.
    Cache-Control and any future ETag pass through; ``If-None-Match``
    forwards upstream so a 304 can short-circuit.

    Auth: intentionally NOT in main.py's AUTH_EXEMPT_PATHS — same-origin
    ``<img>`` tags send the httpOnly ``access_token`` cookie, so the global
    auth middleware admits real browser sessions without any frontend change.
    """
    logger.debug("[CHANNELS-LOGO] GET /channels/logos/%s/image", logo_id)
    client = get_client()
    upstream_headers = {}
    if_none_match = request.headers.get("if-none-match")
    if if_none_match:
        upstream_headers["If-None-Match"] = if_none_match
    try:
        upstream = await client._request(
            "GET",
            f"/api/channels/logos/{logo_id}/cache/",
            headers=upstream_headers,
        )
    except Exception as e:
        # Log only the exception TYPE — httpx error text can embed the full
        # request URL (same hygiene rule as dispatcharr_client._request).
        logger.warning(
            "[CHANNELS-LOGO] Logo image proxy transport failure id=%s: %s",
            logo_id,
            type(e).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Failed to fetch logo image from Dispatcharr"
        )

    if upstream.status_code == 304:
        return Response(status_code=304)
    if upstream.status_code == 404:
        raise HTTPException(status_code=404, detail="Logo image not found")
    if upstream.status_code >= 400:
        logger.warning(
            "[CHANNELS-LOGO] Logo image upstream error id=%s status=%s",
            logo_id,
            upstream.status_code,
        )
        raise HTTPException(status_code=502, detail="Upstream logo fetch failed")

    headers = {
        "Cache-Control": upstream.headers.get("cache-control")
        or _LOGO_IMAGE_DEFAULT_CACHE_CONTROL,
    }
    etag = upstream.headers.get("etag")
    if etag:
        headers["ETag"] = etag
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type") or "application/octet-stream",
        headers=headers,
    )


@router.post("/logos")
async def create_logo(
    request: CreateLogoRequest, http_request: Request, _admin=RequireAdminIfEnabled
):
    """Create a logo from a URL. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] POST /channels/logos - name=%s", request.name)
    client = get_client()
    try:
        start = time.time()
        result = await client.create_logo({"name": request.name, "url": request.url})
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Created logo id=%s name=%s in %.1fms", result.get('id'), result.get('name'), elapsed_ms)
        # Rewrite so a just-created logo renders immediately — ChannelsPane
        # and AutoSyncSettingsModal consume this response object directly
        # (GH #662 / bead hhmat).
        return _rewrite_logo_cache_url(result, _ecm_origin(http_request))
    except Exception as e:
        error_str = str(e)
        # Check if this is a "logo already exists" error from Dispatcharr
        if "logo with this url already exists" in error_str.lower() or "400" in error_str:
            try:
                existing_logo = await client.find_logo_by_url(request.url)
                if existing_logo:
                    logger.info("[CHANNELS-LOGO] Found existing logo id=%s name=%s", existing_logo.get('id'), existing_logo.get('name'))
                    return _rewrite_logo_cache_url(
                        existing_logo, _ecm_origin(http_request)
                    )
                else:
                    logger.warning("[CHANNELS-LOGO] Logo exists but could not find it by URL: %s", request.url)
            except Exception as search_err:
                logger.error("[CHANNELS-LOGO] Error searching for existing logo: %s", search_err)
        logger.exception("[CHANNELS-LOGO] Logo creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logos/upload")
async def upload_logo(request: Request, file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Upload a logo image file directly to Dispatcharr. Admin only (operator-only write, bd-v7n9f)."""
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
        # Rewrite so a just-uploaded logo renders immediately (GH #662).
        return _rewrite_logo_cache_url(result, _ecm_origin(request))
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Logo upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/logos/{logo_id}")
async def update_logo(
    logo_id: int, data: dict, request: Request, _admin=RequireAdminIfEnabled
):
    """Update a logo. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] PATCH /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.update_logo(logo_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Updated logo id=%s in %.1fms", logo_id, elapsed_ms)
        # Rewrite so the edited logo renders immediately (GH #662).
        return _rewrite_logo_cache_url(result, _ecm_origin(request))
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to update logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/logos/{logo_id}")
async def delete_logo(logo_id: int, _admin=RequireAdminIfEnabled):
    """Delete a logo. Admin only (operator-only write, bd-v7n9f)."""
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
async def import_channels_csv(file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Import channels from CSV file. Admin only (bulk operator op, bd-um30y)."""
    logger.debug("[CHANNELS-CSV] POST /channels/import-csv - filename=%s", file.filename)
    client = get_client()

    # Read and decode the file
    try:
        content = await file.read()
        csv_content = content.decode("utf-8")
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to read uploaded file: %s", e)
        raise HTTPException(status_code=400, detail="Failed to read file")

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
        logger.error("[CHANNELS-CSV] Failed to fetch channel groups: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch channel groups")

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
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to create group '%s': %s", row_num, group_name, ge)
                        warnings.append(f"Row {row_num}: Failed to create group '{group_name}'")

            # Build channel data
            channel_data = {
                "name": row["name"],
            }

            # Add optional fields
            channel_number = row.get("channel_number", "").strip()
            if channel_number:
                # `validate_channel_row` already rejected out-of-contract values
                # before the row reached this loop, so this parse re-uses the
                # same canonical function rather than a second, looser `float()`
                # (bead enhancedchannelmanager-ic884.1).
                parsed_number = parse_channel_number_text(channel_number)
                if parsed_number is not None:
                    channel_data["channel_number"] = parsed_number

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
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to assign EPG logo: %s", row_num, le)
                        warnings.append(f"Row {row_num}: Failed to assign EPG logo")

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
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to link streams: %s", row_num, se)
                        warnings.append(f"Row {row_num}: Failed to link streams")

        except Exception as e:
            logger.warning("[CHANNELS-CSV] Row %s import error: %s", row_num, e)
            errors.append({"row": row_num, "error": "Failed to import row"})

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
        logger.warning("[CHANNELS-CSV] CSV parse error: %s", e)
        return {
            "rows": [],
            "errors": [{"row": 1, "error": "Failed to parse CSV"}]
        }


# ---------------------------------------------------------------------------
# Static bulk routes — MUST be defined before /api/channels/{channel_id}
# ---------------------------------------------------------------------------

@router.post("/assign-numbers")
async def assign_channel_numbers(request: AssignNumbersRequest, _admin=RequireAdminIfEnabled):
    """Bulk assign channel numbers. Admin only (bulk operator op, bd-um30y)."""
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


def _consolidate_operations(operations: list[BulkOperation]) -> list[BulkOperation]:
    """Consolidate redundant operations to minimize API calls.

    Optimizations:
    - Multiple updateChannel for same channel -> single update with merged data
    - Multiple bulkAssignChannelNumbers -> single call with final positions
    - Add then remove same stream -> both cancelled
    - Multiple reorderChannelStreams for same channel -> only final order kept
    - Operations targeting channels to be deleted are removed
    - Create + delete of same temp channel cancel out
    """
    start = time.time()
    original_count = len(operations)

    # First pass: find channels to be deleted and temp channels to be created.
    # Both sets are computed up front so that create+delete cancellation is
    # order-independent (a deleteChannel may precede its matching createChannel).
    channels_to_delete: set[int] = set()
    channels_to_create: set[int] = set()
    for op in operations:
        if op.type == "deleteChannel":
            channels_to_delete.add(op.channelId)
        elif op.type == "createChannel":
            channels_to_create.add(op.tempId)

    # Track final state for each operation type
    channel_final_updates: dict[int, dict] = {}  # channelId -> merged data
    channel_final_numbers: dict[int, float] = {}  # channelId -> final number
    channel_final_stream_order: dict[int, list[int]] = {}  # channelId -> final stream IDs
    stream_ops: dict[str, dict] = {}  # "channelId:streamId" -> {added: op, removed: op}
    ordered_ops: list[BulkOperation] = []  # create/delete ops in order

    for op in operations:
        if op.type == "bulkAssignChannelNumbers":
            start_num = op.startingNumber or 0
            for i, cid in enumerate(op.channelIds):
                if cid not in channels_to_delete:
                    channel_final_numbers[cid] = start_num + i

        elif op.type == "updateChannel":
            if op.channelId not in channels_to_delete:
                existing = channel_final_updates.get(op.channelId, {})
                existing.update(op.data)
                channel_final_updates[op.channelId] = existing

        elif op.type == "reorderChannelStreams":
            if op.channelId not in channels_to_delete:
                channel_final_stream_order[op.channelId] = op.streamIds

        elif op.type == "addStreamToChannel":
            if op.channelId not in channels_to_delete:
                key = f"{op.channelId}:{op.streamId}"
                entry = stream_ops.setdefault(key, {"added": None, "removed": None})
                entry["added"] = op

        elif op.type == "removeStreamFromChannel":
            if op.channelId not in channels_to_delete:
                key = f"{op.channelId}:{op.streamId}"
                entry = stream_ops.setdefault(key, {"added": None, "removed": None})
                entry["removed"] = op

        elif op.type == "createChannel":
            # Create + delete of the same temp channel cancel out.
            if op.tempId not in channels_to_delete:
                ordered_ops.append(op)

        elif op.type == "deleteChannel":
            if op.channelId < 0 and op.channelId in channels_to_create:
                pass  # Create + delete cancel out (order-independent)
            else:
                ordered_ops.append(op)

        elif op.type in ("createGroup", "deleteChannelGroup", "renameChannelGroup"):
            ordered_ops.append(op)

    # Build consolidated list
    consolidated: list[BulkOperation] = list(ordered_ops)

    # Merged updateChannel ops
    for cid, data in channel_final_updates.items():
        consolidated.append(BulkUpdateChannelOp(channelId=cid, data=data))

    # Consolidated bulkAssign: group into consecutive ranges
    if channel_final_numbers:
        entries = sorted(channel_final_numbers.items(), key=lambda e: e[1])
        i = 0
        while i < len(entries):
            start_num = entries[i][1]
            j = i
            while j + 1 < len(entries) and entries[j + 1][1] == entries[j][1] + 1:
                j += 1
            ids = [e[0] for e in entries[i:j + 1]]
            consolidated.append(BulkAssignNumbersOp(channelIds=ids, startingNumber=start_num))
            i = j + 1

    # Consolidated reorder ops
    for cid, stream_ids in channel_final_stream_order.items():
        consolidated.append(BulkReorderStreamsOp(channelId=cid, streamIds=stream_ids))

    # Stream add/remove (cancelled pairs excluded)
    for entry in stream_ops.values():
        if entry["added"] and entry["removed"]:
            continue  # Cancel out
        if entry["added"]:
            consolidated.append(entry["added"])
        if entry["removed"]:
            consolidated.append(entry["removed"])

    elapsed = (time.time() - start) * 1000
    logger.info(
        "[CHANNELS-BULK] Consolidated %d -> %d operations in %.1fms",
        original_count, len(consolidated), elapsed,
    )
    return consolidated


# -----------------------------------------------------------------------------
# Bulk-commit background jobs (bd-ggxks)
# -----------------------------------------------------------------------------
# Each Dispatcharr write inside a bulk commit is a sequential HTTP call
# (~0.7s/channel for createChannel). A 441-op SiriusXM batch easily exceeds
# the 30s ECM_REQUEST_TIMEOUT_SECONDS middleware budget, returning 504 to the
# operator while the handler keeps running in the background. The operator
# retries, piling duplicates into Dispatcharr.
#
# Architecture (matches bd-cns7j debug-bundle and bd-enfsy /auto-creation/run):
#   POST /bulk-commit           → 202 + {job_id, status: "running"}; supervised
#                                 background task does the work. validateOnly
#                                 stays SYNCHRONOUS — pre-validation is fast
#                                 and the frontend uses it for instant feedback
#                                 before commit, where a poll round-trip would
#                                 add latency for no gain.
#   GET  /bulk-commit/{job_id}  → JSON status: running | failed (with error) |
#                                 completed (with the full BulkCommitResponse
#                                 envelope under ``result``). Completed jobs
#                                 are evicted on first read so RAM is freed.
#
# Job state lives in-memory because the result envelope is small (a few KB
# even for 1000+ ops) and the bulk commit is operator-triggered. TTL prune
# on every new POST keeps the dict bounded if a client abandons polling.

_BULK_COMMIT_JOB_TTL_SECONDS = 1800  # 30 min — matches debug-bundle TTL
_BULK_COMMIT_BACKGROUND_TASKS: set[asyncio.Task] = set()


class _BulkCommitJob:
    """In-memory state for one bulk-commit run (bd-ggxks)."""

    __slots__ = ("status", "created_at", "completed_at", "error", "result")

    def __init__(self) -> None:
        self.status: str = "running"  # running | completed | failed
        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None
        self.error: Optional[str] = None
        self.result: Optional[dict] = None


_BULK_COMMIT_JOBS: dict[str, _BulkCommitJob] = {}


def _prune_old_bulk_commit_jobs() -> None:
    """Drop bulk-commit jobs older than the TTL so the dict can't grow unbounded."""
    cutoff = time.time() - _BULK_COMMIT_JOB_TTL_SECONDS
    stale = [jid for jid, job in _BULK_COMMIT_JOBS.items() if job.created_at < cutoff]
    for jid in stale:
        _BULK_COMMIT_JOBS.pop(jid, None)
    if stale:
        logger.debug("[CHANNELS-BULK] Pruned %s expired bulk-commit jobs", len(stale))


@router.post("/bulk-commit")
async def bulk_commit_operations(request: BulkCommitRequest, _admin=RequireAdminIfEnabled):
    """
    Process multiple channel operations. Admin only (bulk operator op, bd-um30y).

    Two response shapes (bd-ggxks):

    - ``validateOnly: true`` → **200 sync** with the full BulkCommitResponse.
      Validation runs entirely against ECM-cached lookups + a single
      Dispatcharr page fetch, so it fits comfortably inside the 30s request
      budget and the frontend uses it for instant pre-commit feedback.
    - ``validateOnly: false`` (default) → **202** with
      ``{job_id, status: "running"}``. The actual work runs in a supervised
      background task; the client polls
      ``GET /api/channels/bulk-commit/{job_id}`` until the status is terminal
      (``completed`` carries the full BulkCommitResponse under ``result``;
      ``failed`` carries an ``error`` string).

    Options:
    - validateOnly: If true, only validate without executing
    - continueOnError: If true, continue processing even when operations fail
    - consolidate: If true, server-side dedup of redundant ops
    """
    # Validate-only is fast — keep it sync so the frontend gets pre-commit
    # feedback in one round-trip instead of POST+poll.
    if request.validateOnly:
        return await _run_bulk_commit(request)

    # Enqueue the actual commit as a supervised background task.
    _prune_old_bulk_commit_jobs()
    job_id = uuid.uuid4().hex
    _BULK_COMMIT_JOBS[job_id] = _BulkCommitJob()
    op_count = len(request.operations)

    async def _runner() -> None:
        job = _BULK_COMMIT_JOBS.get(job_id)
        if job is None:
            logger.warning("[CHANNELS-BULK] Job %s missing before start", job_id)
            return
        try:
            result = await _run_bulk_commit(request)
            job.result = result
            job.status = "completed"
            job.completed_at = time.time()
            logger.info(
                "[CHANNELS-BULK] Job %s completed: applied=%s failed=%s",
                job_id, result.get("operationsApplied"), result.get("operationsFailed"),
            )
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "Background task cancelled"
            job.completed_at = time.time()
            logger.warning("[CHANNELS-BULK] Job %s cancelled", job_id)
            raise
        except Exception as e:  # noqa: BLE001 — supervisor must catch broadly
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.completed_at = time.time()
            logger.exception("[CHANNELS-BULK] Job %s failed: %s", job_id, e)

    task = asyncio.create_task(_runner(), name=f"bulk-commit-{job_id}")
    _BULK_COMMIT_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BULK_COMMIT_BACKGROUND_TASKS.discard)

    logger.info("[CHANNELS-BULK] Job %s enqueued (%s operations)", job_id, op_count)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "running",
            "message": (
                f"Bulk commit started; poll /api/channels/bulk-commit/{job_id} "
                "for status"
            ),
        },
    )


@router.get("/bulk-commit/{job_id}")
async def get_bulk_commit_status(job_id: str):
    """Poll a bulk-commit job (bd-ggxks).

    - ``running``   → ``{job_id, status: "running"}``
    - ``failed``    → ``{job_id, status: "failed", error}`` (job stays in the
      dict until TTL prune so the operator can re-poll and see the error)
    - ``completed`` → ``{job_id, status: "completed", result: <BulkCommitResponse>}``
      and the job is evicted on read (single-shot retrieval).
    - missing job   → 404
    """
    job = _BULK_COMMIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk commit job not found")
    if job.status == "running":
        return {"job_id": job_id, "status": "running"}
    if job.status == "failed":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": job.error or "unknown error",
        }
    # completed — drop on read so RAM is freed.
    result = job.result or {}
    _BULK_COMMIT_JOBS.pop(job_id, None)
    return {"job_id": job_id, "status": "completed", "result": result}


async def _run_bulk_commit(request: BulkCommitRequest) -> dict:
    """Execute a bulk-commit request and return the result envelope.

    Pure work function — no HTTP / endpoint awareness. Invoked synchronously by
    POST /bulk-commit when ``validateOnly=true``, and from the supervised
    background task dispatched by POST /bulk-commit otherwise (bd-ggxks).
    """
    client = get_client()
    batch_id = str(uuid.uuid4())[:8]

    # Count operation types for logging
    op_counts = {}
    for op in request.operations:
        op_counts[op.type] = op_counts.get(op.type, 0) + 1
    op_summary = ", ".join(f"{count} {op_type}" for op_type, count in sorted(op_counts.items()))

    logger.debug("[CHANNELS-BULK] Starting bulk commit (batch=%s): %s operations (%s)", batch_id, len(request.operations), op_summary)
    logger.debug("[CHANNELS-BULK] Options: validateOnly=%s, continueOnError=%s, consolidate=%s", request.validateOnly, request.continueOnError, request.consolidate)

    # Consolidate operations if requested
    operations = request.operations
    if request.consolidate:
        operations = _consolidate_operations(operations)
        request = request.model_copy(update={"operations": operations})
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
        "partial": False,  # bd-5xciq: some-applied-some-failed outcome
        # createChannel ops that ASKED for normalization and did not get it
        # (bead enhancedchannelmanager-e9e5o). Always present, so a caller
        # checks its length rather than probing for a key that may not exist.
        # A normalization failure does NOT fail the op or the batch: the
        # channel was created, just under the raw name — which is precisely
        # why the envelope has to say so, since the result is otherwise
        # indistinguishable from `normalize=false`.
        "normalizationFailures": [],
    }

    # Helper to resolve temp IDs to real IDs
    def resolve_id(channel_id: int) -> int:
        return result["tempIdMap"].get(channel_id, channel_id)

    # Helper to resolve group ID (could be temp or real, or from new group name)
    def resolve_group_id(group_id: Optional[int], new_group_name: Optional[str]) -> Optional[int]:
        if new_group_name and new_group_name in result["groupIdMap"]:
            return result["groupIdMap"][new_group_name]
        return group_id

    class UnresolvedGroupError(Exception):
        """A group id that was never resolved to a real Dispatcharr group.

        Negative ids are the frontend's staging placeholders. Dispatcharr
        answers one with ``400 {"channel_group_id": ["Invalid pk \\"-1000\\" -
        object does not exist."]}``, and drill run 2026-08-09-run18 lost a
        channel to exactly that (bead ``enhancedchannelmanager-udq1j``). The
        frontend now resolves them by name before posting; this is the
        backstop for an older or scripted client that does not, so the failure
        is named in ECM's own error rather than relayed as an opaque upstream
        400.
        """

    def reject_unresolved_group(group_id: Optional[int], label: str) -> Optional[int]:
        """Return ``group_id``, or raise if it is still a staging placeholder."""
        if group_id is not None and group_id < 0:
            raise UnresolvedGroupError(
                f"Channel group {group_id} does not exist. A new group must be sent in "
                f"groupsToCreate and referenced by name ({label})."
            )
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
            if op.type == "updateChannel":
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
            elif op.type == "deleteChannel":
                if op.channelId >= 0 and op.channelId not in existing_channels:
                    # Deleting a channel that doesn't exist is a no-op, not an error
                    logger.debug("[CHANNELS-BULK] deleteChannel: channel %s already gone, skipping", op.channelId)

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

        # Per-run logo index (bd-raehx). Previously every createChannel op with
        # a logoUrl + no logoId called find_logo_by_url(), which re-paginated
        # the ENTIRE Dispatcharr logo catalog (~25 pages for large installs).
        # That made the createChannel path O(channels * catalog_pages) — 859
        # logo GETs for a 113-channel batch — exhausting the request budget.
        #
        # Instead we paginate the catalog ONCE, lazily (only the first time a
        # createChannel op actually needs a logo lookup, so validateOnly and
        # logo-free batches pay nothing), and build a {url -> logo} index that
        # every subsequent op reuses. New logos created mid-batch are inserted
        # into the index so a later op sharing the same logoUrl reuses them
        # instead of creating a duplicate.
        #
        # The index is per-run state (closure-local) by design — a long-lived
        # process cache would go stale against Dispatcharr.
        logo_index: Optional[dict[str, dict]] = None

        async def resolve_logo_id(logo_url: str, logo_name: str) -> Optional[int]:
            """Return a logo id for ``logo_url``, building the per-run index on
            first use and creating (and caching) a new logo when absent.

            Raises on a hard failure (pagination error, create error) so the
            caller's try/except can preserve the existing "create the channel
            without a logo" fallthrough behavior.
            """
            nonlocal logo_index
            if logo_index is None:
                # First need this run — paginate the full catalog once.
                logo_index = {}
                page = 1
                page_size = 500
                while True:
                    page_result = await client.get_logos(page=page, page_size=page_size)
                    for logo in page_result.get("results", []):
                        url = logo.get("url")
                        # First occurrence of a url wins (matches find_logo_by_url,
                        # which returned the first match).
                        if url and url not in logo_index:
                            logo_index[url] = logo
                    if not page_result.get("next"):
                        break
                    page += 1
                logger.debug("[CHANNELS-BULK] Built logo index: %s logos across %s page(s)", len(logo_index), page)

            existing = logo_index.get(logo_url)
            if existing is not None:
                return existing["id"]

            new_logo = await client.create_logo({"name": logo_name, "url": logo_url})
            # Cache by url so a later op with the same logoUrl reuses it
            # (fixes a latent duplicate-logo bug too).
            logo_index[logo_url] = new_logo
            return new_logo["id"]

        # Phase 2: Process operations sequentially
        logger.debug("[CHANNELS-BULK] Phase 2: Processing %s operations", len(request.operations))
        for idx, op in enumerate(request.operations):
            op_id = f"op-{idx}-{op.type}"
            try:
                if op.type == "updateChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] updateChannel: channel_id=%s, data=%s", idx+1, len(request.operations), channel_id, op.data)
                    if "channel_group_id" in op.data:
                        reject_unresolved_group(
                            op.data["channel_group_id"],
                            f"updateChannel on channel {channel_id}",
                        )
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
                    # Guard against silent stream detachment (bd-1wq7z.25):
                    # Dispatcharr's ``streams`` field uses replace-semantics, so
                    # a partial streamIds list would detach the omitted streams.
                    # Require a true permutation of the channel's current set.
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    perm_error = validate_stream_permutation(current_streams, op.streamIds)
                    if perm_error is not None:
                        logger.warning(
                            "[CHANNELS-BULK] reorderChannelStreams rejected for channel %s: %s",
                            channel_id, perm_error,
                        )
                        raise ValueError(
                            f"Cannot reorder streams for channel {channel_id}: {perm_error}"
                        )
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
                    group_id = reject_unresolved_group(
                        resolve_group_id(op.groupId, op.newGroupName),
                        f"createChannel '{op.name}'",
                    )

                    # Apply normalization if requested. Same contract as the
                    # single-create path above: the op still applies, but a
                    # swallowed failure would leave the caller unable to tell
                    # this apart from `normalize=false`, so it is recorded in
                    # `normalizationFailures` (bead enhancedchannelmanager-e9e5o).
                    channel_name = op.name
                    if op.normalize:
                        try:
                            with get_session() as db:
                                engine = get_normalization_engine(db)
                                # Offload normalization off event loop (bd-w3z4h)
                                norm_result = await run_cpu_bound(engine.normalize, op.name)
                                channel_name = norm_result.normalized
                                if channel_name != op.name:
                                    logger.debug("[CHANNELS-BULK] Normalized channel name: '%s' -> '%s'", op.name, channel_name)
                        except Exception as norm_err:
                            logger.warning("[CHANNELS-BULK] Failed to normalize channel name '%s': %s", op.name, norm_err)
                            # Continue with the original name, and disclose it.
                            result["normalizationFailures"].append({
                                "tempId": op.tempId,
                                "name": op.name,
                                "nameApplied": op.name,
                                "error": str(norm_err),
                            })

                    # Handle logo - if logoUrl provided but no logoId, resolve
                    # via the per-run logo index (bd-raehx) instead of
                    # re-paginating the whole catalog per channel.
                    logo_id = op.logoId
                    if not logo_id and op.logoUrl:
                        try:
                            logger.debug("[CHANNELS-BULK] Resolving logo by URL for channel '%s'", op.name)
                            logo_id = await resolve_logo_id(op.logoUrl, channel_name)
                            logger.debug("[CHANNELS-BULK] Resolved logo ID %s for channel '%s'", logo_id, op.name)
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
                    try:
                        await client.delete_channel(channel_id)
                        logger.debug("[CHANNELS-BULK] Deleted channel %s", channel_id)
                    except Exception as del_err:
                        if "404" in str(del_err) or "not found" in str(del_err).lower():
                            logger.debug("[CHANNELS-BULK] Channel %s already deleted, skipping", channel_id)
                        else:
                            raise
                    result["operationsApplied"] += 1

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
                    moved = await reparent_group_channels(
                        client, op.groupId, log_prefix="[CHANNELS-BULK]"
                    )
                    await client.delete_channel_group(op.groupId)
                    result["operationsApplied"] += 1
                    logger.debug("[CHANNELS-BULK] Deleted group %s (moved %s channel(s) to '%s')", op.groupId, moved, UNGROUPED_TARGET_GROUP_NAME)

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
                # If continuing, keep going — but the batch is no longer a
                # success, and `partial` below is what records that some of it
                # still landed.

        # Determine final success status.
        #
        # A failed operation is a failure whatever `continueOnError` says (bead
        # …-ayfn9). That flag answers "keep going after one fails?", NOT "call the
        # batch a win if anything landed" — and the old
        # `failed == 0 or applied > 0` reading meant a single successful op could
        # launder every failure beside it into `success=True`. Drill run
        # 2026-08-08-run17: Delete Group raised 400 server-side, the operator was
        # told it worked, and the only trace was an ERROR in the container log.
        #
        # `partial` (below) is what still distinguishes "some of it landed" from
        # "none of it did", and the frontend renders that case as
        # "X succeeded, Y failed" rather than as a flat failure.
        result["success"] = result["operationsFailed"] == 0

        # Partial outcome flag (bd-5xciq): some ops committed AND some failed.
        # The frontend uses this to render "X applied, Y failed" distinctly so
        # the operator reconciles via tempIdMap instead of blindly retrying and
        # piling up duplicate channels. A full success or a total failure
        # (nothing applied) is NOT partial.
        result["partial"] = result["operationsApplied"] > 0 and result["operationsFailed"] > 0

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


@router.post("/normalize-preview-batch")
async def normalize_preview_batch(request: NormalizePreviewBatchRequest):
    """bd-eio04.13 — batch would-normalize preview for channel list rows.

    Per-row result:

        {
          "channel_id": int,
          "current_name": str,
          "proposed_name": str,
          "would_change": bool,
          "transformations": [{rule_id, before, after}, ...],
        }

    Prefers the `channels` payload ({channel_id, name}) when provided —
    no Dispatcharr roundtrip, O(rules × M) total cost. Falls back to
    `channel_ids` for callers that only have IDs (e.g. deep-link flows);
    in that case the backend fetches each name from Dispatcharr and
    silently skips rows it can't resolve.

    Batch size is capped at NORMALIZE_PREVIEW_BATCH_MAX (100) rows. The
    frontend is responsible for paging above that window.
    """
    if request.channels is not None and request.channel_ids is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'channels' or 'channel_ids', not both.",
        )

    rows = request.channels or []
    ids_only: list[int] = list(request.channel_ids or [])
    total = len(rows) + len(ids_only)

    if total > NORMALIZE_PREVIEW_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many rows ({total}); maximum "
                f"{NORMALIZE_PREVIEW_BATCH_MAX} per batch."
            ),
        )

    if total == 0:
        return {"results": []}

    logger.debug(
        "[CHANNELS] POST /normalize-preview-batch - rows=%s ids_only=%s",
        len(rows), len(ids_only),
    )

    session = get_session()
    try:
        engine = get_normalization_engine(session)
        results: list[dict] = []
        start = time.time()

        # Fast path: names already supplied by caller.
        for row in rows:
            current_name = row.name or ""
            preview = engine.normalize(current_name)
            proposed = preview.normalized
            results.append({
                "channel_id": row.channel_id,
                "current_name": current_name,
                "proposed_name": proposed,
                "would_change": proposed != current_name,
                "transformations": [
                    {"rule_id": t[0], "before": t[1], "after": t[2]}
                    for t in (preview.transformations or [])
                ],
            })

        # Fallback path: fetch names for bare IDs via Dispatcharr.
        if ids_only:
            client = get_client()
            for cid in ids_only:
                try:
                    channel = await client.get_channel(cid)
                except Exception as exc:
                    logger.debug("[CHANNELS] normalize-preview: skipped id=%s: %s", cid, exc)
                    continue
                current_name = (channel or {}).get("name") or ""
                preview = engine.normalize(current_name)
                proposed = preview.normalized
                results.append({
                    "channel_id": cid,
                    "current_name": current_name,
                    "proposed_name": proposed,
                    "would_change": proposed != current_name,
                    "transformations": [
                        {"rule_id": t[0], "before": t[1], "after": t[2]}
                        for t in (preview.transformations or [])
                    ],
                })

        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[CHANNELS] normalize-preview-batch: resolved %d/%d in %.1fms",
            len(results), total, elapsed_ms,
        )
        return {"results": results}
    finally:
        session.close()


@router.post("/clear-auto-created")
async def clear_auto_created_flag(request: ClearAutoCreatedRequest, _admin=RequireAdminIfEnabled):
    """Clear the auto_created flag from all channels in the specified groups.

    This converts auto_created channels to manual channels by setting
    auto_created=False and auto_created_by=None.

    Admin only (destructive bulk operator op, bd-um30y).
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-1wq7z.22).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Fetch channel id=%s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to fetch channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{channel_id}/normalize-preview")
async def normalize_preview_single(channel_id: int):
    """bd-eio04.13 — would-normalize preview for a single channel.

    Returns `{channel_id, current_name, proposed_name, would_change,
    transformations}` by fetching the channel from Dispatcharr and running
    the active NormalizationEngine against its current name.
    """
    logger.debug("[CHANNELS] GET /channels/%s/normalize-preview", channel_id)
    client = get_client()
    try:
        start = time.time()
        channel = await client.get_channel(channel_id)
        current_name = (channel or {}).get("name") or ""

        session = get_session()
        try:
            engine = get_normalization_engine(session)
            preview = engine.normalize(current_name)
        finally:
            session.close()

        proposed = preview.normalized
        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[CHANNELS] normalize-preview id=%s '%s' -> '%s' in %.1fms",
            channel_id, current_name, proposed, elapsed_ms,
        )
        return {
            "channel_id": channel_id,
            "current_name": current_name,
            "proposed_name": proposed,
            "would_change": proposed != current_name,
            "transformations": [
                {"rule_id": t[0], "before": t[1], "after": t[2]}
                for t in (preview.transformations or [])
            ],
        }
    except Exception as e:
        logger.exception("[CHANNELS] Failed normalize-preview for id=%s: %s", channel_id, e)
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-8w1ba). Common trigger: callers pass a channel NUMBER (shown in the
        # UI) rather than the internal channel id.
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning(
                "[CHANNELS] Upstream error fetching streams for channel id=%s: %s",
                channel_id, e,
            )
            raise mapped
        logger.exception("[CHANNELS] Failed to fetch streams for channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/{channel_id}")
async def update_channel(channel_id: int, data: dict, _admin=RequireAdminIfEnabled):
    """Update a channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] PATCH /channels/%s - data=%s", channel_id, data)
    # The body is an untyped field bag, so the canonical channel-number contract
    # is applied by key rather than by field type (bead
    # enhancedchannelmanager-ic884.1).
    try:
        validate_channel_number_in_payload(data)
    except InvalidChannelNumberError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id (or bad group/logo/etc.) surfaces as an upstream
        # 4xx — map it to a clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Update channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to update channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/merge")
async def merge_channels(request: "MergeChannelsRequest", _admin=RequireAdminIfEnabled):
    """Merge multiple channels into a single new channel.

    Creates a new channel with the specified metadata, moves all streams
    from the source channels into it (preserving order, deduplicating),
    then deletes the source channels.

    Admin only (destructive operator op — deletes channels, bd-um30y).
    """
    logger.debug("[CHANNELS] POST /channels/merge - sources=%s target_name=%s",
                 request.source_channel_ids, request.target_name)

    if len(request.source_channel_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 source channels are required")

    client = get_client()
    new_channel = None
    try:
        # 1. Fetch all source channels and collect their streams (ordered, deduplicated).
        #    If any source ID no longer exists upstream (e.g., the operator's UI
        #    held a stale reference after a previous merge), surface 422 with a
        #    refresh hint instead of falling through to the catch-all 500. The
        #    UI fix in useEditMode keeps these stale IDs from accumulating in the
        #    first place; this is defense in depth for any other stale-state path.
        source_channels = []
        all_streams: list[int] = []
        seen_streams: set[int] = set()
        missing_ids: list[int] = []
        for cid in request.source_channel_ids:
            try:
                channel = await client.get_channel(cid)
            except httpx.HTTPStatusError as fetch_err:
                if fetch_err.response.status_code == 404:
                    missing_ids.append(cid)
                    continue
                raise
            source_channels.append(channel)
            for sid in channel.get("streams", []):
                if sid not in seen_streams:
                    all_streams.append(sid)
                    seen_streams.add(sid)

        if missing_ids:
            logger.warning("[CHANNELS] Merge rejected: stale source IDs %s no longer exist", missing_ids)
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Source channels {missing_ids} no longer exist — "
                    "refresh the channels list and try again"
                ),
            )

        # 2. Create the new merged channel
        create_data = {"name": request.target_name}
        if request.target_channel_number is not None:
            create_data["channel_number"] = request.target_channel_number
        if request.target_channel_group_id is not None:
            create_data["channel_group_id"] = request.target_channel_group_id
        if request.target_logo_id is not None:
            create_data["logo_id"] = request.target_logo_id
        if request.target_tvg_id is not None:
            create_data["tvg_id"] = request.target_tvg_id

        new_channel = await client.create_channel(create_data)
        new_channel_id = new_channel["id"]
        logger.info("[CHANNELS] Created merged channel id=%s name=%s", new_channel_id, request.target_name)

        # 3. Assign streams to the new channel
        if all_streams:
            await client.update_channel(new_channel_id, {"streams": all_streams})
            logger.info("[CHANNELS] Assigned %d streams to merged channel %s", len(all_streams), new_channel_id)

        # 4. Update EPG data if specified
        update_data = {}
        if request.target_epg_data_id is not None:
            update_data["epg_data_id"] = request.target_epg_data_id
        if request.target_stream_profile_id is not None:
            update_data["stream_profile_id"] = request.target_stream_profile_id
        if update_data:
            await client.update_channel(new_channel_id, update_data)

        # 5. Delete source channels
        deleted_ids = []
        for cid in request.source_channel_ids:
            try:
                await client.delete_channel(cid)
                deleted_ids.append(cid)
                logger.debug("[CHANNELS] Deleted source channel %s during merge", cid)
            except Exception as del_err:
                logger.warning("[CHANNELS] Failed to delete source channel %s during merge: %s", cid, del_err)

        # 6. Fetch the final state of the merged channel
        result = await client.get_channel(new_channel_id)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="merge",
            entity_id=new_channel_id,
            entity_name=request.target_name,
            description=f"Merged {len(source_channels)} channels into '{request.target_name}'",
            before_value={"source_channels": [{"id": ch.get("id"), "name": ch.get("name")} for ch in source_channels]},
            after_value={"merged_channel_id": new_channel_id, "stream_count": len(all_streams), "deleted_source_ids": deleted_ids},
        )

        logger.info("[CHANNELS] Merge complete: %d channels -> '%s' (id=%s, %d streams)",
                     len(source_channels), request.target_name, new_channel_id, len(all_streams))

        return result

    except HTTPException:
        raise
    except Exception as e:
        # Rollback: delete the new channel if it was created
        if new_channel:
            try:
                await client.delete_channel(new_channel["id"])
                logger.info("[CHANNELS] Rolled back merged channel %s after error", new_channel["id"])
            except Exception:
                logger.warning("[CHANNELS] Failed to rollback merged channel %s", new_channel["id"])
        # Map an upstream 4xx (e.g. a bad target group/logo id) to a clean 4xx
        # instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Channel merge rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[CHANNELS] Channel merge failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, _admin=RequireAdminIfEnabled):
    """Delete a channel. Admin only (operator-only write, bd-v7n9f)."""
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Delete channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to delete channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest, _admin=RequireAdminIfEnabled):
    """Add a stream to a channel. Admin only (operator-only write, bd-v7n9f)."""
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel/stream id surfaces as an upstream 4xx — map it to a
        # clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Add stream %s to channel %s rejected by Dispatcharr: %s", request.stream_id, channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to add stream %s to channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/add-streams")
async def add_streams_to_channel(channel_id: int, request: AddStreamsRequest, _admin=RequireAdminIfEnabled):
    """Add multiple streams to a channel in a single Dispatcharr roundtrip.

    Admin only (operator-only write, bd-v7n9f).

    Mirrors the single-add semantics (dedup against streams already on the
    channel, append in request order) but fetches the channel once and PUTs
    once instead of once per stream — the MCP ``bulk_add_streams_to_channel``
    tool used to loop the single-add endpoint, which times out on slow
    hardware for batches of ~10 streams (bd-02xjj / GH #223).
    """
    logger.debug("[CHANNELS] POST /channels/%s/add-streams - %d stream_ids", channel_id, len(request.stream_ids))
    client = get_client()
    try:
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = list(channel.get("streams", []))
        before_streams = list(current_streams)
        existing = set(current_streams)

        added: list[int] = []
        skipped: list[int] = []
        for sid in request.stream_ids:
            if sid in existing:
                skipped.append(sid)
            else:
                current_streams.append(sid)
                existing.add(sid)
                added.append(sid)

        if not added:
            logger.debug("[CHANNELS] No new streams to add to channel %s (all %d already present)",
                         channel_id, len(request.stream_ids))
            return {"channel": channel, "added": [], "skipped": skipped, "total_streams": len(current_streams)}

        result = await client.update_channel(channel_id, {"streams": current_streams})
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS] Added %d stream(s) to channel id=%s name=%s (%d skipped) in %.1fms",
                    len(added), channel_id, channel_name, len(skipped), elapsed_ms)

        journal.log_entry(
            category="channel",
            action_type="stream_add",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Added {len(added)} stream(s) to channel '{channel_name}'",
            before_value={"streams": before_streams},
            after_value={"streams": current_streams},
        )

        return {"channel": result, "added": added, "skipped": skipped, "total_streams": len(current_streams)}
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel/stream id surfaces as an upstream 4xx — map it to a
        # clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Add streams to channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to add streams to channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest, _admin=RequireAdminIfEnabled):
    """Remove a stream from a channel. Admin only (operator-only write, bd-v7n9f)."""
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
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — map it to a clean
        # 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Remove stream %s from channel %s rejected by Dispatcharr: %s", request.stream_id, channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to remove stream %s from channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/reorder-streams")
async def reorder_channel_streams(channel_id: int, request: ReorderStreamsRequest, _admin=RequireAdminIfEnabled):
    """Reorder streams within a channel.

    Admin only (operator-only write, bd-v7n9f).

    This endpoint REPLACES the channel's stream set with the supplied list, so
    the list must be a true reorder — a permutation of the channel's *current*
    streams. A partial list would silently DETACH the omitted streams (data
    loss). To guard against that (bd-1wq7z.3) we validate that the request is a
    permutation before writing: same set of ids, no missing, no unknown, no
    duplicates. Use add-stream / remove-stream to change membership.
    """
    logger.debug("[CHANNELS] POST /channels/%s/reorder-streams - stream_ids=%s", channel_id, request.stream_ids)
    client = get_client()
    try:
        # Get before state
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        before_streams = channel.get("streams", [])

        # Streams normally come back as bare ints, but defensively handle the
        # case where upstream returns stream objects ({"id": ...}).
        before_ids = [
            (s.get("id") if isinstance(s, dict) else s)
            for s in before_streams
        ]

        # --- Permutation guard (data-loss protection) ---
        requested = request.stream_ids
        requested_set = set(requested)
        before_set = set(before_ids)

        duplicate_ids = sorted({sid for sid in requested if requested.count(sid) > 1})
        missing_ids = sorted(before_set - requested_set)      # on channel, absent from list -> would detach
        unknown_ids = sorted(requested_set - before_set)      # in list, not on channel

        if duplicate_ids or missing_ids or unknown_ids:
            problems = []
            if missing_ids:
                problems.append(f"Missing (would be detached): {missing_ids}.")
            if unknown_ids:
                problems.append(f"Unknown (not on this channel): {unknown_ids}.")
            if duplicate_ids:
                problems.append(f"Duplicated: {duplicate_ids}.")
            detail = (
                "reorder-streams expects all of the channel's current streams in "
                "the desired order. " + " ".join(problems) + " Use "
                "remove_stream_from_channel to detach, or add_stream_to_channel "
                "to add."
            )
            logger.warning(
                "[CHANNELS] Rejected reorder-streams for channel %s (not a permutation): %s",
                channel_id, detail,
            )
            raise HTTPException(status_code=400, detail=detail)

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
    except HTTPException:
        # Validation rejections (e.g. the permutation guard above) are
        # intentional client errors — let them propagate unchanged rather than
        # masking them as a 500.
        raise
    except Exception as e:
        # A missing channel id (or a stream id not on the channel that slips past
        # the permutation guard) surfaces as an upstream 4xx — map it to a clean
        # 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Reorder streams for channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to reorder streams for channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Find & Merge Duplicate Channels
# =============================================================================


@router.post("/find-duplicates")
async def find_duplicate_channels(request: Optional[FindDuplicatesRequest] = None):
    """Scan channels and find duplicates by normalized name.

    Applies the user's normalization rules to every channel name,
    then groups channels that resolve to the same normalized name.
    Returns only groups with 2+ channels.

    Scope (enhancedchannelmanager-uahp6): when ``request.channel_ids`` is
    provided, the scan is restricted to those channel ids. Absent body /
    absent field = global scan across all channels (backward compatible —
    MCP and script callers that never send a body keep working unchanged).
    An explicit empty list scopes the scan to nothing (see
    ``FindDuplicatesRequest`` docstring).
    """
    scoped_ids: Optional[set[int]] = None
    if request is not None and request.channel_ids is not None:
        scoped_ids = set(request.channel_ids)
    # GH #645 / bead 0vao3: opt-in whitespace/case fold on the grouping key —
    # same canonicalization the auto-creation fold_match_key rule flag uses.
    fold = bool(request.fold_match_key) if request is not None else False

    logger.debug("[CHANNELS] POST /channels/find-duplicates scoped=%s fold=%s",
                 scoped_ids is not None, fold)
    from normalization_engine import get_normalization_engine

    client = get_client()
    session = get_session()

    try:
        engine = get_normalization_engine(session)

        # Fetch channels (paginated). Dispatcharr's list endpoint has no
        # id-filter, so a scoped scan still has to walk pages — but it filters
        # each page down to the requested ids and stops early once all of
        # them have been found, instead of walking the whole install for a
        # handful of selected channels.
        all_channels = []
        if scoped_ids is None:
            page = 1
            while True:
                result = await client.get_channels(page=page, page_size=500)
                batch = result.get("results", [])
                if not batch:
                    break
                all_channels.extend(batch)
                if not result.get("next"):
                    break
                page += 1
        elif scoped_ids:
            remaining = set(scoped_ids)
            page = 1
            while remaining:
                result = await client.get_channels(page=page, page_size=500)
                batch = result.get("results", [])
                if not batch:
                    break
                matched = [ch for ch in batch if ch.get("id") in remaining]
                all_channels.extend(matched)
                remaining -= {ch.get("id") for ch in matched}
                if not result.get("next"):
                    break
                page += 1
        # else: scoped_ids == set() — explicit empty scope, nothing to fetch.

        if scoped_ids is not None:
            logger.info(
                "[CHANNELS] find-duplicates: scanning %d channels (scoped to %d selected)",
                len(all_channels), len(scoped_ids),
            )
        else:
            logger.info("[CHANNELS] find-duplicates: scanning %d channels", len(all_channels))

        # Group by normalized name
        groups: dict[str, list[dict]] = {}
        for ch in all_channels:
            name = ch.get("name", "")
            norm_result = engine.normalize(name)
            normalized = norm_result.normalized.strip()
            if not normalized:
                continue

            key = fold_match_key(normalized) if fold else normalized.lower()
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "id": ch.get("id"),
                "name": name,
                "normalized_name": normalized,
                "channel_number": ch.get("channel_number"),
                "stream_count": len(ch.get("streams", [])),
                "channel_group_id": ch.get("channel_group_id"),
                "channel_group_name": ch.get("channel_group_name", ""),
            })

        # Filter to groups with duplicates
        duplicates = [
            {
                "normalized_name": channels[0]["normalized_name"],
                "channels": sorted(channels, key=lambda c: -(c["stream_count"] or 0)),
            }
            for channels in groups.values()
            if len(channels) >= 2
        ]

        # Sort by number of duplicates (worst first)
        duplicates.sort(key=lambda g: -len(g["channels"]))

        total_dup_channels = sum(len(g["channels"]) for g in duplicates)
        logger.info("[CHANNELS] find-duplicates: found %d duplicate groups (%d channels)",
                    len(duplicates), total_dup_channels)

        return {
            "groups": duplicates,
            "total_groups": len(duplicates),
            "total_duplicate_channels": total_dup_channels,
        }

    except Exception as e:
        logger.exception("[CHANNELS] find-duplicates failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("/bulk-merge")
async def bulk_merge_channels(request: BulkMergeRequest, _admin=RequireAdminIfEnabled):
    """Merge multiple groups of duplicate channels.

    For each merge item, keeps the target channel and moves all streams
    from source channels into it, then deletes the sources.

    Admin only (destructive bulk operator op — deletes channels, bd-um30y).
    """
    logger.debug("[CHANNELS] POST /channels/bulk-merge - %d merge groups", len(request.merges))

    client = get_client()
    results = []
    merged_count = 0
    failed_count = 0

    for item in request.merges:
        try:
            # Pre-validate the target ID the same way source IDs are validated
            # below: if the target no longer exists upstream (e.g., a stale
            # reference after a previous merge), surface 422 with the same
            # refresh hint instead of falling through to the per-item catch-all
            # (which returns 200 + a failed count). Consistent with the
            # source-ID path added in bd-ozhkf (bd-4xxax).
            try:
                target = await client.get_channel(item.target_channel_id)
            except httpx.HTTPStatusError as fetch_err:
                if fetch_err.response.status_code == 404:
                    logger.warning(
                        "[CHANNELS] bulk-merge: rejected — stale target ID %s no longer exists",
                        item.target_channel_id,
                    )
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Target channel {item.target_channel_id} no longer exists — "
                            "refresh the channels list and try again"
                        ),
                    )
                raise
            target_name = target.get("name", f"Channel {item.target_channel_id}")

            # Collect all streams from target + sources (deduplicated, target first).
            # Pre-validate source IDs before mutating anything: if any source no longer
            # exists upstream (e.g., stale ID from a previous merge), surface 422 with a
            # refresh hint instead of silently calling DELETE on a ghost row and producing
            # [DISPATCHARR] API request failed: DELETE 404 noise.  Mirror of the same
            # pattern in merge_channels (bd-ct9wl); applied here for the bulk path
            # (bd-ozhkf).
            all_streams: list[int] = []
            seen: set[int] = set()
            for sid in target.get("streams", []):
                if sid not in seen:
                    all_streams.append(sid)
                    seen.add(sid)

            source_names = []
            missing_ids: list[int] = []
            for src_id in item.source_channel_ids:
                try:
                    src = await client.get_channel(src_id)
                    source_names.append(src.get("name", f"Channel {src_id}"))
                    for sid in src.get("streams", []):
                        if sid not in seen:
                            all_streams.append(sid)
                            seen.add(sid)
                except httpx.HTTPStatusError as fetch_err:
                    if fetch_err.response.status_code == 404:
                        missing_ids.append(src_id)
                        # Keep source_names complete — one entry per requested
                        # source ID, in order. Harmless today because the 422
                        # branch below raises before the journal entry is
                        # written, but prevents a silently misaligned audit
                        # record if this ever becomes a partial-success batch
                        # (bd-4xxax).
                        source_names.append(f"Channel {src_id}")
                    else:
                        raise

            if missing_ids:
                logger.warning(
                    "[CHANNELS] bulk-merge: rejected — stale source IDs %s no longer exist",
                    missing_ids,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Source channels {missing_ids} no longer exist — "
                        "refresh the channels list and try again"
                    ),
                )

            # Update target with combined streams
            if all_streams:
                await client.update_channel(item.target_channel_id, {"streams": all_streams})

            # Delete source channels
            deleted = []
            for src_id in item.source_channel_ids:
                try:
                    await client.delete_channel(src_id)
                    deleted.append(src_id)
                except Exception as e:
                    logger.warning("[CHANNELS] bulk-merge: failed to delete source %s: %s", src_id, e)

            journal.log_entry(
                category="channel",
                action_type="bulk_merge",
                entity_id=item.target_channel_id,
                entity_name=target_name,
                description=f"Merged {len(item.source_channel_ids)} channels into '{target_name}'",
                before_value={"source_names": source_names},
                after_value={"stream_count": len(all_streams), "deleted_ids": deleted},
            )

            merged_count += 1
            results.append({
                "target_channel_id": item.target_channel_id,
                "target_name": target_name,
                "sources_deleted": len(deleted),
                "total_streams": len(all_streams),
                "success": True,
            })

        except HTTPException:
            raise
        except Exception as e:
            failed_count += 1
            # If the per-item failure is an upstream client error (e.g. a bad
            # target/source id), surface the actionable upstream detail so the
            # caller can tell "does not exist" from a real server fault, instead
            # of the bare exception type (bd-lq38l.4). For genuine server faults
            # we keep CodeQL py/stack-trace-exposure (#1413) hygiene: log the
            # full trace but only return the exception type name to the client.
            logger.exception(
                "[CHANNELS] bulk-merge: group failed (target=%s)",
                item.target_channel_id,
            )
            mapped = upstream_http_exception(e)
            error_detail = mapped.detail if mapped is not None else type(e).__name__
            results.append({
                "target_channel_id": item.target_channel_id,
                "success": False,
                "error": error_detail,
            })

    logger.info("[CHANNELS] bulk-merge complete: %d merged, %d failed", merged_count, failed_count)
    return {
        "merged": merged_count,
        "failed": failed_count,
        "results": results,
    }
