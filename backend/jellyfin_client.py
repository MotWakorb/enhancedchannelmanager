"""Jellyfin API client (bd-r5f0c.3, epic bd-r5f0c).

Read-only async client for the operator's Jellyfin server. Single concern:
fetch the ``/Sessions`` feed so downstream code (jellyfin_cache,
jellyfin_resolver, BandwidthTracker enrichment) can cross-reference live
Jellyfin viewers against ECM's active streams and attribute the real Jellyfin
username instead of collapsing every Jellyfin-mediated pull to the proxy IP.

Jellyfin is an Emby fork and the ``/Sessions`` JSON shape is structurally
identical: a top-level array of session objects with ``Id``, ``UserId``,
``UserName``, ``NowPlayingItem.Name``, ``RemoteEndPoint``,
``LastActivityDate``. The critical difference is the auth header:

  Jellyfin: ``Authorization: MediaBrowser Token="<api_key>"``
  Emby:     ``X-Emby-Token: <api_key>``

Operator note: Jellyfin API keys are server-issued via Dashboard → API Keys
(Administration → API Keys). The key is a 32+ character hex string. This is
NOT a user password — the key grants read access to the ``/Sessions``
endpoint. ECM only needs read scope; restrict the key's label so it's
identifiable in the Jellyfin audit log (e.g. "ECM attribution").

This module is intentionally narrow:

* No caching here — that lives in jellyfin_cache's wrapper around this client.
* No resolver / matching logic — jellyfin_resolver owns ``ECM stream →
  Jellyfin user``.
* No Settings UI plumbing — W4 wires ``test_connection`` into Settings.

Mirrors ``emby_client.py``'s shape (async httpx, ``[JELLYFIN]`` log prefix,
dataclass DTOs, dedicated error class). Forked rather than subclassed because:

1. Auth header difference makes inheritance fragile — Emby uses
   ``X-Emby-Token`` while Jellyfin uses ``Authorization: MediaBrowser Token``.
2. Live-TV item-name format requires per-source tolerance — Jellyfin's
   ``NowPlayingItem.Name`` often lacks the ``"<number> | "`` pipe prefix that
   Emby uses, so the resolver must handle both shapes independently.
3. Smaller blast radius — a bug in either client cannot break the other if
   they share no code path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JellyfinSession:
    """A single live Jellyfin session as exposed by ``GET /Sessions``.

    Fields are a deliberate subset of the upstream Jellyfin response — only
    what the user-attribution resolver (jellyfin_resolver) actually needs.
    Naming is snake_case ECM convention, mapped from Jellyfin's PascalCase
    response payload in ``JellyfinClient.get_sessions``.

    The JSON shape is structurally identical to Emby's — Jellyfin is an Emby
    fork and the Sessions API was not redesigned. ``now_playing_item_name``
    differs in practice: Jellyfin often surfaces just the channel name (e.g.
    ``"ESPN"``) rather than Emby's ``"<number> | <channel>"`` pattern, though
    some installs do use the pipe format.

    Attributes:
        session_id: Jellyfin's session identifier (``Id`` in the upstream
            payload). Useful only for debugging — the resolver matches on
            ``user_id`` / ``user_name``.
        user_id: Jellyfin user UUID (``UserId``). Persisted to
            ``session_telemetry.jellyfin_user_id`` once W4 lands.
        user_name: Human-readable Jellyfin username (``UserName``). Persisted
            to ``session_telemetry.jellyfin_user_name``.
        remote_endpoint: Client IP the Jellyfin session originated from
            (``RemoteEndPoint``). Used as a sanity check in the resolver.
        now_playing_item_name: ``NowPlayingItem.Name`` if the session is
            actively playing something, else ``None`` (idle session). For
            live-TV sessions this may be ``"ESPN"`` (bare channel name) or
            ``"408 | ESPN"`` (pipe-prefixed) depending on the Jellyfin install
            configuration. The resolver is tolerant of both formats.
        now_playing_channel_name: ``NowPlayingItem.ChannelName`` for live
            TV sessions, else ``None`` (VOD or idle). Often absent even on
            live-TV sessions — the resolver does not rely on this field for
            the primary live-TV match.
        channel_number: ``NowPlayingItem.ChannelNumber`` for live-TV
            sessions, else ``None``. String per the upstream payload
            (preserved verbatim — do NOT int-cast, sub-channel numbers like
            ``"408.1"`` would be truncated).
        last_activity_date: ISO timestamp string of the last server-side
            activity for this session (``LastActivityDate``). Used to
            break ties when multiple Jellyfin sessions match the same ECM
            stream (most-recent-wins).
    """

    session_id: str
    user_id: str
    user_name: str
    remote_endpoint: str
    now_playing_item_name: str | None
    now_playing_channel_name: str | None
    last_activity_date: str | None
    channel_number: str | None = None


class JellyfinClientError(Exception):
    """Raised by :class:`JellyfinClient` on any auth / network / non-2xx
    failure.

    Callers decide whether to swallow (e.g. :meth:`JellyfinClient.test_connection`
    returns ``False`` on this) or surface (e.g. the resolver should log and
    fall back to the proxy-IP attribution).

    The underlying ``httpx`` exception is preserved in ``__cause__`` so
    structured loggers can still capture root cause without re-raising.
    """


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


# 5s connect / 10s read — mirrors emby_client. Tight enough that a
# misconfigured Jellyfin URL fails the Settings UI 'Test Connection' button
# promptly, but generous enough to absorb a slow LAN response under load.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)


class JellyfinClient:
    """Async HTTP client for the Jellyfin ``/Sessions`` endpoint.

    Stateless across calls — Jellyfin's ``Authorization: MediaBrowser Token``
    auth header is set as a default header on the underlying httpx client, so
    every request carries it automatically. No token-refresh lifecycle to
    manage (unlike Dispatcharr's JWT flow).

    Operator note: Jellyfin tokens are server-issued via Dashboard > API Keys.
    Not relevant for this client's operation, but documented here so W7 docs
    can reference it.
    """

    def __init__(self, base_url: str, api_key: str, timeout: httpx.Timeout = _DEFAULT_TIMEOUT):
        # Strip exactly one trailing slash so ``base + "/Sessions"`` never
        # produces a double-slash. Preserve any sub-path the operator
        # configured for reverse-proxy setups (``http://proxy/jellyfin``).
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # Jellyfin auth: Authorization: MediaBrowser Token="<key>"
        # Note the quotes around the token value — this is Jellyfin's auth
        # scheme format. Emby uses X-Emby-Token without quotes.
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f'MediaBrowser Token="{api_key}"'},
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_sessions(self) -> list[JellyfinSession]:
        """Fetch the live Jellyfin session list.

        Always hits the API — caching is deliberately not in this layer
        (jellyfin_cache owns the TTL cache around this method).

        Returns:
            List of :class:`JellyfinSession`. Empty list when Jellyfin reports
            no active sessions (a normal idle-server state, not an error).

        Raises:
            JellyfinClientError: On 401 (bad/expired API key), any non-2xx
                response, or any underlying network failure. The original
                exception is preserved as ``__cause__`` where applicable.
        """
        url = f"{self.base_url}/Sessions"

        logger.debug("[JELLYFIN] GET %s", url)
        try:
            response = await self._client.request("GET", url)
        except httpx.HTTPError as exc:
            # ConnectError, ReadTimeout, RemoteProtocolError, etc. — any
            # transport-level failure. Wrap so callers see one exception
            # type regardless of whether the failure was DNS, TCP, or TLS.
            logger.warning("[JELLYFIN] /Sessions request failed: %s", exc)
            raise JellyfinClientError(f"Jellyfin request failed: {exc}") from exc

        if response.status_code == 401:
            # Surface 401 distinctly — the operator's most common failure
            # mode is a wrong/revoked API key.
            logger.warning("[JELLYFIN] /Sessions returned 401 unauthorized")
            raise JellyfinClientError(
                "Jellyfin /Sessions returned 401 unauthorized — check API key"
            )

        if response.status_code >= 400:
            logger.warning(
                "[JELLYFIN] /Sessions returned non-2xx: status=%s",
                response.status_code,
            )
            raise JellyfinClientError(
                f"Jellyfin /Sessions returned {response.status_code}"
            )

        payload = response.json()
        if not payload:
            # Empty list = no active sessions. Normal idle state, not an
            # error — return ``[]`` so resolver iteration works directly.
            return []

        sessions = [_map_session(item) for item in payload]
        logger.debug("[JELLYFIN] /Sessions returned %d sessions", len(sessions))
        return sessions

    async def test_connection(self) -> bool:
        """Verify the configured URL + API key reach a working Jellyfin server.

        Wired into the Settings UI 'Test Connection' button (W4).
        Swallows :class:`JellyfinClientError` and returns ``False`` so the UI
        handler only needs to render a bool.

        Returns:
            ``True`` if ``/Sessions`` returned a 2xx response, ``False``
            on any auth / network / server failure.
        """
        try:
            await self.get_sessions()
        except JellyfinClientError as exc:
            logger.info("[JELLYFIN] test_connection failed: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` connection pool.

        Mirrors :meth:`EmbyClient.close` — call from a lifespan shutdown
        handler or test teardown to avoid leaking sockets.
        """
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _map_session(item: dict) -> JellyfinSession:
    """Map one raw Jellyfin session dict to a :class:`JellyfinSession`.

    Defensive on the ``NowPlayingItem`` sub-object — an idle session
    omits the field entirely, and ``ChannelName`` is only present for
    live-TV sessions (VOD playback has ``Name`` but no ``ChannelName``).
    """
    now_playing = item.get("NowPlayingItem") or {}
    # ChannelNumber is a string in Jellyfin's payload — preserve verbatim
    # (do NOT int-cast) so sub-channel numbers like "408.1" survive.
    channel_number_raw = now_playing.get("ChannelNumber")
    return JellyfinSession(
        session_id=item.get("Id", ""),
        user_id=item.get("UserId", ""),
        user_name=item.get("UserName", ""),
        remote_endpoint=item.get("RemoteEndPoint", ""),
        now_playing_item_name=now_playing.get("Name"),
        now_playing_channel_name=now_playing.get("ChannelName"),
        last_activity_date=item.get("LastActivityDate"),
        channel_number=channel_number_raw if channel_number_raw is not None else None,
    )
