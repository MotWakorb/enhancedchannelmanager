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
        alert_category: Category for granular filtering ("epg_refresh", "m3u_refresh", "probe_failures")
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
    """Dispatch notification to configured alert channels using shared settings.

    This sends directly to Discord/Telegram/Email using the shared notification
    settings configured in Settings > Notification Settings.

    Args:
        channel_settings: Per-task legacy channel settings. Enabled ntfy methods
                         receive alerts without a per-task toggle.
    """
    import aiohttp

    settings = get_settings()
    results = {"email": None, "discord": None, "telegram": None, "ntfy": None}
    alert_title = title or "ECM Notification"

    # Determine which channels are enabled
    send_email = channel_settings.get("send_to_email", True) if channel_settings else True
    send_discord = channel_settings.get("send_to_discord", True) if channel_settings else True
    send_telegram = channel_settings.get("send_to_telegram", True) if channel_settings else True

    # Generic alert methods are configured independently of the legacy global
    # notification settings. Read enabled method types once so SMTP and ntfy can
    # share one precisely-filtered manager dispatch.
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
    if (
        channel_settings is None
        and not settings.is_smtp_configured()
        and not settings.is_discord_configured()
        and not settings.is_telegram_configured()
        and not has_ntfy_method
    ):
        return results

    logger.info(
        "[NOTIFY-SVC] Dispatching alerts: type=%s title=%s channels=[email=%s(configured=%s) discord=%s(configured=%s) telegram=%s(configured=%s)]",
        notification_type, alert_title,
        send_email, settings.is_smtp_configured(),
        send_discord, settings.is_discord_configured(),
        send_telegram, settings.is_telegram_configured(),
    )

    # Format message with type indicator
    type_emoji = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}.get(notification_type, "📢")

    # Send to Discord if configured and enabled
    if send_discord and settings.is_discord_configured():
        try:
            discord_message = f"**{type_emoji} {alert_title}**\n\n{message}"
            if metadata:
                if "task_name" in metadata:
                    discord_message += f"\n\n**Task:** {metadata['task_name']}"
                if "duration_seconds" in metadata:
                    discord_message += f"\n**Duration:** {metadata['duration_seconds']:.1f}s"

            payload = {
                "content": discord_message,
                "username": "ECM Alerts",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    settings.discord_webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 204:
                        results["discord"] = True
                        logger.debug("[NOTIFY-SVC] Alert sent to Discord successfully")
                    else:
                        results["discord"] = False
                        logger.warning("[NOTIFY-SVC] Discord alert failed: %s", response.status)
        except Exception as e:
            results["discord"] = False
            logger.error("[NOTIFY-SVC] Failed to send Discord alert: %s", e)

    # Send to Telegram if configured and enabled
    if send_telegram and settings.is_telegram_configured():
        try:
            telegram_message = f"{type_emoji} <b>{alert_title}</b>\n\n{message}"
            if metadata:
                if "task_name" in metadata:
                    telegram_message += f"\n\n<b>Task:</b> {metadata['task_name']}"
                if "duration_seconds" in metadata:
                    telegram_message += f"\n<b>Duration:</b> {metadata['duration_seconds']:.1f}s"

            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": settings.telegram_chat_id,
                "text": telegram_message,
                "parse_mode": "HTML",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        results["telegram"] = True
                        logger.debug("[NOTIFY-SVC] Alert sent to Telegram successfully")
                    else:
                        results["telegram"] = False
                        text = await response.text()
                        logger.warning("[NOTIFY-SVC] Telegram alert failed: %s - %s", response.status, text)
        except Exception as e:
            results["telegram"] = False
            logger.error("[NOTIFY-SVC] Failed to send Telegram alert: %s", e)

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
    if queue_smtp or has_ntfy_method:
        try:
            # Ensure method types are registered when this service is imported
            # without main.py (tests and task workers).
            import alert_methods_ntfy  # noqa: F401
            import alert_methods_smtp  # noqa: F401
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
                    "send_to_discord": False,
                    "send_to_telegram": False,
                    "send_to_ntfy": has_ntfy_method,
                },
            )
            if queue_smtp:
                results["email"] = True
            if has_ntfy_method:
                results["ntfy"] = True
        except Exception as e:
            if queue_smtp:
                results["email"] = False
            if has_ntfy_method:
                results["ntfy"] = False
            logger.error("[NOTIFY-SVC] Failed to queue generic alert methods: %s", e)

    # Log summary
    sent = [k for k, v in results.items() if v is True]
    failed = [k for k, v in results.items() if v is False]
    if sent:
        logger.info("[NOTIFY-SVC] Alert dispatched to: %s", ', '.join(sent))
    if failed:
        logger.warning("[NOTIFY-SVC] Alert failed for: %s", ', '.join(failed))

    return results
