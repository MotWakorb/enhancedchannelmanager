"""ntfy alert method using the HTTP publish API."""
import asyncio
import logging
import re
from typing import Any, Dict
from urllib.parse import urlsplit

import aiohttp

from alert_methods import AlertMessage, AlertMethod, register_method

logger = logging.getLogger(__name__)

_TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")
_PRIORITIES = {"info": "3", "success": "3", "warning": "4", "error": "5"}


@register_method
class NtfyMethod(AlertMethod):
    """Publish alerts to ntfy.sh or a self-hosted ntfy server."""

    method_type = "ntfy"
    display_name = "ntfy"
    required_config_fields = ["server_url", "topic"]
    optional_config_fields = {"access_token": ""}

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(config, dict):
            return False, "ntfy config must be an object"

        server_url = config.get("server_url")
        if not isinstance(server_url, str) or not server_url:
            return False, "server_url is required and must be an absolute HTTP(S) server URL"
        if any(char.isspace() for char in server_url) or "\\" in server_url:
            return False, "Invalid ntfy server URL"

        try:
            parsed = urlsplit(server_url)
            _ = parsed.port
            hostname = parsed.hostname
        except ValueError:
            return False, "Invalid ntfy server URL"
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return False, "Invalid ntfy server URL"

        topic = config.get("topic")
        if not isinstance(topic, str) or not _TOPIC_RE.fullmatch(topic):
            return False, "topic must match ^[-_A-Za-z0-9]{1,64}$"

        if "access_token" in config:
            token = config["access_token"]
            if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
                return False, "access_token must be a non-empty string without line breaks"

        return True, ""

    async def send(self, message: AlertMessage) -> bool:
        valid, _ = self.validate_config(self.config)
        if not valid:
            logger.error("[ALERTS-NTFY] Method %s has invalid configuration", self.name)
            return False

        if not isinstance(message.message, str) or not message.message:
            logger.error("[ALERTS-NTFY] Method %s cannot send an empty message", self.name)
            return False
        try:
            body = message.message.encode("utf-8")
        except UnicodeEncodeError:
            logger.error("[ALERTS-NTFY] Method %s message is not valid UTF-8", self.name)
            return False

        url = f"{self.config['server_url'].rstrip('/')}/{self.config['topic']}"
        headers = {
            "Title": message.title or "ECM Notification",
            "Priority": _PRIORITIES.get(message.notification_type, "3"),
        }
        token = self.config.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        logger.debug("[ALERTS-NTFY] Message sent successfully via %s", self.name)
                        return True
                    logger.warning(
                        "[ALERTS-NTFY] Method %s failed with HTTP status %s",
                        self.name,
                        response.status,
                    )
                    return False
        except asyncio.TimeoutError:
            logger.error("[ALERTS-NTFY] Method %s timed out", self.name)
            return False
        except aiohttp.ClientError:
            logger.error("[ALERTS-NTFY] Method %s could not connect", self.name)
            return False
        except Exception:
            # Do not log exception text: header-construction failures can echo
            # the bearer value supplied by the operator.
            logger.error("[ALERTS-NTFY] Method %s failed unexpectedly", self.name)
            return False

    async def test_connection(self) -> tuple[bool, str]:
        test_message = AlertMessage(
            title="Connection Test",
            message=(
                "This is a test notification from Enhanced Channel Manager. "
                "If you see this, your ntfy target is configured correctly!"
            ),
            notification_type="info",
            source="ECM Alert Test",
        )
        if await self.send(test_message):
            return True, "Test notification sent successfully"
        return (
            False,
            "Failed to send test notification; check the server URL, topic, token, and server availability",
        )
