"""
M3U router — M3U account CRUD, upload, refresh, filters, profiles,
group settings, and server groups.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File

from cache import get_cache
from config import CONFIG_DIR, get_settings, save_settings
from database import get_session
from dispatcharr_client import get_client
from alert_methods import send_alert
from tasks.m3u_digest import send_immediate_digest
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/m3u", tags=["M3U"])

# Polling configuration for manual refresh endpoints
REFRESH_POLL_INTERVAL_SECONDS = 5
M3U_REFRESH_MAX_WAIT_SECONDS = 300  # 5 minutes for M3U


# -------------------------------------------------------------------------
# Helper functions (used only by M3U refresh endpoints)
# -------------------------------------------------------------------------

async def _capture_m3u_changes_after_refresh(account_id: int, account_name: str):
    """
    Capture M3U state changes after a refresh.

    Fetches current groups/streams for the account, compares with previous
    snapshot, and persists any detected changes.

    IMPORTANT: Gets ALL groups from the M3U source (not just enabled ones) by:
    1. Getting the M3U account which has channel_groups with group IDs
    2. Getting all channel groups to build ID -> name mapping
    3. Getting actual stream counts per group (only available for enabled groups)
    4. Merging: all groups get names, stream counts where available
    """
    from m3u_change_detector import M3UChangeDetector

    try:
        api_client = get_client()

        # Get the M3U account - channel_groups contains ALL groups from this M3U source
        account_data = await api_client.get_m3u_account(account_id)
        account_channel_groups = account_data.get("channel_groups", [])

        # Get all channel groups to build ID -> name mapping
        all_channel_groups = await api_client.get_channel_groups()
        group_lookup = {
            g["id"]: g["name"]
            for g in all_channel_groups
        }

        # Get actual stream counts (only available for enabled groups with imported streams)
        stream_counts = await api_client.get_stream_groups_with_counts(m3u_account_id=account_id)
        stream_count_lookup = {
            g["name"]: g["count"]
            for g in stream_counts
        }

        # Build list of enabled group names to fetch stream names for
        enabled_group_names = []
        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup and acg.get("enabled", False):
                enabled_group_names.append(group_lookup[group_id])

        # Fetch stream names for enabled groups (limit to first 50 per group)
        stream_names_by_group = {}
        MAX_STREAM_NAMES = 500
        logger.info("[M3U-CHANGE] Fetching stream names for %s enabled groups: %s%s", len(enabled_group_names), enabled_group_names[:5], '...' if len(enabled_group_names) > 5 else '')
        for group_name in enabled_group_names:
            try:
                streams_response = await api_client.get_streams(
                    page=1,
                    page_size=MAX_STREAM_NAMES,
                    channel_group_name=group_name,
                    m3u_account=account_id,
                )
                results = streams_response.get("results", [])
                stream_names = [s.get("name", "") for s in results]
                logger.debug("[M3U-CHANGE] Group '%s': got %s streams, %s names", group_name, len(results), len(stream_names))
                if stream_names:
                    stream_names_by_group[group_name] = stream_names
            except Exception as e:
                logger.warning("[M3U-CHANGE] Could not fetch streams for group '%s': %s", group_name, e)

        logger.info("[M3U-CHANGE] Captured stream names for %s groups", len(stream_names_by_group))

        # Match up: for each group in this M3U account, get name and stream count
        current_groups = []
        total_streams = 0

        for acg in account_channel_groups:
            group_id = acg.get("channel_group")
            if group_id and group_id in group_lookup:
                group_name = group_lookup[group_id]
                # Get stream count if available (only for enabled groups), otherwise 0
                stream_count = stream_count_lookup.get(group_name, 0)
                enabled = acg.get("enabled", False)
                current_groups.append({
                    "name": group_name,
                    "stream_count": stream_count,
                    "enabled": enabled,
                })
                total_streams += stream_count

        logger.info(
            "[M3U-CHANGE] Capturing state for account %s (%s): "
            "%s groups, %s streams (all groups from M3U)",
            account_id, account_name,
            len(current_groups), total_streams
        )

        # Use change detector to compare and persist
        db = get_session()
        try:
            detector = M3UChangeDetector(db)
            change_set = detector.detect_changes(
                m3u_account_id=account_id,
                current_groups=current_groups,
                current_total_streams=total_streams,
                stream_names_by_group=stream_names_by_group,
            )

            if change_set.has_changes:
                # Persist the changes
                detector.persist_changes(change_set)
                logger.info(
                    "[M3U-CHANGE] Detected and persisted changes for %s: "
                    "+%s groups, -%s groups, "
                    "+%s streams, "
                    "-%s streams",
                    account_name,
                    len(change_set.groups_added), len(change_set.groups_removed),
                    sum(s.count for s in change_set.streams_added),
                    sum(s.count for s in change_set.streams_removed)
                )
            else:
                logger.debug("[M3U-CHANGE] No changes detected for %s", account_name)
        finally:
            db.close()

    except Exception as e:
        logger.exception("[M3U-CHANGE] Failed to capture changes for %s: %s", account_name, e)


async def _poll_m3u_refresh_completion(account_id: int, account_name: str, initial_updated):
    """
    Background task to poll Dispatcharr until M3U refresh completes.

    Polls every REFRESH_POLL_INTERVAL_SECONDS for up to M3U_REFRESH_MAX_WAIT_SECONDS.
    Sends success notification when updated_at changes, warning on timeout.
    """
    from datetime import datetime

    client = get_client()
    wait_start = datetime.utcnow()

    try:
        while True:
            elapsed = (datetime.utcnow() - wait_start).total_seconds()
            if elapsed >= M3U_REFRESH_MAX_WAIT_SECONDS:
                logger.warning("[M3U-REFRESH] Timeout waiting for '%s' refresh after %.0fs", account_name, elapsed)
                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"M3U refresh for '{account_name}' timed out after {int(elapsed)}s - refresh may still be in progress",
                    notification_type="warning",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name, "timeout": True},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )
                return

            await asyncio.sleep(REFRESH_POLL_INTERVAL_SECONDS)

            try:
                current_account = await client.get_m3u_account(account_id)
            except Exception as e:
                # Account may have been deleted during refresh
                logger.warning("[M3U-REFRESH] Could not fetch account %s during polling: %s", account_id, e)
                return

            current_updated = current_account.get("updated_at") or current_account.get("last_refresh")

            if current_updated and current_updated != initial_updated:
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info("[M3U-REFRESH] '%s' refresh complete in %.1fs", account_name, wait_duration)

                # Capture M3U changes after refresh
                await _capture_m3u_changes_after_refresh(account_id, account_name)

                # Send immediate digest if configured
                try:
                    await send_immediate_digest(account_id)
                except Exception as e:
                    logger.warning("[M3U-REFRESH] Failed to send immediate digest for '%s': %s", account_name, e)

                journal.log_entry(
                    category="m3u",
                    action_type="refresh",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Refreshed M3U account '{account_name}' in {wait_duration:.1f}s",
                )

                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"Successfully refreshed M3U account '{account_name}' in {wait_duration:.1f}s",
                    notification_type="success",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name, "duration": wait_duration},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )

                # Run auto-creation rules if any have run_on_refresh=True
                try:
                    from tasks.auto_creation import run_auto_creation_after_refresh
                    await run_auto_creation_after_refresh(
                        m3u_account_ids=[account_id],
                        triggered_by="m3u_refresh",
                    )
                except Exception as e:
                    logger.warning("[M3U-REFRESH] Auto-creation after refresh failed: %s", e)

                return
            elif elapsed > 30 and not initial_updated:
                # After 30 seconds, assume complete if no timestamp field available
                wait_duration = (datetime.utcnow() - wait_start).total_seconds()
                logger.info("[M3U-REFRESH] '%s' - assuming complete after %.0fs (no timestamp field)", account_name, wait_duration)

                # Capture M3U changes after refresh
                await _capture_m3u_changes_after_refresh(account_id, account_name)

                # Send immediate digest if configured
                try:
                    await send_immediate_digest(account_id)
                except Exception as e:
                    logger.warning("[M3U-REFRESH] Failed to send immediate digest for '%s': %s", account_name, e)

                journal.log_entry(
                    category="m3u",
                    action_type="refresh",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Refreshed M3U account '{account_name}'",
                )

                await send_alert(
                    title=f"M3U Refresh: {account_name}",
                    message=f"M3U account '{account_name}' refresh completed",
                    notification_type="success",
                    source="M3U Refresh",
                    metadata={"account_id": account_id, "account_name": account_name},
                    alert_category="m3u_refresh",
                    entity_id=account_id,
                )

                # Run auto-creation rules if any have run_on_refresh=True
                try:
                    from tasks.auto_creation import run_auto_creation_after_refresh
                    await run_auto_creation_after_refresh(
                        m3u_account_ids=[account_id],
                        triggered_by="m3u_refresh",
                    )
                except Exception as e:
                    logger.warning("[M3U-REFRESH] Auto-creation after refresh failed: %s", e)

                return

    except Exception as e:
        logger.exception("[M3U-REFRESH] Error polling for '%s' completion: %s", account_name, e)


# -------------------------------------------------------------------------
# M3U Account Management
# -------------------------------------------------------------------------

@router.get("/accounts/{account_id}")
async def get_m3u_account(account_id: int):
    """Get a single M3U account by ID."""
    logger.debug("[M3U] GET /api/m3u/accounts/%s", account_id)
    client = get_client()
    start = time.time()
    try:
        result = await client.get_m3u_account(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched M3U account id=%s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/accounts/{account_id}/stream-metadata")
async def get_m3u_stream_metadata(account_id: int):
    """Fetch and parse M3U file to extract stream metadata (tvg-id -> tvc-guide-stationid mapping).

    This parses the M3U file directly to get attributes like tvc-guide-stationid
    that Dispatcharr doesn't expose via its API.
    """
    logger.debug("[M3U] GET /api/m3u/accounts/%s/stream-metadata", account_id)
    client = get_client()
    try:
        # Get the M3U account details
        start = time.time()
        account = await client.get_m3u_account(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched M3U account %s in %.1fms", account_id, elapsed_ms)

        # Construct the M3U URL based on account type
        account_type = account.get("account_type", "M3U")
        server_url = account.get("server_url")

        if not server_url:
            raise HTTPException(status_code=400, detail="M3U account has no server URL")

        if account_type == "XC":
            # XtreamCodes: construct M3U URL from credentials
            username = account.get("username", "")
            password = account.get("password", "")
            # Remove trailing slash from server_url if present
            base_url = server_url.rstrip("/")
            m3u_url = f"{base_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
        else:
            # Standard M3U: server_url is the direct URL
            m3u_url = server_url

        # Fetch the M3U file
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            response = await http_client.get(m3u_url, follow_redirects=True)
            response.raise_for_status()
            m3u_content = response.text

        # Parse EXTINF lines to extract metadata
        # Format: #EXTINF:-1 tvg-id="ID" tvc-guide-stationid="12345" ...,Channel Name
        metadata = {}

        # Regex to match key="value" or key=value patterns in EXTINF lines
        attr_pattern = re.compile(r'([\w-]+)=["\']?([^"\'>\s,]+)["\']?')

        lines = m3u_content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                # Extract all attributes from the EXTINF line
                attrs = dict(attr_pattern.findall(line))

                tvg_id = attrs.get('tvg-id')
                tvc_station_id = attrs.get('tvc-guide-stationid')

                # Only include entries that have a tvg-id (needed for matching)
                if tvg_id:
                    entry = {}
                    if tvc_station_id:
                        entry['tvc-guide-stationid'] = tvc_station_id
                    # Include other useful attributes
                    if 'tvg-name' in attrs:
                        entry['tvg-name'] = attrs['tvg-name']
                    if 'tvg-logo' in attrs:
                        entry['tvg-logo'] = attrs['tvg-logo']
                    if 'group-title' in attrs:
                        entry['group-title'] = attrs['group-title']

                    if entry:  # Only add if we have at least one attribute
                        metadata[tvg_id] = entry

        logger.info("[M3U] Parsed M3U metadata for account %s: %s entries with tvg-id", account_id, len(metadata))
        return {"metadata": metadata, "count": len(metadata)}

    except httpx.HTTPError as e:
        logger.error("[M3U] Failed to fetch M3U file for account %s: %s", account_id, e)
        raise HTTPException(status_code=502, detail=f"Failed to fetch M3U file: {str(e)}")
    except Exception as e:
        logger.exception("[M3U] Failed to parse M3U metadata for account %s: %s", account_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts")
async def create_m3u_account(request: Request):
    """Create a new M3U account."""
    logger.debug("[M3U] POST /api/m3u/accounts")
    client = get_client()
    start = time.time()
    try:
        data = await request.json()
        result = await client.create_m3u_account(data)
        elapsed_ms = (time.time() - start) * 1000

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", data.get("name", "Unknown")),
            description=f"Created M3U account '{result.get('name', data.get('name'))}'",
            after_value={"name": result.get("name"), "server_url": data.get("server_url")},
        )

        logger.info("[M3U] Created M3U account id=%s name='%s' in %.1fms", result.get("id"), result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/upload")
async def upload_m3u_file(file: UploadFile = File(...)):
    """Upload an M3U file and return the path for use with M3U accounts.

    The file is saved to /config/m3u_uploads/ directory.
    Returns the full path that can be used as file_path when creating/updating M3U accounts.
    """
    logger.debug("[M3U] POST /api/m3u/upload - filename=%s", file.filename)
    import aiofiles
    from pathlib import Path
    import uuid

    # Create uploads directory if it doesn't exist
    uploads_dir = CONFIG_DIR / "m3u_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Validate file extension
    original_name = file.filename or "upload.m3u"
    if not original_name.lower().endswith(('.m3u', '.m3u8')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only .m3u and .m3u8 files are allowed."
        )

    # Create a unique filename to avoid collisions
    # Use original name with a short UUID prefix for uniqueness
    safe_name = re.sub(r'[^\w\-_\.]', '_', original_name)
    unique_prefix = str(uuid.uuid4())[:8]
    final_name = f"{unique_prefix}_{safe_name}"
    file_path = uploads_dir / final_name

    try:
        # Read and save the file
        content = await file.read()
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)

        logger.info("[M3U] M3U file uploaded: %s (%s bytes)", file_path, len(content))

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="upload",
            entity_name=original_name,
            description=f"Uploaded M3U file '{original_name}' ({len(content)} bytes)",
        )

        return {
            "file_path": str(file_path),
            "original_name": original_name,
            "size": len(content)
        }
    except Exception as e:
        logger.exception("[M3U] Failed to upload M3U file: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")


@router.put("/accounts/{account_id}")
async def update_m3u_account(account_id: int, request: Request):
    """Update an M3U account (full update)."""
    logger.debug("[M3U] PUT /api/m3u/accounts/%s", account_id)
    client = get_client()
    start = time.time()
    try:
        before_account = await client.get_m3u_account(account_id)
        data = await request.json()
        result = await client.update_m3u_account(account_id, data)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="update",
            entity_id=account_id,
            entity_name=result.get("name", before_account.get("name", "Unknown")),
            description=f"Updated M3U account '{result.get('name', before_account.get('name'))}'",
            before_value={"name": before_account.get("name")},
            after_value={"name": data.get("name")},
        )

        elapsed_ms = (time.time() - start) * 1000
        logger.info("[M3U] Updated M3U account id=%s name='%s' in %.1fms", account_id, result.get("name"), elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/accounts/{account_id}")
async def patch_m3u_account(account_id: int, request: Request):
    """Partially update an M3U account (e.g., toggle is_active)."""
    logger.debug("[M3U] PATCH /api/m3u/accounts/%s", account_id)
    client = get_client()
    try:
        start = time.time()
        before_account = await client.get_m3u_account(account_id)
        data = await request.json()
        result = await client.patch_m3u_account(account_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Patched M3U account %s in %.1fms", account_id, elapsed_ms)

        # Log to journal
        changes = []
        if "is_active" in data:
            changes.append(f"{'enabled' if data['is_active'] else 'disabled'}")
        if "name" in data:
            changes.append(f"renamed to '{data['name']}'")

        if changes:
            journal.log_entry(
                category="m3u",
                action_type="update",
                entity_id=account_id,
                entity_name=result.get("name", before_account.get("name", "Unknown")),
                description=f"M3U account {', '.join(changes)}",
                before_value={"is_active": before_account.get("is_active")},
                after_value=data,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/accounts/{account_id}")
async def delete_m3u_account(account_id: int, delete_groups: bool = True):
    """Delete an M3U account and optionally its associated channel groups.

    Args:
        account_id: The M3U account ID to delete
        delete_groups: If True (default), also delete orphaned channel groups
                       (groups not referenced by any other M3U account)
    """
    logger.debug("[M3U] DELETE /api/m3u/accounts/%s - delete_groups=%s", account_id, delete_groups)
    client = get_client()
    try:
        # Get account info before deleting (includes channel_groups)
        start = time.time()
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")

        # Extract channel group IDs associated with this M3U account
        channel_group_ids = []
        shared_group_ids = set()
        if delete_groups:
            for group_setting in account.get("channel_groups", []):
                group_id = group_setting.get("channel_group")
                if group_id:
                    channel_group_ids.append(group_id)
            logger.info("[M3U] M3U account '%s' has %s associated channel groups", account_name, len(channel_group_ids))

            # Check which groups are shared with other M3U accounts
            if channel_group_ids:
                all_accounts = await client.get_m3u_accounts()
                group_id_set = set(channel_group_ids)
                for other_account in all_accounts:
                    if other_account.get("id") == account_id:
                        continue
                    for gs in other_account.get("channel_groups", []):
                        gid = gs.get("channel_group")
                        if gid in group_id_set:
                            shared_group_ids.add(gid)
                if shared_group_ids:
                    logger.info("[M3U] %s groups shared with other accounts, will not delete: %s",
                                len(shared_group_ids), sorted(shared_group_ids))

        # Delete the M3U account first
        await client.delete_m3u_account(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Deleted M3U account %s in %.1fms", account_id, elapsed_ms)

        # Invalidate caches - streams from this M3U are now gone
        cache = get_cache()
        streams_cleared = cache.invalidate_prefix("streams:")
        groups_cleared = cache.invalidate("channel_groups")
        logger.info("[M3U] Invalidated cache after M3U deletion: %s stream entries, channel_groups=%s", streams_cleared, groups_cleared)

        # Only delete orphaned groups (not referenced by any other account)
        deleted_groups = []
        failed_groups = []
        skipped_groups = []
        if delete_groups and channel_group_ids:
            for group_id in channel_group_ids:
                if group_id in shared_group_ids:
                    skipped_groups.append(group_id)
                    logger.info("[M3U] Skipped deletion of shared channel group %s", group_id)
                    continue
                try:
                    await client.delete_channel_group(group_id)
                    deleted_groups.append(group_id)
                    logger.info("[M3U] Deleted orphaned channel group %s (was associated with M3U '%s')", group_id, account_name)
                except Exception as group_err:
                    # Group might have channels or other issues - log but don't fail
                    failed_groups.append({"id": group_id, "error": str(group_err)})
                    logger.warning("[M3U] Failed to delete channel group %s: %s", group_id, group_err)

        # Clean up linked_m3u_accounts in settings
        try:
            settings = get_settings()
            if settings.linked_m3u_accounts:
                cleaned = []
                for link_group in settings.linked_m3u_accounts:
                    filtered = [aid for aid in link_group if aid != account_id]
                    # Only keep groups with 2+ accounts
                    if len(filtered) >= 2:
                        cleaned.append(filtered)
                if cleaned != settings.linked_m3u_accounts:
                    settings.linked_m3u_accounts = cleaned
                    save_settings(settings)
                    logger.info("[M3U] Cleaned up linked_m3u_accounts after deleting account %s", account_id)
        except Exception as settings_err:
            logger.warning("[M3U] Failed to clean up linked_m3u_accounts: %s", settings_err)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="delete",
            entity_id=account_id,
            entity_name=account_name,
            description=f"Deleted M3U account '{account_name}'" +
                       (f" and {len(deleted_groups)} orphaned channel groups" if deleted_groups else "") +
                       (f" (kept {len(skipped_groups)} shared groups)" if skipped_groups else ""),
            before_value={
                "name": account_name,
                "channel_groups": channel_group_ids,
            },
            after_value={
                "deleted_groups": deleted_groups,
                "skipped_groups": skipped_groups,
                "failed_groups": failed_groups,
            } if channel_group_ids else None,
        )

        return {
            "status": "deleted",
            "deleted_groups": deleted_groups,
            "skipped_groups": skipped_groups,
            "failed_groups": failed_groups,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------------------------------------------------
# M3U Refresh
# -------------------------------------------------------------------------

@router.post("/refresh")
async def refresh_all_m3u_accounts():
    """Trigger refresh for all active M3U accounts."""
    logger.debug("[M3U-REFRESH] POST /api/m3u/refresh")
    client = get_client()
    start = time.time()
    try:
        result = await client.refresh_all_m3u_accounts()
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[M3U-REFRESH] Triggered refresh for all M3U accounts in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/refresh/{account_id}")
async def refresh_m3u_account(account_id: int):
    """Trigger refresh for a single M3U account.

    Triggers the refresh and spawns a background task to poll for completion.
    Success notification is sent only when refresh actually completes.
    """
    logger.debug("[M3U-REFRESH] POST /api/m3u/refresh/%s", account_id)
    client = get_client()
    try:
        # Get account info and capture initial state for polling
        start = time.time()
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")
        initial_updated = account.get("updated_at") or account.get("last_refresh")

        # Trigger the refresh (returns immediately, refresh happens in background)
        result = await client.refresh_m3u_account(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U-REFRESH] Triggered refresh for account %s in %.1fms", account_id, elapsed_ms)

        # Spawn background task to poll for completion and send notification
        asyncio.create_task(
            _poll_m3u_refresh_completion(account_id, account_name, initial_updated)
        )

        logger.info("[M3U-REFRESH] Triggered refresh for '%s', polling for completion in background", account_name)
        return result
    except Exception as e:
        # Send error notification for trigger failure
        try:
            await send_alert(
                title="M3U Refresh Failed",
                message=f"Failed to trigger M3U refresh for account (ID: {account_id}): {str(e)}",
                notification_type="error",
                source="M3U Refresh",
                metadata={"account_id": account_id, "error": str(e)},
                alert_category="m3u_refresh",
                entity_id=account_id,
            )
        except Exception:
            pass  # Don't fail the request if notification fails
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts/{account_id}/refresh-vod")
async def refresh_m3u_vod(account_id: int):
    """Refresh VOD content for an XtreamCodes account."""
    logger.debug("[M3U-REFRESH] POST /api/m3u/accounts/%s/refresh-vod", account_id)
    client = get_client()
    start = time.time()
    try:
        result = await client.refresh_m3u_vod(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[M3U-REFRESH] Triggered VOD refresh for account %s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------------------------------------------------
# M3U Filters
# -------------------------------------------------------------------------

@router.get("/accounts/{account_id}/filters")
async def get_m3u_filters(account_id: int):
    """Get all filters for an M3U account."""
    logger.debug("[M3U] GET /api/m3u/accounts/%s/filters", account_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_m3u_filters(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched filters for account %s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts/{account_id}/filters")
async def create_m3u_filter(account_id: int, request: Request):
    """Create a new filter for an M3U account."""
    logger.debug("[M3U] POST /api/m3u/accounts/%s/filters", account_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.create_m3u_filter(account_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Created filter for account %s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/accounts/{account_id}/filters/{filter_id}")
async def update_m3u_filter(account_id: int, filter_id: int, request: Request):
    """Update a filter for an M3U account."""
    logger.debug("[M3U] PUT /api/m3u/accounts/%s/filters/%s", account_id, filter_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.update_m3u_filter(account_id, filter_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Updated filter %s for account %s in %.1fms", filter_id, account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/accounts/{account_id}/filters/{filter_id}")
async def delete_m3u_filter(account_id: int, filter_id: int):
    """Delete a filter from an M3U account."""
    logger.debug("[M3U] DELETE /api/m3u/accounts/%s/filters/%s", account_id, filter_id)
    client = get_client()
    try:
        start = time.time()
        await client.delete_m3u_filter(account_id, filter_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Deleted filter %s for account %s in %.1fms", filter_id, account_id, elapsed_ms)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------------------------------------------------
# M3U Profiles
# -------------------------------------------------------------------------

@router.get("/accounts/{account_id}/profiles/")
async def get_m3u_profiles(account_id: int):
    """Get all profiles for an M3U account."""
    logger.debug("[M3U] GET /api/m3u/accounts/%s/profiles", account_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_m3u_profiles(account_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched profiles for account %s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/accounts/{account_id}/profiles/")
async def create_m3u_profile(account_id: int, request: Request):
    """Create a new profile for an M3U account."""
    logger.debug("[M3U] POST /api/m3u/accounts/%s/profiles", account_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.create_m3u_profile(account_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Created profile for account %s in %.1fms", account_id, elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/accounts/{account_id}/profiles/{profile_id}/")
async def get_m3u_profile(account_id: int, profile_id: int):
    """Get a specific profile for an M3U account."""
    logger.debug("[M3U] GET /api/m3u/accounts/%s/profiles/%s", account_id, profile_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_m3u_profile(account_id, profile_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched profile %s for account %s in %.1fms", profile_id, account_id, elapsed_ms)
        return result
    except Exception as e:
        logger.warning("[M3U] Failed to fetch profile %s for account %s: %s", profile_id, account_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/accounts/{account_id}/profiles/{profile_id}/")
async def update_m3u_profile(account_id: int, profile_id: int, request: Request):
    """Update a profile for an M3U account."""
    logger.debug("[M3U] PATCH /api/m3u/accounts/%s/profiles/%s", account_id, profile_id)
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.update_m3u_profile(account_id, profile_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Updated profile %s for account %s in %.1fms", profile_id, account_id, elapsed_ms)
        return result
    except Exception as e:
        logger.warning("[M3U] Failed to update profile %s for account %s: %s", profile_id, account_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/accounts/{account_id}/profiles/{profile_id}/")
async def delete_m3u_profile(account_id: int, profile_id: int):
    """Delete a profile from an M3U account."""
    logger.debug("[M3U] DELETE /api/m3u/accounts/%s/profiles/%s", account_id, profile_id)
    client = get_client()
    try:
        start = time.time()
        await client.delete_m3u_profile(account_id, profile_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Deleted profile %s for account %s in %.1fms", profile_id, account_id, elapsed_ms)
        return {"status": "deleted"}
    except Exception as e:
        logger.warning("[M3U] Failed to delete profile %s for account %s: %s", profile_id, account_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# M3U Group Settings
# -------------------------------------------------------------------------

@router.patch("/accounts/{account_id}/group-settings")
async def update_m3u_group_settings(account_id: int, request: Request):
    """Update group settings for an M3U account."""
    logger.debug("[M3U] PATCH /api/m3u/accounts/%s/group-settings", account_id)
    client = get_client()
    try:
        # Get account info and current group settings before update
        start = time.time()
        account = await client.get_m3u_account(account_id)
        account_name = account.get("name", "Unknown")
        # Store full settings for each group (all auto-sync related fields)
        before_groups = {}
        for g in account.get("channel_groups", []):
            before_groups[g.get("channel_group")] = {
                "enabled": g.get("enabled"),
                "auto_channel_sync": g.get("auto_channel_sync"),
                "auto_sync_channel_start": g.get("auto_sync_channel_start"),
                "custom_properties": g.get("custom_properties"),
            }

        # Get channel groups for name lookup
        channel_groups = await client.get_channel_groups()
        group_name_map = {g["id"]: g["name"] for g in channel_groups}

        data = await request.json()
        result = await client.update_m3u_group_settings(account_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Updated group settings for account %s in %.1fms", account_id, elapsed_ms)

        # Log to journal - compare before/after states for all settings
        group_settings = data.get("group_settings", [])
        if group_settings:
            enabled_names = []
            disabled_names = []
            auto_sync_enabled_names = []
            auto_sync_disabled_names = []
            start_channel_changed = []
            settings_changed_names = []
            changed_groups = []

            for gs in group_settings:
                channel_group_id = gs.get("channel_group")
                before = before_groups.get(channel_group_id, {})
                group_name = group_name_map.get(channel_group_id, f"Group {channel_group_id}")

                changes_for_group = {}

                # Check enabled change
                new_enabled = gs.get("enabled")
                old_enabled = before.get("enabled")
                if old_enabled is not None and new_enabled != old_enabled:
                    if new_enabled:
                        enabled_names.append(group_name)
                    else:
                        disabled_names.append(group_name)
                    changes_for_group["enabled"] = {"was": old_enabled, "now": new_enabled}

                # Check auto_channel_sync change
                new_auto_sync = gs.get("auto_channel_sync")
                old_auto_sync = before.get("auto_channel_sync")
                if old_auto_sync is not None and new_auto_sync != old_auto_sync:
                    if new_auto_sync:
                        auto_sync_enabled_names.append(group_name)
                    else:
                        auto_sync_disabled_names.append(group_name)
                    changes_for_group["auto_channel_sync"] = {"was": old_auto_sync, "now": new_auto_sync}

                # Check auto_sync_channel_start change
                new_start = gs.get("auto_sync_channel_start")
                old_start = before.get("auto_sync_channel_start")
                if old_start != new_start:
                    start_channel_changed.append(f"{group_name} ({old_start} → {new_start})")
                    changes_for_group["auto_sync_channel_start"] = {"was": old_start, "now": new_start}

                # Check custom_properties change
                # Normalize empty dict and None to be equivalent
                new_custom = gs.get("custom_properties")
                old_custom = before.get("custom_properties")
                # Treat empty dict {} as equivalent to None
                new_custom_normalized = new_custom if new_custom else None
                old_custom_normalized = old_custom if old_custom else None
                if old_custom_normalized != new_custom_normalized:
                    settings_changed_names.append(group_name)
                    changes_for_group["custom_properties"] = {"was": old_custom, "now": new_custom}

                if changes_for_group:
                    changed_groups.append({
                        "channel_group": channel_group_id,
                        "name": group_name,
                        "changes": changes_for_group,
                    })

            if changed_groups:
                changes = []
                if enabled_names:
                    changes.append(f"Enabled: {', '.join(enabled_names)}")
                if disabled_names:
                    changes.append(f"Disabled: {', '.join(disabled_names)}")
                if auto_sync_enabled_names:
                    changes.append(f"Auto-sync on: {', '.join(auto_sync_enabled_names)}")
                if auto_sync_disabled_names:
                    changes.append(f"Auto-sync off: {', '.join(auto_sync_disabled_names)}")
                if start_channel_changed:
                    changes.append(f"Start channel: {', '.join(start_channel_changed)}")
                if settings_changed_names:
                    changes.append(f"Settings: {', '.join(settings_changed_names)}")

                # Only include before state for groups that actually changed
                changed_group_ids = {g["channel_group"] for g in changed_groups}
                before_changed_only = {
                    gid: {**before_groups[gid], "name": group_name_map.get(gid, f"Group {gid}")}
                    for gid in changed_group_ids
                    if gid in before_groups
                }

                journal.log_entry(
                    category="m3u",
                    action_type="update",
                    entity_id=account_id,
                    entity_name=account_name,
                    description=f"Updated group settings - {'; '.join(changes)}",
                    before_value=before_changed_only,
                    after_value=changed_groups,
                )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------------------------------------------------
# Server Groups
# -------------------------------------------------------------------------

@router.get("/server-groups")
async def get_server_groups():
    """Get all server groups."""
    logger.debug("[M3U] GET /api/m3u/server-groups")
    client = get_client()
    try:
        start = time.time()
        result = await client.get_server_groups()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Fetched server groups in %.1fms", elapsed_ms)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/server-groups")
async def create_server_group(request: Request):
    """Create a new server group."""
    logger.debug("[M3U] POST /api/m3u/server-groups")
    client = get_client()
    try:
        data = await request.json()
        start = time.time()
        result = await client.create_server_group(data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Created server group in %.1fms", elapsed_ms)

        # Log to journal
        group_name = data.get("name", "Unknown")
        account_ids = data.get("account_ids", [])
        journal.log_entry(
            category="m3u",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=group_name,
            description=f"Created server group '{group_name}' linking {len(account_ids)} M3U account(s)",
            after_value={"name": group_name, "account_ids": account_ids},
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/server-groups/{group_id}")
async def update_server_group(group_id: int, request: Request):
    """Update a server group."""
    logger.debug("[M3U] PATCH /api/m3u/server-groups/%s", group_id)
    client = get_client()
    try:
        # Get current group info
        start = time.time()
        groups = await client.get_server_groups()
        before_group = next((g for g in groups if g.get("id") == group_id), {})
        before_name = before_group.get("name", "Unknown")

        data = await request.json()
        result = await client.update_server_group(group_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Updated server group %s in %.1fms", group_id, elapsed_ms)

        # Log to journal
        new_name = data.get("name", before_name)
        account_ids = data.get("account_ids", [])

        changes = []
        if "name" in data and data["name"] != before_name:
            changes.append(f"renamed to '{new_name}'")
        if "account_ids" in data:
            changes.append(f"updated to {len(account_ids)} M3U account(s)")

        if changes:
            journal.log_entry(
                category="m3u",
                action_type="update",
                entity_id=group_id,
                entity_name=new_name,
                description=f"Updated server group: {', '.join(changes)}",
                before_value={"name": before_name, "account_ids": before_group.get("account_ids", [])},
                after_value=data,
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/server-groups/{group_id}")
async def delete_server_group(group_id: int):
    """Delete a server group."""
    logger.debug("[M3U] DELETE /api/m3u/server-groups/%s", group_id)
    client = get_client()
    try:
        # Get group info before deleting
        start = time.time()
        groups = await client.get_server_groups()
        group = next((g for g in groups if g.get("id") == group_id), {})
        group_name = group.get("name", "Unknown")

        await client.delete_server_group(group_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[M3U] Deleted server group %s in %.1fms", group_id, elapsed_ms)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="delete",
            entity_id=group_id,
            entity_name=group_name,
            description=f"Deleted server group '{group_name}'",
            before_value={"name": group_name, "account_ids": group.get("account_ids", [])},
        )

        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
