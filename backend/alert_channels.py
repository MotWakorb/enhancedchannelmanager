"""
Alert Channels Framework.

Provides abstract base class and registry for external notification channels
(Discord, Telegram, SMTP, etc.).
"""
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any, Type, List

logger = logging.getLogger(__name__)


class AlertMessage:
    """Represents a message to be sent through alert channels."""

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


class AlertChannel(ABC):
    """Abstract base class for alert channels."""

    # Channel type identifier (e.g., "discord", "telegram", "smtp")
    channel_type: str = ""

    # Human-readable name
    display_name: str = ""

    # Required config fields for this channel type
    required_config_fields: List[str] = []

    # Optional config fields with defaults
    optional_config_fields: Dict[str, Any] = {}

    def __init__(self, channel_id: int, name: str, config: Dict[str, Any]):
        self.channel_id = channel_id
        self.name = name
        self.config = config

    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        """
        Send a message through this channel.

        Args:
            message: The AlertMessage to send

        Returns:
            True if sent successfully, False otherwise
        """
        pass

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test the channel connection/credentials.

        Returns:
            Tuple of (success, message)
        """
        pass

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate the configuration for this channel type.

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
        Format a message for this channel. Override in subclasses for
        channel-specific formatting (e.g., Markdown for Discord).
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


# Channel type registry
_channel_registry: Dict[str, Type[AlertChannel]] = {}


def register_channel(channel_class: Type[AlertChannel]) -> Type[AlertChannel]:
    """Decorator to register an alert channel type."""
    if not channel_class.channel_type:
        raise ValueError(f"Channel class {channel_class.__name__} must define channel_type")

    _channel_registry[channel_class.channel_type] = channel_class
    logger.info(f"Registered alert channel type: {channel_class.channel_type}")
    return channel_class


def get_channel_types() -> List[Dict[str, Any]]:
    """Get list of available channel types with their metadata."""
    return [
        {
            "type": cls.channel_type,
            "display_name": cls.display_name,
            "required_fields": cls.required_config_fields,
            "optional_fields": cls.optional_config_fields,
        }
        for cls in _channel_registry.values()
    ]


def create_channel(channel_type: str, channel_id: int, name: str, config: Dict[str, Any]) -> Optional[AlertChannel]:
    """Create an alert channel instance from type and config."""
    channel_class = _channel_registry.get(channel_type)
    if not channel_class:
        logger.error(f"Unknown alert channel type: {channel_type}")
        return None

    return channel_class(channel_id, name, config)


class AlertChannelManager:
    """Manages alert channels and sends notifications to them."""

    def __init__(self):
        self._channels: Dict[int, AlertChannel] = {}

    def load_channels(self) -> None:
        """Load all enabled alert channels from database."""
        from database import get_session
        from models import AlertChannel as AlertChannelModel

        session = get_session()
        try:
            channels = session.query(AlertChannelModel).filter(
                AlertChannelModel.enabled == True
            ).all()

            self._channels.clear()
            for channel_model in channels:
                config = json.loads(channel_model.config) if channel_model.config else {}
                channel = create_channel(
                    channel_model.channel_type,
                    channel_model.id,
                    channel_model.name,
                    config
                )
                if channel:
                    self._channels[channel_model.id] = channel
                    logger.debug(f"Loaded alert channel: {channel_model.name} ({channel_model.channel_type})")

            logger.info(f"Loaded {len(self._channels)} alert channels")
        finally:
            session.close()

    def reload_channel(self, channel_id: int) -> None:
        """Reload a specific channel from database."""
        from database import get_session
        from models import AlertChannel as AlertChannelModel

        session = get_session()
        try:
            channel_model = session.query(AlertChannelModel).filter(
                AlertChannelModel.id == channel_id
            ).first()

            if not channel_model or not channel_model.enabled:
                # Remove from active channels
                self._channels.pop(channel_id, None)
                return

            config = json.loads(channel_model.config) if channel_model.config else {}
            channel = create_channel(
                channel_model.channel_type,
                channel_model.id,
                channel_model.name,
                config
            )
            if channel:
                self._channels[channel_id] = channel
        finally:
            session.close()

    async def send_alert(
        self,
        title: str,
        message: str,
        notification_type: str = "info",
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[int, bool]:
        """
        Send an alert to all applicable channels.

        Args:
            title: Alert title
            message: Alert message
            notification_type: One of info, success, warning, error
            source: Source of the notification
            metadata: Additional metadata

        Returns:
            Dict mapping channel_id to success status
        """
        from database import get_session
        from models import AlertChannel as AlertChannelModel

        alert_message = AlertMessage(
            title=title,
            message=message,
            notification_type=notification_type,
            source=source,
            metadata=metadata,
        )

        results = {}
        session = get_session()

        try:
            for channel_id, channel in self._channels.items():
                # Check if this channel should receive this notification type
                channel_model = session.query(AlertChannelModel).filter(
                    AlertChannelModel.id == channel_id
                ).first()

                if not channel_model:
                    continue

                # Check notification type filter
                type_enabled = {
                    "info": channel_model.notify_info,
                    "success": channel_model.notify_success,
                    "warning": channel_model.notify_warning,
                    "error": channel_model.notify_error,
                }.get(notification_type, False)

                if not type_enabled:
                    logger.debug(f"Channel {channel.name} skipped: {notification_type} not enabled")
                    continue

                # Check rate limiting
                if channel_model.last_sent_at:
                    elapsed = (datetime.utcnow() - channel_model.last_sent_at).total_seconds()
                    if elapsed < channel_model.min_interval_seconds:
                        logger.debug(
                            f"Channel {channel.name} rate limited: "
                            f"{elapsed:.0f}s < {channel_model.min_interval_seconds}s"
                        )
                        continue

                # Send the alert
                try:
                    success = await channel.send(alert_message)
                    results[channel_id] = success

                    if success:
                        # Update last_sent_at
                        channel_model.last_sent_at = datetime.utcnow()
                        session.commit()
                        logger.info(f"Alert sent via {channel.name}: {title}")
                    else:
                        logger.warning(f"Failed to send alert via {channel.name}")

                except Exception as e:
                    logger.error(f"Error sending alert via {channel.name}: {e}")
                    results[channel_id] = False

        finally:
            session.close()

        return results


# Global manager instance
_manager: Optional[AlertChannelManager] = None


def get_alert_manager() -> AlertChannelManager:
    """Get the global alert channel manager."""
    global _manager
    if _manager is None:
        _manager = AlertChannelManager()
        _manager.load_channels()
    return _manager


async def send_alert(
    title: str,
    message: str,
    notification_type: str = "info",
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[int, bool]:
    """Convenience function to send an alert via the global manager."""
    manager = get_alert_manager()
    return await manager.send_alert(
        title=title,
        message=message,
        notification_type=notification_type,
        source=source,
        metadata=metadata,
    )
