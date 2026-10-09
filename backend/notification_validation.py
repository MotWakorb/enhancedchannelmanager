"""
Shared validators for Discord and Telegram alert destinations (GH #1026).

One place for the shape checks that the Discord / Telegram alert methods, the
settings test endpoints, the M3U digest sender and the legacy-settings
migration all apply. Before this module each caller carried its own copy, and
they had drifted: the settings save path accepted the canary/ptb Discord
hosts while the alert method and the M3U digest rejected them.
"""
import re
from typing import Any

# Discord webhook URL prefix — accepts the canonical discord.com host, the legacy
# discordapp.com host, and the canary/ptb subdomains. Anchored to the start so
# only well-formed webhook URLs are admitted. The webhook URL is POSTed
# verbatim, so this allowlist is the SSRF guard for every Discord sender.
DISCORD_WEBHOOK_RE = re.compile(
    r"^https://(discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)/api/webhooks/"
)

# Telegram bot tokens are ``<bot id>:<secret>``. The token is interpolated into
# the Bot API URL path, so anything outside this shape is refused rather than
# allowed to rewrite the request path.
TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

DISCORD_WEBHOOK_ERROR = (
    "Invalid Discord webhook URL — must be an https webhook on "
    "discord.com / discordapp.com (e.g. https://discord.com/api/webhooks/...)."
)
TELEGRAM_BOT_TOKEN_ERROR = "Invalid bot token format"


def is_valid_discord_webhook(url: Any) -> bool:
    """Whether ``url`` is a Discord webhook URL on an allowlisted host."""
    return isinstance(url, str) and bool(DISCORD_WEBHOOK_RE.match(url))


def is_valid_telegram_bot_token(token: Any) -> bool:
    """Whether ``token`` has the ``<bot id>:<secret>`` Telegram token shape."""
    return isinstance(token, str) and bool(TELEGRAM_BOT_TOKEN_RE.match(token))
