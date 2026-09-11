"""Provider-facing User-Agent selection for direct previews and ECM probes.

Upstream contract and fixture provenance: docs/dispatcharr_api.md, Stream User-Agent.
"""

import asyncio
import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)

StreamUserAgent = Literal["dispatcharr", "chrome", "firefox", "safari", "vlc", "tivimate"]

# Version-labelled compatibility presets, not a promise of browser currency.
USER_AGENT_PRESETS = {
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "vlc": "VLC/3.0.20 LibVLC/3.0.20",
    "tivimate": "TiviMate/5.1.6 (Android 12)",
}
UNKNOWN_VERSION_USER_AGENT = "Dispatcharr/unknown"
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+._a-zA-Z0-9]*)")


class StreamUserAgentError(RuntimeError):
    """Configuration could not be resolved; messages contain only fixed safe text."""


def validate_user_agent(value: str) -> str:
    """Accept a bounded printable ASCII header, rejecting injection and bad config.

    Raises:
        StreamUserAgentError: Empty, oversized, non-ASCII or control-containing value.
    """
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 512
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        raise StreamUserAgentError("Dispatcharr User-Agent configuration is malformed")
    return value.strip()


class StreamUserAgentResolver:
    """Lazy configuration snapshot, owned by one operation or explicit batch.

    Args:
        client: Connected Dispatcharr client.
        selection: Persisted preset key. None reads the current ECM setting.
    """

    def __init__(self, client, selection: StreamUserAgent | None = None):
        if selection is None:
            from config import get_settings
            selection = get_settings().stream_user_agent
        self.client = client
        self.selection = selection
        self._cache = {}
        self._lock = asyncio.Lock()

    async def _read(self, key, method, *args):
        async with self._lock:
            if key not in self._cache:
                try:
                    # Existing client methods can otherwise pass timeout=None.
                    self._cache[key] = await asyncio.wait_for(method(*args), timeout=10)
                except Exception:
                    logger.warning("[STREAM-UA] Dispatcharr configuration fetch failed (%s)", key[0])
                    raise StreamUserAgentError("Could not load Dispatcharr User-Agent configuration") from None
            return self._cache[key]

    async def _agent(self, agent_id):
        if agent_id in (None, ""):
            return None
        if isinstance(agent_id, str) and agent_id.isascii() and agent_id.isdecimal() and len(agent_id) <= 20:
            agent_id = int(agent_id)
        if type(agent_id) is not int:
            raise StreamUserAgentError("Dispatcharr User-Agent reference is malformed")
        rows = await self._read(("user-agents",), self.client.get_user_agents)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise StreamUserAgentError("Dispatcharr User-Agent response is malformed")
        for row in rows:
            if row.get("id") == agent_id:
                value = row.get("user_agent")
                # Upstream falls through on an empty UA; is_active is not consulted.
                return validate_user_agent(value) if value not in (None, "") else None
        return None

    async def resolve(self, stream: dict | None = None, *, stream_id: int | None = None) -> str:
        """Resolve override, account, global default, then Dispatcharr/version.

        Raises:
            StreamUserAgentError: Required configuration failed or is malformed.
        """
        if self.selection in USER_AGENT_PRESETS:
            return USER_AGENT_PRESETS[self.selection]
        if self.selection != "dispatcharr":
            raise StreamUserAgentError("ECM stream User-Agent selection is invalid")
        if stream is None and stream_id is not None:
            stream = await self._read(("stream", stream_id), self.client.get_stream, stream_id)
            if stream is None:
                raise StreamUserAgentError("Dispatcharr stream is unavailable for User-Agent resolution")
        if stream is not None and not isinstance(stream, dict):
            raise StreamUserAgentError("Dispatcharr stream response is malformed")
        account_id = (stream or {}).get("m3u_account")
        if account_id is not None:
            if type(account_id) is not int:
                raise StreamUserAgentError("Dispatcharr M3U account reference is malformed")
            account = await self._read(("account", account_id), self.client.get_m3u_account, account_id)
            if not isinstance(account, dict):
                raise StreamUserAgentError("Dispatcharr M3U account response is malformed")
            value = await self._agent(account.get("user_agent"))
            if value is not None:
                return value

        rows = await self._read(("core-settings",), self.client.get_core_settings)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise StreamUserAgentError("Dispatcharr core settings response is malformed")
        for row in rows:
            if row.get("key") == "stream_settings":
                settings = row.get("value")
                if not isinstance(settings, dict):
                    raise StreamUserAgentError("Dispatcharr stream settings are malformed")
                value = await self._agent(settings.get("default_user_agent"))
                if value is not None:
                    return value
                break

        if "fallback" not in self._cache:
            try:
                metadata = await self.client.get_version(timeout=5, retry_on_401=False)
                version = metadata.get("version") if isinstance(metadata, dict) else None
            except Exception:
                version = None
            if isinstance(version, str) and len(version) <= 64 and _VERSION_RE.fullmatch(version):
                self._cache["fallback"] = f"Dispatcharr/{version}"
            else:
                logger.warning("[STREAM-UA] Dispatcharr version unavailable; using Dispatcharr/unknown")
                self._cache["fallback"] = UNKNOWN_VERSION_USER_AGENT
        return self._cache["fallback"]
