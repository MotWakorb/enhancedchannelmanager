"""
Alert Methods Framework.

Provides abstract base class and registry for external notification methods
(Discord, Telegram, SMTP, etc.).
"""
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any, Type, List

logger = logging.getLogger(__name__)


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
    """Manages alert methods and sends notifications to them."""

    def __init__(self):
        self._methods: Dict[int, AlertMethod] = {}

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

    async def send_alert(
        self,
        title: str,
        message: str,
        notification_type: str = "info",
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[int, bool]:
        """
        Send an alert to all applicable methods.

        Args:
            title: Alert title
            message: Alert message
            notification_type: One of info, success, warning, error
            source: Source of the notification
            metadata: Additional metadata

        Returns:
            Dict mapping method_id to success status
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

        try:
            for method_id, method in self._methods.items():
                # Check if this method should receive this notification type
                method_model = session.query(AlertMethodModel).filter(
                    AlertMethodModel.id == method_id
                ).first()

                if not method_model:
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

                # Check rate limiting
                if method_model.last_sent_at:
                    elapsed = (datetime.utcnow() - method_model.last_sent_at).total_seconds()
                    if elapsed < method_model.min_interval_seconds:
                        logger.debug(
                            f"Method {method.name} rate limited: "
                            f"{elapsed:.0f}s < {method_model.min_interval_seconds}s"
                        )
                        continue

                # Send the alert
                try:
                    success = await method.send(alert_message)
                    results[method_id] = success

                    if success:
                        # Update last_sent_at
                        method_model.last_sent_at = datetime.utcnow()
                        session.commit()
                        logger.info(f"Alert sent via {method.name}: {title}")
                    else:
                        logger.warning(f"Failed to send alert via {method.name}")

                except Exception as e:
                    logger.error(f"Error sending alert via {method.name}: {e}")
                    results[method_id] = False

        finally:
            session.close()

        return results


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
