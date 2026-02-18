"""
M3U Digest router — M3U change tracking, snapshots, digest settings,
and test digest delivery.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
import re
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_session
import journal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["M3U Digest"])


# -------------------------------------------------------------------------
# Pydantic models
# -------------------------------------------------------------------------

class M3UDigestSettingsUpdate(BaseModel):
    """Request model for updating M3U digest settings."""
    enabled: Optional[bool] = None
    frequency: Optional[str] = None  # immediate, hourly, daily, weekly
    email_recipients: Optional[List[str]] = None
    include_group_changes: Optional[bool] = None
    include_stream_changes: Optional[bool] = None
    show_detailed_list: Optional[bool] = None  # Show detailed list vs just summary
    min_changes_threshold: Optional[int] = None
    send_to_discord: Optional[bool] = None  # Send digest to Discord (uses shared webhook)
    exclude_group_patterns: Optional[List[str]] = None  # Regex patterns to exclude groups
    exclude_stream_patterns: Optional[List[str]] = None  # Regex patterns to exclude streams


# -------------------------------------------------------------------------
# M3U Change Tracking API
# -------------------------------------------------------------------------

@router.get("/api/m3u/changes")
async def get_m3u_changes(
    page: int = 1,
    page_size: int = 50,
    m3u_account_id: Optional[int] = None,
    change_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """
    Get paginated list of M3U change logs.

    Args:
        page: Page number (1-indexed)
        page_size: Number of items per page
        m3u_account_id: Filter by M3U account ID
        change_type: Filter by change type (group_added, group_removed, streams_added, streams_removed)
        enabled: Filter by enabled status (true/false)
        sort_by: Column to sort by (change_time, m3u_account_id, change_type, group_name, count, enabled)
        sort_order: Sort order (asc or desc, default: desc)
        date_from: Filter changes from this date (ISO format)
        date_to: Filter changes until this date (ISO format)
    """
    from datetime import datetime as dt
    from models import M3UChangeLog

    db = get_session()
    try:
        query = db.query(M3UChangeLog)

        # Apply filters
        if m3u_account_id:
            query = query.filter(M3UChangeLog.m3u_account_id == m3u_account_id)
        if change_type:
            query = query.filter(M3UChangeLog.change_type == change_type)
        if enabled is not None:
            query = query.filter(M3UChangeLog.enabled == enabled)
        if date_from:
            try:
                date_from_dt = dt.fromisoformat(date_from.replace("Z", "+00:00"))
                query = query.filter(M3UChangeLog.change_time >= date_from_dt)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_dt = dt.fromisoformat(date_to.replace("Z", "+00:00"))
                query = query.filter(M3UChangeLog.change_time <= date_to_dt)
            except ValueError:
                pass

        # Get total count
        total = query.count()

        # Apply sorting
        sort_columns = {
            "change_time": M3UChangeLog.change_time,
            "m3u_account_id": M3UChangeLog.m3u_account_id,
            "change_type": M3UChangeLog.change_type,
            "group_name": M3UChangeLog.group_name,
            "count": M3UChangeLog.count,
            "enabled": M3UChangeLog.enabled,
        }
        sort_column = sort_columns.get(sort_by, M3UChangeLog.change_time)
        if sort_order == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        # Apply pagination
        changes = (
            query.offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "results": [c.to_dict() for c in changes],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }
    finally:
        db.close()


@router.get("/api/m3u/changes/summary")
async def get_m3u_changes_summary(
    hours: int = 24,
    m3u_account_id: Optional[int] = None,
):
    """
    Get aggregated summary of M3U changes.

    Args:
        hours: Look back this many hours (default: 24)
        m3u_account_id: Filter by M3U account ID
    """
    from datetime import datetime as dt, timedelta
    from m3u_change_detector import M3UChangeDetector

    db = get_session()
    try:
        detector = M3UChangeDetector(db)
        since = dt.utcnow() - timedelta(hours=hours)
        summary = detector.get_change_summary(since, m3u_account_id)
        return summary
    finally:
        db.close()


@router.get("/api/m3u/accounts/{account_id}/changes")
async def get_m3u_account_changes(
    account_id: int,
    page: int = 1,
    page_size: int = 50,
    change_type: Optional[str] = None,
):
    """
    Get change history for a specific M3U account.

    Args:
        account_id: M3U account ID
        page: Page number (1-indexed)
        page_size: Number of items per page
        change_type: Filter by change type
    """
    from models import M3UChangeLog

    db = get_session()
    try:
        query = db.query(M3UChangeLog).filter(M3UChangeLog.m3u_account_id == account_id)

        if change_type:
            query = query.filter(M3UChangeLog.change_type == change_type)

        total = query.count()

        changes = (
            query.order_by(M3UChangeLog.change_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "results": [c.to_dict() for c in changes],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "m3u_account_id": account_id,
        }
    finally:
        db.close()


@router.get("/api/m3u/snapshots")
async def get_m3u_snapshots(
    m3u_account_id: Optional[int] = None,
    limit: int = 10,
):
    """
    Get recent M3U snapshots.

    Args:
        m3u_account_id: Filter by M3U account ID
        limit: Maximum number of snapshots to return
    """
    from models import M3USnapshot

    db = get_session()
    try:
        query = db.query(M3USnapshot)

        if m3u_account_id:
            query = query.filter(M3USnapshot.m3u_account_id == m3u_account_id)

        snapshots = query.order_by(M3USnapshot.snapshot_time.desc()).limit(limit).all()

        return [s.to_dict() for s in snapshots]
    finally:
        db.close()


# -------------------------------------------------------------------------
# M3U Digest Settings API
# -------------------------------------------------------------------------

@router.get("/api/m3u/digest/settings")
async def get_m3u_digest_settings():
    """Get M3U digest email settings."""
    from tasks.m3u_digest import get_or_create_digest_settings

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)
        return settings.to_dict()
    finally:
        db.close()


@router.put("/api/m3u/digest/settings")
async def update_m3u_digest_settings(request: M3UDigestSettingsUpdate):
    """Update M3U digest email settings."""
    from tasks.m3u_digest import get_or_create_digest_settings

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)

        # Validate and apply updates
        if request.enabled is not None:
            settings.enabled = request.enabled

        if request.frequency is not None:
            if request.frequency not in ("immediate", "hourly", "daily", "weekly"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid frequency. Must be: immediate, hourly, daily, or weekly"
                )
            settings.frequency = request.frequency

        if request.email_recipients is not None:
            # Validate email addresses
            email_pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
            for email in request.email_recipients:
                if not email_pattern.match(email):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid email address: {email}"
                    )
            settings.set_email_recipients(request.email_recipients)

        if request.include_group_changes is not None:
            settings.include_group_changes = request.include_group_changes

        if request.include_stream_changes is not None:
            settings.include_stream_changes = request.include_stream_changes

        if request.show_detailed_list is not None:
            settings.show_detailed_list = request.show_detailed_list

        if request.min_changes_threshold is not None:
            if request.min_changes_threshold < 1:
                raise HTTPException(
                    status_code=400,
                    detail="min_changes_threshold must be at least 1"
                )
            settings.min_changes_threshold = request.min_changes_threshold

        if request.send_to_discord is not None:
            settings.send_to_discord = request.send_to_discord

        if request.exclude_group_patterns is not None:
            for pattern in request.exclude_group_patterns:
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid group exclude regex '{pattern}': {e}"
                    )
            settings.set_exclude_group_patterns(request.exclude_group_patterns)

        if request.exclude_stream_patterns is not None:
            for pattern in request.exclude_stream_patterns:
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid stream exclude regex '{pattern}': {e}"
                    )
            settings.set_exclude_stream_patterns(request.exclude_stream_patterns)

        db.commit()
        db.refresh(settings)

        # Log to journal
        journal.log_entry(
            category="m3u",
            action_type="update",
            entity_id=settings.id,
            entity_name="M3U Digest Settings",
            description="Updated M3U digest email settings",
            after_value=settings.to_dict(),
        )

        return settings.to_dict()
    finally:
        db.close()


@router.post("/api/m3u/digest/test")
async def send_test_m3u_digest():
    """Send a test M3U digest email."""
    from tasks.m3u_digest import M3UDigestTask, get_or_create_digest_settings

    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)

        has_email = bool(settings.get_email_recipients())
        has_discord = bool(settings.send_to_discord)
        if not has_email and not has_discord:
            raise HTTPException(
                status_code=400,
                detail="No notification targets configured. Please add email recipients or enable Discord."
            )

        task = M3UDigestTask()
        result = await task.execute(force=True)

        return {
            "success": result.success,
            "message": result.message,
            "details": result.details,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to send test M3U digest: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
