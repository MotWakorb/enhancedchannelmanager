"""Plex API client (bd-r5f0c.2, epic bd-r5f0c).

Read-only async client for the operator's Plex Media Server. Single concern:
fetch the ``/status/sessions`` feed so downstream code (plex_cache,
plex_resolver, BandwidthTracker enrichment) can cross-reference live
Plex viewers against ECM's active streams and attribute the real Plex
username instead of collapsing every Plex-mediated pull to the proxy IP.

This module is intentionally narrow:

* No caching here — that lives in plex_cache's wrapper around this client.
* No resolver / matching logic — plex_resolver owns ``ECM stream → Plex user``.
* No Settings UI plumbing — W4 wires ``test_connection`` into Settings.

Mirrors ``emby_client.py``'s shape (async httpx, ``[PLEX]`` log prefix,
dataclass DTOs, dedicated error class) so the patterns stay consistent
across the two outbound HTTP clients.

Plex-specific differences from Emby:
* Auth header: ``X-Plex-Token: <token>`` (NOT ``X-Emby-Token``)
* Endpoint: ``GET /status/sessions`` (NOT ``/Sessions``)
* Response format: XML (NOT JSON). Parsed via stdlib
  ``xml.etree.ElementTree`` — no additional deps required. Plex is a
  configured-by-operator trusted upstream so the XXE risk of stdlib is
  acceptable (defusedxml is unnecessary here).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlexSession:
    """A single live Plex session as exposed by ``GET /status/sessions``.

    Fields are a deliberate subset of the upstream Plex XML response — only
    what the user-attribution resolver (plex_resolver) actually needs. Naming
    is snake_case ECM convention, mapped from Plex's XML attributes in
    ``PlexClient.get_sessions``.

    Attributes:
        session_id: Plex's rating key for the playing item (``ratingKey``
            attribute on the ``<Video>`` element). Useful only for debugging.
        user_id: Plex user numeric ID (``User/@id`` in the XML). Persisted to
            ``session_telemetry.plex_user_id`` once W4 lands.
        user_name: Human-readable Plex username (``User/@title``). Persisted
            to ``session_telemetry.plex_user_name``.
        remote_endpoint: Client IP the Plex session originated from
            (``Player/@address``). Used as a sanity check in the resolver.
        now_playing_item_name: The ``Video/@title`` attribute — for live TV
            this is often ``"<number> | <channel_name>"`` or just the channel
            name. ``None`` if unparseable.
        last_activity_date: ``datetime`` parsed from ``Video/@lastViewedAt``
            (epoch seconds). Used to break ties when multiple Plex sessions
            match the same ECM stream (most-recent-wins). ``None`` when the
            attribute is absent or unparseable.
    """

    session_id: str
    user_id: str
    user_name: str
    remote_endpoint: str
    now_playing_item_name: str | None
    last_activity_date: datetime | None


class PlexClientError(Exception):
    """Raised by :class:`PlexClient` on any auth / network / non-2xx /
    malformed-XML failure.

    Callers decide whether to swallow (e.g. :meth:`PlexClient.test_connection`
    returns ``False`` on this) or surface (e.g. the resolver should log and
    fall back to the proxy-IP attribution).

    The underlying exception is preserved in ``__cause__`` where applicable so
    structured loggers can still capture root cause without re-raising.
    """


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


# 5s connect / 10s read — matches the Emby client's timeout tuning. Tight
# enough that a misconfigured Plex URL fails the Settings UI 'Test
# Connection' button promptly, but generous enough to absorb a slow LAN
# response under load.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)


class PlexClient:
    """Async HTTP client for the Plex ``/status/sessions`` endpoint.

    Stateless across calls — Plex's ``X-Plex-Token`` auth header is
    attached per-request, no token-refresh lifecycle to manage.
    """

    def __init__(self, base_url: str, api_key: str, timeout: httpx.Timeout = _DEFAULT_TIMEOUT):
        # Strip exactly one trailing slash so ``base + "/status/sessions"``
        # never produces a double-slash. Preserve any sub-path the operator
        # configured for reverse-proxy setups.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_sessions(self) -> list[PlexSession]:
        """Fetch the live Plex session list.

        Always hits the API — caching is deliberately not in this layer
        (plex_cache owns the TTL cache around this method).

        Returns:
            List of :class:`PlexSession`. Empty list when Plex reports no
            active sessions (a normal idle-server state, not an error).

        Raises:
            PlexClientError: On 401 (bad/expired token), any non-2xx
                response, network failure, or malformed XML. The original
                exception is preserved as ``__cause__`` where applicable.
        """
        url = f"{self.base_url}/status/sessions"
        headers = {"X-Plex-Token": self.api_key}

        logger.debug("[PLEX] GET %s", url)
        try:
            response = await self._client.request("GET", url, headers=headers)
        except httpx.HTTPError as exc:
            # ConnectError, ReadTimeout, RemoteProtocolError, etc. — any
            # transport-level failure. Wrap so callers see one exception
            # type regardless of whether the failure was DNS, TCP, or TLS.
            logger.warning("[PLEX] /status/sessions request failed: %s", exc)
            raise PlexClientError(f"Plex request failed: {exc}") from exc

        if response.status_code == 401:
            # Surface 401 distinctly — the operator's most common failure
            # mode is a wrong/revoked token, and the Settings UI surface
            # will route on this string.
            logger.warning("[PLEX] /status/sessions returned 401 unauthorized")
            raise PlexClientError(
                "Plex /status/sessions returned 401 unauthorized — check token"
            )

        if response.status_code >= 400:
            logger.warning(
                "[PLEX] /status/sessions returned non-2xx: status=%s",
                response.status_code,
            )
            raise PlexClientError(
                f"Plex /status/sessions returned {response.status_code}"
            )

        sessions = _parse_sessions_xml(response.text)
        logger.debug("[PLEX] /status/sessions returned %d sessions", len(sessions))
        return sessions

    async def test_connection(self) -> bool:
        """Verify the configured URL + token reach a working Plex server.

        Wired into the Settings UI (W4) 'Test Connection' button.
        Swallows :class:`PlexClientError` and returns ``False`` so the UI
        handler only needs to render a bool.

        Returns:
            ``True`` if ``/status/sessions`` returned a 2xx response,
            ``False`` on any auth / network / server failure.
        """
        try:
            await self.get_sessions()
        except PlexClientError as exc:
            logger.info("[PLEX] test_connection failed: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` connection pool.

        Call from a lifespan shutdown handler or test teardown to avoid
        leaking sockets.
        """
        await self._client.aclose()


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------


def _parse_sessions_xml(xml_text: str) -> list[PlexSession]:
    """Parse Plex ``/status/sessions`` XML into a list of :class:`PlexSession`.

    Plex returns a ``<MediaContainer>`` root element containing zero or more
    ``<Video>`` (or ``<Track>`` for music) child elements. Each ``<Video>``
    has nested ``<User>`` and ``<Player>`` elements.

    This function:
    * Handles an empty ``<MediaContainer/>`` → returns ``[]``
    * Skips elements without a ``<User>`` child (server-side sessions with
      no authenticated user, e.g. local-network anonymous sessions)
    * Wraps malformed XML in :class:`PlexClientError`

    Raises:
        PlexClientError: On any XML parse error.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("[PLEX] Failed to parse /status/sessions XML: %s", exc)
        raise PlexClientError(f"Plex /status/sessions returned malformed XML: {exc}") from exc

    sessions: list[PlexSession] = []
    # Plex session items can be <Video> (live TV, movies, episodes) or
    # <Track> (music). We look for both under the MediaContainer root.
    for item in root:
        session = _map_item(item)
        if session is not None:
            sessions.append(session)

    return sessions


def _map_item(item: ET.Element) -> PlexSession | None:
    """Map one Plex session XML element to a :class:`PlexSession`.

    Returns ``None`` when the element lacks a ``<User>`` child — this
    can happen with server-side anonymous sessions (local-network
    unauthenticated streams). The resolver can only attribute a user
    when a real user identity is present.
    """
    user_el = item.find("User")
    if user_el is None:
        # No user identity — skip this session; the resolver cannot
        # attribute an anonymous session to a Plex account.
        return None

    player_el = item.find("Player")
    remote_endpoint = player_el.get("address", "") if player_el is not None else ""

    # Parse lastViewedAt (epoch seconds) into a timezone-aware datetime.
    last_viewed_raw = item.get("lastViewedAt")
    last_activity: datetime | None = None
    if last_viewed_raw is not None:
        try:
            last_activity = datetime.fromtimestamp(int(last_viewed_raw), tz=timezone.utc)
        except (ValueError, OSError):
            # Malformed timestamp — treat as absent rather than raise.
            pass

    return PlexSession(
        session_id=item.get("ratingKey", ""),
        user_id=user_el.get("id", ""),
        user_name=user_el.get("title", ""),
        remote_endpoint=remote_endpoint,
        now_playing_item_name=item.get("title"),
        last_activity_date=last_activity,
    )
