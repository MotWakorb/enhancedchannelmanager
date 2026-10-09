"""
Notification service — cross-cutting notification helpers.

Extracted from main.py (Phase 1 of v0.13.0 backend refactor).
These functions are used by: notification endpoints, startup_event,
task_engine, settings router, and stream_prober.
"""
import asyncio
import json
import logging
from typing import Optional

from config import get_settings
from database import get_session

logger = logging.getLogger(__name__)


def task_start_alerts_enabled(task_id: str) -> bool:
    """Whether a task's info-level "started" notification should dispatch an
    external alert (Telegram/Discord/email).

    The start/progress notification is info-level, so external dispatch is
    opt-in via the task's ``alert_on_info`` flag on top of the master
    ``send_alerts`` toggle — mirroring the task-engine gate (GH #462 / bd-on4sr).
    Used by manual probe endpoints, which (unlike scheduled tasks) don't get a
    gated ``send_alerts`` from the task engine and must look it up themselves.

    Defaults to ``False`` (no external start alert) when the task has no
    ``ScheduledTask`` row, matching the info-alerts-opt-in default.
    """
    try:
        session = get_session()
        try:
            from models import ScheduledTask
            scheduled_task = session.query(ScheduledTask).filter(
                ScheduledTask.task_id == task_id
            ).first()
            if scheduled_task:
                return bool(scheduled_task.send_alerts and scheduled_task.alert_on_info)
        finally:
            session.close()
    except Exception as e:
        logger.warning("[NOTIFY-SVC] Failed to read alert config for task %s: %s", task_id, e)
    return False


async def create_notification_internal(
    notification_type: str = "info",
    title: Optional[str] = None,
    message: str = "",
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    action_label: Optional[str] = None,
    action_url: Optional[str] = None,
    metadata: Optional[dict] = None,
    send_alerts: bool = True,
    alert_category: Optional[str] = None,
    entity_id: Optional[int] = None,
    channel_settings: Optional[dict] = None,
) -> Optional[dict]:
    """Create a new notification (internal helper).

    Can be called from anywhere in the backend (task_engine, etc.)

    Args:
        notification_type: One of "info", "success", "warning", "error"
        title: Optional notification title
        message: Notification message (required)
        source: Source identifier (e.g., "task", "system")
        source_id: Source-specific ID (e.g., task_id)
        action_label: Optional action button label
        action_url: Optional action URL
        metadata: Optional additional data
        send_alerts: If True (default), also dispatch to configured alert channels.
        alert_category: Category for granular filtering, one of
            alert_methods.ALERT_CATEGORIES. None is filed under "other".
        entity_id: Source/account ID for filtering (EPG source ID or M3U account ID)
        channel_settings: Per-task legacy channel settings; ntfy has no per-task toggle.

    Returns:
        Notification dict or None if message is empty
    """
    from models import Notification

    if not message:
        logger.warning("[NOTIFY-SVC] create_notification_internal called with empty message")
        return None

    if notification_type not in ("info", "success", "warning", "error"):
        logger.warning("[NOTIFY-SVC] Invalid notification type: %s, defaulting to info", notification_type)
        notification_type = "info"

    session = get_session()
    try:
        notification = Notification(
            type=notification_type,
            title=title,
            message=message,
            source=source,
            source_id=source_id,
            action_label=action_label,
            action_url=action_url,
            extra_data=json.dumps(metadata) if metadata else None,
        )
        session.add(notification)
        session.commit()
        session.refresh(notification)
        result = notification.to_dict()

        # Dispatch to alert channels asynchronously (non-blocking)
        if send_alerts:
            asyncio.create_task(
                _dispatch_to_alert_channels(
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    source=source,
                    metadata=metadata,
                    alert_category=alert_category,
                    entity_id=entity_id,
                    channel_settings=channel_settings,
                )
            )

        # Log only safe routing identifiers (type + source), never the title or
        # message body: callers may pass a credential-derived (masked) detail in
        # `message`. The body is still persisted to the notification row and
        # dispatched to alert channels — both non-logging sinks.
        #
        # This started as a CodeQL workaround: the redactor used to be named
        # `mask_secrets`, and CodeQL's name-based sensitive-data heuristic
        # classified its OUTPUT as a secret, so any log line carrying it was
        # flagged. That root cause is gone (see the naming rule on
        # cloud_storage.upload_security.redact_secrets, bead
        # enhancedchannelmanager-9kwzp), but the behaviour is kept on purpose:
        # withholding an operator-supplied body from the log is correct on its
        # own merits, and the detail is not lost, only routed elsewhere.
        logger.debug("[NOTIFY-SVC] Created notification: type=%s source=%s source_id=%s", notification_type, source, source_id)
        return result
    except Exception as e:
        logger.exception("[NOTIFY-SVC] Failed to create notification: %s", e)
        return None
    finally:
        session.close()


async def update_notification_internal(
    notification_id: int,
    notification_type: str = None,
    message: str = None,
    metadata: dict = None,
) -> Optional[dict]:
    """Update an existing notification's content.

    Used for updating progress notifications like stream probe status.

    Args:
        notification_id: ID of the notification to update
        notification_type: New type (info, success, warning, error) - optional
        message: New message - optional
        metadata: New metadata dict - optional (replaces existing metadata)

    Returns:
        Updated notification dict or None if not found
    """
    from models import Notification

    session = get_session()
    try:
        notification = session.query(Notification).filter(
            Notification.id == notification_id
        ).first()

        if not notification:
            logger.warning("[NOTIFY-SVC] Notification %s not found for update", notification_id)
            return None

        if notification_type is not None and notification_type in ("info", "success", "warning", "error"):
            notification.type = notification_type

        if message is not None:
            notification.message = message

        if metadata is not None:
            notification.extra_data = json.dumps(metadata)

        session.commit()
        session.refresh(notification)
        result = notification.to_dict()

        logger.debug("[NOTIFY-SVC] Updated notification %s: %s - %s", notification_id, notification_type or 'same type', message[:50] if message else 'same message')
        return result
    except Exception as e:
        logger.exception("[NOTIFY-SVC] Failed to update notification %s: %s", notification_id, e)
        session.rollback()
        return None
    finally:
        session.close()


async def delete_notifications_by_source_internal(source: str) -> int:
    """Delete all notifications with a given source.

    Used for cleanup of progress notifications (e.g., old probe notifications).

    Args:
        source: The source identifier to match

    Returns:
        Number of notifications deleted
    """
    from models import Notification

    session = get_session()
    try:
        deleted = session.query(Notification).filter(
            Notification.source == source
        ).delete()
        session.commit()
        if deleted > 0:
            logger.debug("[NOTIFY-SVC] Deleted %s notification(s) with source '%s'", deleted, source)
        return deleted
    except Exception as e:
        logger.exception("[NOTIFY-SVC] Failed to delete notifications by source '%s': %s", source, e)
        session.rollback()
        return 0
    finally:
        session.close()


async def _dispatch_to_alert_channels(
    title: Optional[str],
    message: str,
    notification_type: str,
    source: Optional[str],
    metadata: Optional[dict],
    alert_category: Optional[str] = None,
    entity_id: Optional[int] = None,
    channel_settings: Optional[dict] = None,
):
    """Dispatch a notification to the configured alert methods.

    Every external destination (Discord, Telegram, email, ntfy) is an
    ``AlertMethod`` row, and every alert goes through the alert manager, which
    applies each row's severity toggles and ``alert_sources`` category filters
    and batches deliveries into a digest (GH #1026). Before #1026 Discord and
    Telegram were sent directly from shared settings, unfiltered.

    Args:
        channel_settings: Per-task channel toggles (send_to_email /
            send_to_discord / send_to_telegram). Each one covers every
            destination of that type. ntfy has no per-task toggle, so enabled
            ntfy methods receive any alert the task-level gates let through.

    Returns:
        ``{"email"|"discord"|"telegram"|"ntfy": None|bool}``: None when the
        channel was not attempted, True when the alert was handed to the alert
        manager (which may still filter it per destination), False when a
        requested channel could not be used.
    """
    settings = get_settings()
    results = {"email": None, "discord": None, "telegram": None, "ntfy": None}
    alert_title = title or "ECM Notification"

    # Determine which channels are enabled
    send_email = channel_settings.get("send_to_email", True) if channel_settings else True
    send_discord = channel_settings.get("send_to_discord", True) if channel_settings else True
    send_telegram = channel_settings.get("send_to_telegram", True) if channel_settings else True

    # Read the enabled method types once; each per-type decision below needs it.
    enabled_method_types = set()
    try:
        from models import AlertMethod as AlertMethodModel

        method_session = get_session()
        try:
            enabled_method_types = {
                row.method_type
                for row in method_session.query(AlertMethodModel).filter(
                    AlertMethodModel.enabled == True
                ).all()
            }
        finally:
            method_session.close()
    except Exception as e:
        logger.error("[NOTIFY-SVC] Failed to read enabled alert methods: %s", e)

    has_ntfy_method = "ntfy" in enabled_method_types
    has_smtp_method = "smtp" in enabled_method_types

    # If caller explicitly disabled all channels, do nothing.
    if channel_settings and not send_email and not send_discord and not send_telegram and not has_ntfy_method:
        return results

    # If nothing is configured, do nothing (prevents noisy "failed" logs).
    #
    # If the caller explicitly requested a channel via channel_settings (e.g. send_to_email=True),
    # we still run the branch to return a definitive False result for that channel.
    if channel_settings is None and not settings.is_smtp_configured() and not enabled_method_types:
        return results

    queue_discord = send_discord and "discord" in enabled_method_types
    queue_telegram = send_telegram and "telegram" in enabled_method_types

    logger.info(
        "[NOTIFY-SVC] Dispatching alerts: type=%s title=%s category=%s channels=[email=%s discord=%s telegram=%s ntfy=%s]",
        notification_type, alert_title, alert_category,
        send_email, queue_discord, queue_telegram, has_ntfy_method,
    )

    # Send to Email if enabled.
    #
    # Email delivery requires recipients. Those are configured via the Alert Methods
    # framework (SMTP method "to_emails"), while SMTP server settings are shared
    # (Settings > Email Settings).
    if send_email:
        if not settings.is_smtp_configured():
            results["email"] = False
            logger.warning("[NOTIFY-SVC] Email alerts enabled but shared SMTP settings are not configured")
        else:
            if not has_smtp_method:
                results["email"] = False
                logger.warning(
                    "[NOTIFY-SVC] Email alerts enabled but no SMTP alert method is configured. "
                    "Create an Email alert channel and set recipients."
                )

    queue_smtp = send_email and settings.is_smtp_configured() and has_smtp_method
    queued_channels = {
        "email": queue_smtp,
        "discord": queue_discord,
        "telegram": queue_telegram,
        "ntfy": has_ntfy_method,
    }
    if any(queued_channels.values()):
        try:
            # Ensure method types are registered when this service is imported
            # without main.py (tests and task workers).
            import alert_methods_discord  # noqa: F401
            import alert_methods_ntfy  # noqa: F401
            import alert_methods_smtp  # noqa: F401
            import alert_methods_telegram  # noqa: F401
            from alert_methods import send_alert as send_alert_via_methods

            await send_alert_via_methods(
                title=alert_title,
                message=message,
                notification_type=notification_type,
                source=source,
                metadata=metadata,
                alert_category=alert_category,
                entity_id=entity_id,
                channel_settings={
                    "send_to_email": queue_smtp,
                    "send_to_discord": queue_discord,
                    "send_to_telegram": queue_telegram,
                    "send_to_ntfy": has_ntfy_method,
                },
            )
            for channel, queued in queued_channels.items():
                if queued:
                    results[channel] = True
        except Exception as e:
            for channel, queued in queued_channels.items():
                if queued:
                    results[channel] = False
            logger.error("[NOTIFY-SVC] Failed to queue alert methods: %s", e)

    # Log summary
    sent = [k for k, v in results.items() if v is True]
    failed = [k for k, v in results.items() if v is False]
    if sent:
        logger.info("[NOTIFY-SVC] Alert dispatched to: %s", ', '.join(sent))
    if failed:
        logger.warning("[NOTIFY-SVC] Alert failed for: %s", ', '.join(failed))

    return results
