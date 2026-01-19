"""
Discord Webhook Alert Method.

Sends notifications to Discord via webhooks.
"""
import aiohttp
import logging
from typing import Dict, Any

from alert_methods import AlertMethod, AlertMessage, register_method

logger = logging.getLogger(__name__)


@register_method
class DiscordWebhookMethod(AlertMethod):
    """Sends alerts to Discord via webhook."""

    method_type = "discord"
    display_name = "Discord Webhook"
    required_config_fields = ["webhook_url"]
    optional_config_fields = {
        "username": "ECM Alerts",
        "avatar_url": "",
        "include_timestamp": True,
    }

    # Discord embed colors by notification type
    EMBED_COLORS = {
        "info": 0x3B82F6,     # Blue
        "success": 0x22C55E,  # Green
        "warning": 0xF59E0B,  # Amber
        "error": 0xEF4444,    # Red
    }

    async def send(self, message: AlertMessage) -> bool:
        """Send a message to Discord via webhook."""
        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            logger.error(f"Discord method {self.name}: No webhook URL configured")
            return False

        # Build Discord embed
        embed = {
            "title": f"{self.get_emoji(message.notification_type)} {message.title}" if message.title else None,
            "description": message.message,
            "color": self.EMBED_COLORS.get(message.notification_type, 0x808080),
        }

        # Add timestamp if configured
        if self.config.get("include_timestamp", True):
            embed["timestamp"] = message.timestamp.isoformat()

        # Add source as footer
        if message.source:
            embed["footer"] = {"text": f"Source: {message.source}"}

        # Add metadata as fields if present
        if message.metadata:
            fields = []
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    fields.append({
                        "name": key.replace("_", " ").title(),
                        "value": str(value),
                        "inline": True,
                    })
            if fields:
                embed["fields"] = fields[:25]  # Discord limit

        # Clean up None values
        embed = {k: v for k, v in embed.items() if v is not None}

        # Build payload
        payload: Dict[str, Any] = {"embeds": [embed]}

        # Add optional username and avatar
        username = self.config.get("username")
        if username:
            payload["username"] = username

        avatar_url = self.config.get("avatar_url")
        if avatar_url:
            payload["avatar_url"] = avatar_url

        # Send to Discord
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 204:
                        return True
                    elif response.status == 429:
                        # Rate limited
                        retry_after = response.headers.get("Retry-After", "unknown")
                        logger.warning(
                            f"Discord method {self.name}: Rate limited, retry after {retry_after}s"
                        )
                        return False
                    else:
                        text = await response.text()
                        logger.error(
                            f"Discord method {self.name}: Failed with status {response.status}: {text}"
                        )
                        return False

        except aiohttp.ClientError as e:
            logger.error(f"Discord method {self.name}: Connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Discord method {self.name}: Unexpected error: {e}")
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test the Discord webhook by sending a test message."""
        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            return False, "No webhook URL configured"

        # Validate webhook URL format
        if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
           not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
            return False, "Invalid Discord webhook URL format"

        # Send a test message
        test_message = AlertMessage(
            title="Connection Test",
            message="This is a test message from Enhanced Channel Manager. "
                    "If you see this, your Discord webhook is configured correctly!",
            notification_type="info",
            source="ECM Alert Test",
        )

        try:
            success = await self.send(test_message)
            if success:
                return True, "Test message sent successfully"
            else:
                return False, "Failed to send test message"
        except Exception as e:
            return False, f"Error during test: {str(e)}"
