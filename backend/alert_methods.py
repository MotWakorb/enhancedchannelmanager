"""
Alert Methods Framework.

Provides abstract base class and registry for external notification methods
(Discord, Telegram, SMTP, etc.).

Supports notification batching/digest mode: when multiple notifications arrive
within a short window, they are combined into a single digest message.
"""
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any, Type, List

logger = logging.getLogger(__name__)

# Default digest window in seconds - alerts within this window are batched together
DEFAULT_DIGEST_WINDOW_SECONDS = 30


class AlertMessage:
    """Represents a message to be sent through alert methods."""

    def __init__(
        self,
        title: str,
        message: str,
        notification_type: str = "info",
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.title = title
        self.message = message
        self.notification_type = notification_type  # info, success, warning, error
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "message": self.message,
            "type": self.notification_type,
            "source": self.source,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat() + "Z",
        }


class AlertMethod(ABC):
    """Abstract base class for alert methods."""

    # Method type identifier (e.g., "discord", "telegram", "smtp")
    method_type: str = ""

    # Human-readable name
    display_name: str = ""

    # Required config fields for this method type
    required_config_fields: List[str] = []

    # Optional config fields with defaults
    optional_config_fields: Dict[str, Any] = {}

    def __init__(self, method_id: int, name: str, config: Dict[str, Any]):
        self.method_id = method_id
        self.name = name
        self.config = config

    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        """
        Send a message through this method.

        Args:
            message: The AlertMessage to send

        Returns:
            True if sent successfully, False otherwise
        """
        pass

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test the method connection/credentials.

        Returns:
            Tuple of (success, message)
        """
        pass

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate the configuration for this method type.

        Returns:
            Tuple of (is_valid, error_message)
        """
        missing = []
        for field in cls.required_config_fields:
            if field not in config or not config[field]:
                missing.append(field)

        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"

        return True, ""

    def format_message(self, message: AlertMessage) -> str:
        """
        Format a message for this method. Override in subclasses for
        method-specific formatting (e.g., Markdown for Discord).
        """
        parts = []
        if message.title:
            parts.append(f"**{message.title}**")
        parts.append(message.message)
        if message.source:
            parts.append(f"Source: {message.source}")
        return "\n".join(parts)

    def get_emoji(self, notification_type: str) -> str:
        """Get an emoji for the notification type."""
        return {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }.get(notification_type, "📢")

    async def send_digest(self, messages: List[AlertMessage]) -> bool:
        """
        Send a digest of multiple messages.

        Default implementation combines messages and sends as one.
        Subclasses can override for method-specific digest formatting.

        Args:
            messages: List of AlertMessages to send as a digest

        Returns:
            True if sent successfully, False otherwise
        """
        if not messages:
            return True

        if len(messages) == 1:
            # Single message, just send normally
            return await self.send(messages[0])

        # Build a combined digest message
        digest = self._build_digest(messages)
        return await self.send(digest)

    def _build_digest(self, messages: List[AlertMessage]) -> AlertMessage:
        """Build a digest AlertMessage from multiple messages."""
        # Count by type
        counts = {"success": 0, "error": 0, "warning": 0, "info": 0}
        for msg in messages:
            counts[msg.notification_type] = counts.get(msg.notification_type, 0) + 1

        # Determine overall status
        if counts["error"] > 0:
            overall_type = "error"
        elif counts["warning"] > 0:
            overall_type = "warning"
        elif counts["success"] > 0:
            overall_type = "success"
        else:
            overall_type = "info"

        # Build summary
        summary_parts = []
        if counts["success"]:
            summary_parts.append(f"{counts['success']} succeeded")
        if counts["error"]:
            summary_parts.append(f"{counts['error']} failed")
        if counts["warning"]:
            summary_parts.append(f"{counts['warning']} warnings")
        if counts["info"]:
            summary_parts.append(f"{counts['info']} info")

        # Build message body
        body_parts = []
        for msg in messages:
            emoji = self.get_emoji(msg.notification_type)
            body_parts.append(f"{emoji} {msg.title}: {msg.message}")

        return AlertMessage(
            title=f"ECM Digest ({', '.join(summary_parts)})",
            message="\n".join(body_parts),
            notification_type=overall_type,
            source="ECM Digest",
            metadata={"message_count": len(messages), "counts": counts},
        )


# Method type registry
_method_registry: Dict[str, Type[AlertMethod]] = {}


def register_method(method_class: Type[AlertMethod]) -> Type[AlertMethod]:
    """Decorator to register an alert method type."""
    if not method_class.method_type:
        raise ValueError(f"Method class {method_class.__name__} must define method_type")

    _method_registry[method_class.method_type] = method_class
    logger.info(f"Registered alert method type: {method_class.method_type}")
    return method_class


def get_method_types() -> List[Dict[str, Any]]:
    """Get list of available method types with their metadata."""
    logger.debug(f"Getting method types, registry has {len(_method_registry)} types: {list(_method_registry.keys())}")
    return [
        {
            "type": cls.method_type,
            "display_name": cls.display_name,
            "required_fields": cls.required_config_fields,
            "optional_fields": cls.optional_config_fields,
        }
        for cls in _method_registry.values()
    ]


def create_method(method_type: str, method_id: int, name: str, config: Dict[str, Any]) -> Optional[AlertMethod]:
    """Create an alert method instance from type and config."""
    logger.debug(f"Creating method instance: type={method_type}, id={method_id}, name={name}")
    method_class = _method_registry.get(method_type)
    if not method_class:
        logger.error(f"Unknown alert method type: {method_type}. Available types: {list(_method_registry.keys())}")
        return None

    logger.debug(f"Created method instance: {name} ({method_type})")
    return method_class(method_id, name, config)


class AlertMethodManager:
    """
    Manages alert methods and sends notifications to them.

    Supports notification batching: alerts are buffered for a short window
    (default 30 seconds) and then sent as a single digest message.
    """

    def __init__(self, digest_window_seconds: int = DEFAULT_DIGEST_WINDOW_SECONDS):
        self._methods: Dict[int, AlertMethod] = {}
        self._digest_window = digest_window_seconds
        # Buffer: method_id -> list of (AlertMessage, method_model_snapshot)
        self._alert_buffer: Dict[int, List[AlertMessage]] = {}
        self._flush_task: Optional[asyncio.Task] = None
        self._buffer_lock = asyncio.Lock()

    def load_methods(self) -> None:
        """Load all enabled alert methods from database."""
        from database import get_session
        from models import AlertMethod as AlertMethodModel

        logger.debug("Loading alert methods from database")
        session = get_session()
        try:
            methods = session.query(AlertMethodModel).filter(
                AlertMethodModel.enabled == True
            ).all()
            logger.debug(f"Found {len(methods)} enabled methods in database")

            self._methods.clear()
            for method_model in methods:
                try:
                    config = json.loads(method_model.config) if method_model.config else {}
                    method = create_method(
                        method_model.method_type,
                        method_model.id,
                        method_model.name,
                        config
                    )
                    if method:
                        self._methods[method_model.id] = method
                        logger.debug(f"Loaded alert method: {method_model.name} ({method_model.method_type})")
                    else:
                        logger.warning(f"Failed to create method instance for: {method_model.name} ({method_model.method_type})")
                except Exception as e:
                    logger.exception(f"Error loading method {method_model.name}: {e}")

            logger.info(f"Loaded {len(self._methods)} alert methods")
        except Exception as e:
            logger.exception(f"Error loading alert methods: {e}")
        finally:
            session.close()

    def reload_method(self, method_id: int) -> None:
        """Reload a specific method from database."""
        from database import get_session
        from models import AlertMethod as AlertMethodModel

        session = get_session()
        try:
            method_model = session.query(AlertMethodModel).filter(
                AlertMethodModel.id == method_id
            ).first()

            if not method_model or not method_model.enabled:
                # Remove from active methods
                self._methods.pop(method_id, None)
                return

            config = json.loads(method_model.config) if method_model.config else {}
            method = create_method(
                method_model.method_type,
                method_model.id,
                method_model.name,
                config
            )
            if method:
                self._methods[method_id] = method
        finally:
            session.close()

    def _should_alert_for_source(
        self,
        alert_sources_json: Optional[str],
        alert_category: Optional[str],
        entity_id: Optional[int],
        failed_count: int = 0,
    ) -> bool:
        """
        Check if an alert should be sent based on granular source filtering.

        Args:
            alert_sources_json: JSON string with filter config (or None for "send all")
            alert_category: Category like "epg_refresh", "m3u_refresh", "probe_failures"
            entity_id: Source ID (EPG source ID or M3U account ID)
            failed_count: For probe_failures, the number of failures

        Returns:
            True if alert should be sent, False otherwise
        """
        # If no alert_sources configured, send all (backwards compatible)
        if not alert_sources_json:
            return True

        # If no category specified, send the alert (general notification)
        if not alert_category:
            return True

        try:
            alert_sources = json.loads(alert_sources_json)
        except (json.JSONDecodeError, TypeError):
            # Invalid JSON, default to send all
            return True

        # Check category-specific filtering
        if alert_category == "epg_refresh":
            config = alert_sources.get("epg_refresh", {})
            if not config.get("enabled", True):
                return False
            filter_mode = config.get("filter_mode", "all")
            source_ids = config.get("source_ids", [])

            if filter_mode == "all":
                return True
            elif filter_mode == "only_selected":
                return entity_id in source_ids if entity_id else False
            elif filter_mode == "all_except":
                return entity_id not in source_ids if entity_id else True

        elif alert_category == "m3u_refresh":
            config = alert_sources.get("m3u_refresh", {})
            if not config.get("enabled", True):
                return False
            filter_mode = config.get("filter_mode", "all")
            account_ids = config.get("account_ids", [])

            if filter_mode == "all":
                return True
            elif filter_mode == "only_selected":
                return entity_id in account_ids if entity_id else False
            elif filter_mode == "all_except":
                return entity_id not in account_ids if entity_id else True

        elif alert_category == "probe_failures":
            config = alert_sources.get("probe_failures", {})
            if not config.get("enabled", True):
                return False
            min_failures = config.get("min_failures", 1)
            return failed_count >= min_failures

        # Unknown category, send the alert
        return True

    async def send_alert(
        self,
        title: str,
        message: str,
        notification_type: str = "info",
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        alert_category: Optional[str] = None,
        entity_id: Optional[int] = None,
        channel_settings: Optional[Dict[str, bool]] = None,
    ) -> Dict[int, bool]:
        """
        Queue an alert to be sent to all applicable methods.

        Alerts are buffered and sent as a digest after the digest window expires.
        This prevents notification flooding when multiple events occur quickly.

        Args:
            title: Alert title
            message: Alert message
            notification_type: One of info, success, warning, error
            source: Source of the notification
            metadata: Additional metadata
            alert_category: Category for granular filtering ("epg_refresh", "m3u_refresh", "probe_failures")
            entity_id: Source/account ID for filtering (EPG source ID or M3U account ID)
            channel_settings: Per-task channel settings (send_to_email, send_to_discord, send_to_telegram).
                             If None, all channels are allowed.

        Returns:
            Dict mapping method_id to queued status (True = queued successfully)
        """
        from database import get_session
        from models import AlertMethod as AlertMethodModel

        alert_message = AlertMessage(
            title=title,
            message=message,
            notification_type=notification_type,
            source=source,
            metadata=metadata,
        )

        results = {}
        session = get_session()

        # Extract failed_count from metadata for probe_failures
        failed_count = 0
        if metadata and "failed_count" in metadata:
            failed_count = metadata.get("failed_count", 0)

        # Map channel_settings keys to method_type values
        # send_to_email -> smtp, send_to_discord -> discord, send_to_telegram -> telegram
        channel_type_map = {
            "send_to_email": "smtp",
            "send_to_discord": "discord",
            "send_to_telegram": "telegram",
        }

        try:
            async with self._buffer_lock:
                for method_id, method in self._methods.items():
                    # Check if this method should receive this notification type
                    method_model = session.query(AlertMethodModel).filter(
                        AlertMethodModel.id == method_id
                    ).first()

                    if not method_model:
                        continue

                    # Check per-task channel settings (if provided)
                    if channel_settings is not None:
                        method_type = method_model.method_type
                        channel_enabled = True
                        for setting_key, type_value in channel_type_map.items():
                            if method_type == type_value:
                                channel_enabled = channel_settings.get(setting_key, True)
                                break

                        if not channel_enabled:
                            logger.debug(f"Method {method.name} skipped: channel disabled by task settings")
                            continue

                    # Check notification type filter
                    type_enabled = {
                        "info": method_model.notify_info,
                        "success": method_model.notify_success,
                        "warning": method_model.notify_warning,
                        "error": method_model.notify_error,
                    }.get(notification_type, False)

                    if not type_enabled:
                        logger.debug(f"Method {method.name} skipped: {notification_type} not enabled")
                        continue

                    # Check granular source filtering
                    if not self._should_alert_for_source(
                        method_model.alert_sources,
                        alert_category,
                        entity_id,
                        failed_count,
                    ):
                        logger.debug(f"Method {method.name} skipped: source filter ({alert_category}, entity_id={entity_id})")
                        continue

                    # Add to buffer instead of sending immediately
                    if method_id not in self._alert_buffer:
                        self._alert_buffer[method_id] = []
                    self._alert_buffer[method_id].append(alert_message)
                    results[method_id] = True
                    logger.debug(f"Alert queued for {method.name}: {title} (buffer size: {len(self._alert_buffer[method_id])})")

                # Schedule flush if not already scheduled
                if self._alert_buffer and (self._flush_task is None or self._flush_task.done()):
                    self._flush_task = asyncio.create_task(self._schedule_flush())
                    logger.debug(f"Scheduled digest flush in {self._digest_window}s")

        finally:
            session.close()

        return results

    async def _schedule_flush(self) -> None:
        """Wait for digest window then flush the buffer."""
        try:
            await asyncio.sleep(self._digest_window)
            await self._flush_buffer()
        except asyncio.CancelledError:
            logger.debug("Flush task cancelled")
        except Exception as e:
            logger.exception(f"Error in scheduled flush: {e}")

    async def _flush_buffer(self) -> Dict[int, bool]:
        """Flush all buffered alerts as digests."""
        from database import get_session
        from models import AlertMethod as AlertMethodModel

        results = {}

        async with self._buffer_lock:
            if not self._alert_buffer:
                return results

            logger.info(f"Flushing alert buffer: {sum(len(msgs) for msgs in self._alert_buffer.values())} alerts for {len(self._alert_buffer)} methods")

            session = get_session()
            try:
                for method_id, messages in list(self._alert_buffer.items()):
                    if not messages:
                        continue

                    method = self._methods.get(method_id)
                    if not method:
                        logger.warning(f"Method {method_id} not found, skipping {len(messages)} alerts")
                        continue

                    method_model = session.query(AlertMethodModel).filter(
                        AlertMethodModel.id == method_id
                    ).first()

                    if not method_model:
                        continue

                    # Send digest
                    try:
                        success = await method.send_digest(messages)
                        results[method_id] = success

                        if success:
                            method_model.last_sent_at = datetime.utcnow()
                            session.commit()
                            logger.info(f"Digest sent via {method.name}: {len(messages)} alerts")
                        else:
                            logger.warning(f"Failed to send digest via {method.name}")

                    except Exception as e:
                        logger.error(f"Error sending digest via {method.name}: {e}")
                        results[method_id] = False

                # Clear the buffer
                self._alert_buffer.clear()

            finally:
                session.close()

        return results

    async def flush_now(self) -> Dict[int, bool]:
        """Force immediate flush of the buffer (for testing or shutdown)."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        return await self._flush_buffer()


# Global manager instance
_manager: Optional[AlertMethodManager] = None


def get_alert_manager() -> AlertMethodManager:
    """Get the global alert method manager."""
    global _manager
    if _manager is None:
        logger.debug("Initializing global AlertMethodManager")
        _manager = AlertMethodManager()
        _manager.load_methods()
        logger.debug(f"AlertMethodManager initialized with {len(_manager._methods)} methods")
    return _manager


async def send_alert(
    title: str,
    message: str,
    notification_type: str = "info",
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    alert_category: Optional[str] = None,
    entity_id: Optional[int] = None,
    channel_settings: Optional[Dict[str, bool]] = None,
) -> Dict[int, bool]:
    """Convenience function to send an alert via the global manager.

    Args:
        title: Alert title
        message: Alert message
        notification_type: One of info, success, warning, error
        source: Source of the notification
        metadata: Additional metadata
        alert_category: Category for granular filtering ("epg_refresh", "m3u_refresh", "probe_failures")
        entity_id: Source/account ID for filtering (EPG source ID or M3U account ID)
        channel_settings: Per-task channel settings (send_to_email, send_to_discord, send_to_telegram).
                         If None, all channels are allowed.
    """
    manager = get_alert_manager()
    return await manager.send_alert(
        title=title,
        message=message,
        notification_type=notification_type,
        source=source,
        metadata=metadata,
        alert_category=alert_category,
        entity_id=entity_id,
        channel_settings=channel_settings,
    )
