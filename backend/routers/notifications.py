"""
Notifications router — notification CRUD endpoints.

Extracted from main.py (Phase 3 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_session
from services.notification_service import create_notification_internal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


class CreateNotificationRequest(BaseModel):
    notification_type: str = "info"
    title: Optional[str] = None
    message: str
    source: Optional[str] = None
    source_id: Optional[str] = None
    action_label: Optional[str] = None
    action_url: Optional[str] = None
    metadata: Optional[dict] = None
    send_alerts: bool = True


@router.get("")
async def get_notifications(
    page: int = 1,
    page_size: int = 50,
    unread_only: bool = False,
    notification_type: Optional[str] = None,
):
    """Get notifications with pagination and filtering."""
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification)

        # Filter by read status
        if unread_only:
            query = query.filter(Notification.read == False)

        # Filter by type
        if notification_type:
            query = query.filter(Notification.type == notification_type)

        # Order by most recent first
        query = query.order_by(Notification.created_at.desc())

        # Get total count
        total = query.count()

        # Apply pagination
        offset = (page - 1) * page_size
        notifications = query.offset(offset).limit(page_size).all()

        # Get unread count
        unread_count = session.query(Notification).filter(Notification.read == False).count()

        return {
            "notifications": [n.to_dict() for n in notifications],
            "total": total,
            "unread_count": unread_count,
            "page": page,
            "page_size": page_size,
        }
    finally:
        session.close()


@router.post("")
async def create_notification(request: CreateNotificationRequest):
    """Create a new notification (API endpoint).

    Args:
        send_alerts: If True (default), also dispatch to configured alert channels.
    """
    if not request.message:
        raise HTTPException(status_code=400, detail="Message is required")

    if request.notification_type not in ("info", "success", "warning", "error"):
        raise HTTPException(status_code=400, detail="Invalid notification type")

    result = await create_notification_internal(
        notification_type=request.notification_type,
        title=request.title,
        message=request.message,
        source=request.source,
        source_id=request.source_id,
        action_label=request.action_label,
        action_url=request.action_url,
        metadata=request.metadata,
        send_alerts=request.send_alerts,
    )

    if result is None:
        raise HTTPException(status_code=500, detail="Failed to create notification")

    return result


@router.patch("/mark-all-read")
async def mark_all_notifications_read():
    """Mark all notifications as read."""
    from datetime import datetime
    from models import Notification

    session = get_session()
    try:
        count = session.query(Notification).filter(Notification.read == False).update(
            {"read": True, "read_at": datetime.utcnow()},
            synchronize_session=False
        )
        session.commit()
        return {"marked_read": count}
    finally:
        session.close()


@router.patch("/{notification_id}")
async def update_notification(notification_id: int, read: Optional[bool] = None):
    """Update a notification (mark as read/unread)."""
    from datetime import datetime
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        if read is not None:
            notification.read = read
            notification.read_at = datetime.utcnow() if read else None

        session.commit()
        session.refresh(notification)
        return notification.to_dict()
    finally:
        session.close()


@router.delete("/{notification_id}")
async def delete_notification(notification_id: int):
    """Delete a specific notification."""
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        session.delete(notification)
        session.commit()
        return {"deleted": True}
    finally:
        session.close()


@router.delete("")
async def clear_all_notifications(read_only: bool = True):
    """Clear notifications. By default only clears read notifications."""
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification)
        if read_only:
            query = query.filter(Notification.read == True)

        count = query.delete(synchronize_session=False)
        session.commit()
        return {"deleted": count, "read_only": read_only}
    finally:
        session.close()


@router.delete("/by-source")
async def delete_notifications_by_source(source: str, source_id: Optional[str] = None):
    """Delete notifications matching source and optionally source_id."""
    from models import Notification

    session = get_session()
    try:
        query = session.query(Notification).filter(Notification.source == source)
        if source_id is not None:
            query = query.filter(Notification.source_id == source_id)

        count = query.delete(synchronize_session=False)
        session.commit()
        return {"deleted": count, "source": source, "source_id": source_id}
    finally:
        session.close()
