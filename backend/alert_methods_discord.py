"""
Discord Webhook Alert Method.

Sends notifications to Discord via webhooks.
"""
import aiohttp
import logging
from typing import Dict, Any

from alert_methods import AlertMethod, AlertMessage, register_method, truncate_text
from credential_sentinel import credential_is_present
from notification_validation import DISCORD_WEBHOOK_ERROR, is_valid_discord_webhook

logger = logging.getLogger(__name__)

# Discord embed limits. A digest of a busy window can exceed them, and Discord
# rejects the whole message with a 400, so every text part is trimmed to fit.
_EMBED_TITLE_LIMIT = 256
_EMBED_DESCRIPTION_LIMIT = 4096
_EMBED_FIELD_NAME_LIMIT = 256
_EMBED_FIELD_VALUE_LIMIT = 1024
_EMBED_FOOTER_LIMIT = 2048
_EMBED_MAX_FIELDS = 25
_EMBED_TOTAL_LIMIT = 6000


def _embed_text_length(embed: Dict[str, Any]) -> int:
    """Characters Discord counts against the 6000-character embed total."""
    total = len(embed.get("title") or "") + len(embed.get("description") or "")
    total += len((embed.get("footer") or {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field["name"]) + len(field["value"])
    return total


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

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple[bool, str]:
        """Require a webhook URL on an allowlisted Discord host (GH #1026).

        The URL is POSTed verbatim, so this is the same SSRF guard the shared
        settings webhook had on save. It also rejects the ``'********'`` mask.
        """
        is_valid, error = super().validate_config(config)
        if not is_valid:
            return is_valid, error
        if not is_valid_discord_webhook(config.get("webhook_url")):
            return False, DISCORD_WEBHOOK_ERROR
        return True, ""

    def _build_embed(self, message: AlertMessage) -> Dict[str, Any]:
        """Build one embed for ``message``, trimmed to Discord's limits."""
        embed: Dict[str, Any] = {
            "description": truncate_text(message.message, _EMBED_DESCRIPTION_LIMIT),
            "color": self.EMBED_COLORS.get(message.notification_type, 0x808080),
        }
        if message.title:
            embed["title"] = truncate_text(
                f"{self.get_emoji(message.notification_type)} {message.title}",
                _EMBED_TITLE_LIMIT,
            )

        # Add timestamp if configured
        if self.config.get("include_timestamp", True):
            embed["timestamp"] = message.timestamp.isoformat()

        # Add source as footer
        if message.source:
            embed["footer"] = {"text": truncate_text(f"Source: {message.source}", _EMBED_FOOTER_LIMIT)}

        # Add metadata as fields. Discord rejects an empty field value, so
        # blank values are left out rather than sent.
        if message.metadata:
            fields = []
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)) and str(value):
                    fields.append({
                        "name": truncate_text(key.replace("_", " ").title(), _EMBED_FIELD_NAME_LIMIT),
                        "value": truncate_text(str(value), _EMBED_FIELD_VALUE_LIMIT),
                        "inline": True,
                    })
            if fields:
                embed["fields"] = fields[:_EMBED_MAX_FIELDS]

        # Stay under the total limit: drop metadata fields first, then trim
        # the description, which is where a long digest puts its text.
        while embed.get("fields") and _embed_text_length(embed) > _EMBED_TOTAL_LIMIT:
            embed["fields"].pop()
            if not embed["fields"]:
                del embed["fields"]
        overflow = _embed_text_length(embed) - _EMBED_TOTAL_LIMIT
        if overflow > 0:
            embed["description"] = truncate_text(
                embed["description"], len(embed["description"]) - overflow
            )
        return embed

    async def send(self, message: AlertMessage) -> bool:
        """Send a message to Discord via webhook."""
        webhook_url = self.config.get("webhook_url")
        logger.debug(
            "[ALERTS-DISCORD] Sending to %s: type=%s title=%r",
            self.name, message.notification_type, message.title,
        )
        if not webhook_url:
            logger.error("[ALERTS-DISCORD] Discord method %s: No webhook URL configured", self.name)
            return False
        # Rows written before the allowlist existed (or restored from an old
        # backup) are re-checked here, so a non-Discord URL is never POSTed.
        if not is_valid_discord_webhook(webhook_url):
            logger.error("[ALERTS-DISCORD] Discord method %s: Webhook URL is not on an allowlisted Discord host", self.name)
            return False

        embed = self._build_embed(message)

        # Build payload
        payload: Dict[str, Any] = {"embeds": [embed]}

        # Add optional username and avatar
        # A standard backup scrubs alert-method usernames to the redaction
        # placeholder; a row restored from one posts under the webhook's own
        # name rather than as "***REDACTED***".
        username = self.config.get("username")
        if credential_is_present(username):
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
                        logger.debug("[ALERTS-DISCORD] Message sent successfully to %s", self.name)
                        return True
                    elif response.status == 429:
                        # Rate limited
                        retry_after = response.headers.get("Retry-After", "unknown")
                        logger.warning(
                            "[ALERTS-DISCORD] Discord method %s: Rate limited, retry after %ss",
                            self.name, retry_after,
                        )
                        return False
                    else:
                        text = await response.text()
                        logger.error(
                            "[ALERTS-DISCORD] Discord method %s: Failed with status %s: %s",
                            self.name, response.status, text,
                        )
                        return False

        except aiohttp.ClientError as e:
            logger.error("[ALERTS-DISCORD] Discord method %s: Connection error: %s", self.name, e)
            return False
        except Exception as e:
            logger.exception("[ALERTS-DISCORD] Discord method %s: Unexpected error: %s", self.name, e)
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test the Discord webhook by sending a test message."""
        logger.info("[ALERTS-DISCORD] Testing connection for method %s", self.name)
        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            logger.debug("[ALERTS-DISCORD] Test failed: no webhook_url for %s", self.name)
            return False, "No webhook URL configured"

        # Validate webhook URL format (same allowlist as validate_config)
        if not is_valid_discord_webhook(webhook_url):
            logger.debug("[ALERTS-DISCORD] Test failed: invalid webhook URL format for %s", self.name)
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
