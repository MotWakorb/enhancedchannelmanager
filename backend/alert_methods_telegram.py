"""
Telegram Bot Alert Method.

Sends notifications to Telegram via Bot API.
"""
import aiohttp
import logging
from typing import Any, Dict, List, Tuple

from alert_methods import AlertMethod, AlertMessage, register_method, truncate_text
from notification_validation import TELEGRAM_BOT_TOKEN_ERROR, is_valid_telegram_bot_token

logger = logging.getLogger(__name__)

# Telegram rejects a message longer than 4096 characters (counted after the
# HTML/Markdown markup is parsed) with a 400, which would lose a whole digest.
# The title, metadata and footer are capped and the body gets what is left.
_TEXT_LIMIT = 4096
_TITLE_LIMIT = 256
_SOURCE_LIMIT = 100
_METADATA_VALUE_LIMIT = 200
# Metadata lines stop once their total reaches this, so the body always keeps
# at least _TEXT_LIMIT - _TITLE_LIMIT - _SOURCE_LIMIT - _METADATA_BUDGET
# - _FIXED_OVERHEAD characters.
_METADATA_BUDGET = 1500
# Room for the emoji, separators and the timestamp footer.
_FIXED_OVERHEAD = 64


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

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> tuple[bool, str]:
        """Require a well-formed bot token (GH #1026).

        The token is interpolated into the Bot API URL path, so a malformed
        value is refused here, as the settings test endpoint always did. This
        also rejects the ``'********'`` mask.
        """
        is_valid, error = super().validate_config(config)
        if not is_valid:
            return is_valid, error
        if not is_valid_telegram_bot_token(config.get("bot_token")):
            return False, TELEGRAM_BOT_TOKEN_ERROR
        return True, ""

    @staticmethod
    def _bounded_parts(message: AlertMessage) -> Tuple[str, str, List[Tuple[str, str]], str]:
        """Plain-text title, body, metadata and source, sized to fit one message.

        Lengths are measured before escaping because Telegram counts the text
        after it parses the markup, so escapes and tags do not use the budget.
        """
        title = truncate_text(message.title or "Notification", _TITLE_LIMIT)
        source = truncate_text(message.source or "", _SOURCE_LIMIT)
        metadata: List[Tuple[str, str]] = []
        metadata_used = 0
        for key, value in (message.metadata or {}).items():
            if not isinstance(value, (str, int, float, bool)):
                continue
            label = truncate_text(key.replace("_", " ").title(), _METADATA_VALUE_LIMIT)
            text = truncate_text(str(value), _METADATA_VALUE_LIMIT)
            line_length = len(label) + len(text) + 3  # "label: value\n"
            if metadata_used + line_length > _METADATA_BUDGET:
                break
            metadata.append((label, text))
            metadata_used += line_length
        used = len(title) + len(source) + metadata_used + _FIXED_OVERHEAD
        body = truncate_text(message.message, _TEXT_LIMIT - used)
        return title, body, metadata, source

    def _format_html_message(self, message: AlertMessage) -> str:
        """Format message as HTML for Telegram."""
        emoji = self.TYPE_EMOJI.get(message.notification_type, "📢")
        title, body, metadata, source = self._bounded_parts(message)
        parts = []

        # Title with emoji
        parts.append(f"{emoji} <b>{self._escape_html(title)}</b>")

        parts.append("")

        # Message body
        parts.append(self._escape_html(body))

        # Metadata
        if metadata:
            parts.append("")
            for label, value in metadata:
                parts.append(f"<b>{label}:</b> {self._escape_html(value)}")

        # Footer
        parts.append("")
        footer_parts = []
        if source:
            footer_parts.append(f"📍 {self._escape_html(source)}")
        footer_parts.append(f"🕐 {message.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        parts.append("<i>" + " | ".join(footer_parts) + "</i>")

        return "\n".join(parts)

    def _format_markdown_message(self, message: AlertMessage) -> str:
        """Format message as Markdown for Telegram."""
        emoji = self.TYPE_EMOJI.get(message.notification_type, "📢")
        title, body, metadata, source = self._bounded_parts(message)
        parts = []

        # Title with emoji
        parts.append(f"{emoji} *{self._escape_markdown(title)}*")

        parts.append("")

        # Message body
        parts.append(self._escape_markdown(body))

        # Metadata
        if metadata:
            parts.append("")
            for label, value in metadata:
                parts.append(f"*{label}:* {self._escape_markdown(value)}")

        # Footer
        parts.append("")
        footer_parts = []
        if source:
            footer_parts.append(f"📍 {self._escape_markdown(source)}")
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
        # The chat id is masked on every read path (GH #1026), so it is not
        # logged either; the method name identifies the destination.
        logger.debug(
            "[ALERTS-TELEGRAM] Sending to %s: type=%s title=%r",
            self.name, message.notification_type, message.title,
        )

        if not bot_token or not chat_id:
            logger.error("[ALERTS-TELEGRAM] Telegram method %s: Missing bot_token or chat_id", self.name)
            return False
        # Rows written before validate_config checked the token shape are
        # re-checked here, so a malformed token never rewrites the URL path.
        if not is_valid_telegram_bot_token(bot_token):
            logger.error("[ALERTS-TELEGRAM] Telegram method %s: Bot token is not in the <id>:<secret> format", self.name)
            return False

        parse_mode = self.config.get("parse_mode", "HTML")

        # Format message based on parse mode
        if parse_mode == "HTML":
            text = self._format_html_message(message)
        elif parse_mode == "Markdown" or parse_mode == "MarkdownV2":
            text = self._format_markdown_message(message)
        else:
            text = truncate_text(self.format_message(message), _TEXT_LIMIT)

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
                        logger.debug("[ALERTS-TELEGRAM] Message sent successfully to %s", self.name)
                        return True
                    elif response.status == 429:
                        # Rate limited
                        retry_after = result.get("parameters", {}).get("retry_after", "unknown")
                        logger.warning(
                            "[ALERTS-TELEGRAM] Telegram method %s: Rate limited, retry after %ss",
                            self.name, retry_after,
                        )
                        return False
                    else:
                        error_desc = result.get("description", "Unknown error")
                        logger.error(
                            "[ALERTS-TELEGRAM] Telegram method %s: Failed with status %s: %s",
                            self.name, response.status, error_desc,
                        )
                        return False

        except aiohttp.ClientError as e:
            logger.error("[ALERTS-TELEGRAM] Telegram method %s: Connection error: %s", self.name, e)
            return False
        except Exception as e:
            logger.exception("[ALERTS-TELEGRAM] Telegram method %s: Unexpected error: %s", self.name, e)
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test the Telegram bot connection."""
        logger.info("[ALERTS-TELEGRAM] Testing connection for method %s", self.name)
        bot_token = self.config.get("bot_token")
        chat_id = self.config.get("chat_id")

        if not bot_token:
            logger.debug("[ALERTS-TELEGRAM] Test failed: no bot_token for %s", self.name)
            return False, "Bot token not configured"
        if not chat_id:
            logger.debug("[ALERTS-TELEGRAM] Test failed: no chat_id for %s", self.name)
            return False, "Chat ID not configured"
        # Same guard as send(): the token goes into the getMe URL path below.
        if not is_valid_telegram_bot_token(bot_token):
            logger.debug("[ALERTS-TELEGRAM] Test failed: malformed bot_token for %s", self.name)
            return False, TELEGRAM_BOT_TOKEN_ERROR

        # First, verify the bot token by calling getMe
        logger.debug("[ALERTS-TELEGRAM] Verifying bot token via getMe")
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
                    logger.debug("[ALERTS-TELEGRAM] Bot verified: @%s", bot_username)

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
                return False, (
                    f"Bot @{bot_username} verified but failed to send a message to the "
                    "configured chat. Check the chat ID and that the bot is in that chat."
                )

        except aiohttp.ClientError as e:
            return False, f"Connection error: {str(e)}"
        except Exception as e:
            return False, f"Error: {str(e)}"
