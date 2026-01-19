"""
Telegram Bot Alert Method.

Sends notifications to Telegram via Bot API.
"""
import aiohttp
import logging
from typing import Optional

from alert_methods import AlertMethod, AlertMessage, register_method

logger = logging.getLogger(__name__)


@register_method
class TelegramBotMethod(AlertMethod):
    """Sends alerts to Telegram via Bot API."""

    method_type = "telegram"
    display_name = "Telegram Bot"
    required_config_fields = ["bot_token", "chat_id"]
    optional_config_fields = {
        "parse_mode": "HTML",
        "disable_notification": False,
        "disable_web_page_preview": True,
    }

    TELEGRAM_API_BASE = "https://api.telegram.org/bot"

    # Emoji for notification types
    TYPE_EMOJI = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
    }

    def _format_html_message(self, message: AlertMessage) -> str:
        """Format message as HTML for Telegram."""
        emoji = self.TYPE_EMOJI.get(message.notification_type, "📢")
        parts = []

        # Title with emoji
        if message.title:
            parts.append(f"{emoji} <b>{self._escape_html(message.title)}</b>")
        else:
            parts.append(f"{emoji} <b>Notification</b>")

        parts.append("")

        # Message body
        parts.append(self._escape_html(message.message))

        # Metadata
        if message.metadata:
            parts.append("")
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    parts.append(f"<b>{key.replace('_', ' ').title()}:</b> {self._escape_html(str(value))}")

        # Footer
        parts.append("")
        footer_parts = []
        if message.source:
            footer_parts.append(f"📍 {self._escape_html(message.source)}")
        footer_parts.append(f"🕐 {message.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        parts.append("<i>" + " | ".join(footer_parts) + "</i>")

        return "\n".join(parts)

    def _format_markdown_message(self, message: AlertMessage) -> str:
        """Format message as Markdown for Telegram."""
        emoji = self.TYPE_EMOJI.get(message.notification_type, "📢")
        parts = []

        # Title with emoji
        if message.title:
            parts.append(f"{emoji} *{self._escape_markdown(message.title)}*")
        else:
            parts.append(f"{emoji} *Notification*")

        parts.append("")

        # Message body
        parts.append(self._escape_markdown(message.message))

        # Metadata
        if message.metadata:
            parts.append("")
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    parts.append(f"*{key.replace('_', ' ').title()}:* {self._escape_markdown(str(value))}")

        # Footer
        parts.append("")
        footer_parts = []
        if message.source:
            footer_parts.append(f"📍 {self._escape_markdown(message.source)}")
        footer_parts.append(f"🕐 {message.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        parts.append("_" + " | ".join(footer_parts) + "_")

        return "\n".join(parts)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Escape Markdown special characters for Telegram."""
        chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in chars_to_escape:
            text = text.replace(char, f"\\{char}")
        return text

    async def send(self, message: AlertMessage) -> bool:
        """Send a message to Telegram via Bot API."""
        bot_token = self.config.get("bot_token")
        chat_id = self.config.get("chat_id")

        if not bot_token or not chat_id:
            logger.error(f"Telegram method {self.name}: Missing bot_token or chat_id")
            return False

        parse_mode = self.config.get("parse_mode", "HTML")

        # Format message based on parse mode
        if parse_mode == "HTML":
            text = self._format_html_message(message)
        elif parse_mode == "Markdown" or parse_mode == "MarkdownV2":
            text = self._format_markdown_message(message)
        else:
            text = self.format_message(message)

        # Build request payload
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": self.config.get("disable_notification", False),
            "disable_web_page_preview": self.config.get("disable_web_page_preview", True),
        }

        url = f"{self.TELEGRAM_API_BASE}{bot_token}/sendMessage"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    result = await response.json()

                    if response.status == 200 and result.get("ok"):
                        return True
                    elif response.status == 429:
                        # Rate limited
                        retry_after = result.get("parameters", {}).get("retry_after", "unknown")
                        logger.warning(
                            f"Telegram method {self.name}: Rate limited, retry after {retry_after}s"
                        )
                        return False
                    else:
                        error_desc = result.get("description", "Unknown error")
                        logger.error(
                            f"Telegram method {self.name}: Failed with status {response.status}: {error_desc}"
                        )
                        return False

        except aiohttp.ClientError as e:
            logger.error(f"Telegram method {self.name}: Connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Telegram method {self.name}: Unexpected error: {e}")
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test the Telegram bot connection."""
        bot_token = self.config.get("bot_token")
        chat_id = self.config.get("chat_id")

        if not bot_token:
            return False, "Bot token not configured"
        if not chat_id:
            return False, "Chat ID not configured"

        # First, verify the bot token by calling getMe
        url = f"{self.TELEGRAM_API_BASE}{bot_token}/getMe"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    result = await response.json()

                    if response.status != 200 or not result.get("ok"):
                        error_desc = result.get("description", "Invalid bot token")
                        return False, f"Bot verification failed: {error_desc}"

                    bot_username = result.get("result", {}).get("username", "Unknown")

            # Send test message
            test_message = AlertMessage(
                title="Connection Test",
                message="This is a test message from Enhanced Channel Manager. "
                        "If you see this, your Telegram bot is configured correctly!",
                notification_type="info",
                source="ECM Alert Test",
            )

            success = await self.send(test_message)
            if success:
                return True, f"Test message sent successfully via @{bot_username}"
            else:
                return False, f"Bot @{bot_username} verified but failed to send message to chat {chat_id}"

        except aiohttp.ClientError as e:
            return False, f"Connection error: {str(e)}"
        except Exception as e:
            return False, f"Error: {str(e)}"
