"""Emby API client (bd-6c0g6, epic bd-2cenq).

Read-only async client for the operator's Emby server. Single concern:
fetch the ``/Sessions`` feed so downstream code (bd-gpeot cache,
bd-6802c resolver, BandwidthTracker enrichment) can cross-reference live
Emby viewers against ECM's active streams and attribute the real Emby
username instead of collapsing every Emby-mediated pull to the proxy IP.

This module is intentionally narrow:

* No caching here — that lives in bd-gpeot's wrapper around this client.
* No resolver / matching logic — bd-6802c owns ``ECM stream → Emby user``.
* No Settings UI plumbing — bd-8wc6q wires ``test_connection`` into Settings.

Mirrors ``dispatcharr_client.py``'s shape (async httpx, ``[EMBY]`` log
prefix, dataclass DTOs, dedicated error class) so the patterns stay
consistent across the two outbound HTTP clients.
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
class EmbySession:
    """A single live Emby session as exposed by ``GET /Sessions``.

    Fields are a deliberate subset of the upstream Emby response — only
    what the user-attribution resolver (bd-6802c) actually needs. Naming
    is snake_case ECM convention, mapped from Emby's PascalCase response
    payload in ``EmbyClient.get_sessions``.

    Attributes:
        session_id: Emby's session identifier (``Id`` in the upstream
            payload). Useful only for debugging — the resolver matches on
            ``user_id`` / ``user_name``.
        user_id: Emby user UUID (``UserId``). Persisted to
            ``session_telemetry.emby_user_id`` once bd-2cenq lands.
        user_name: Human-readable Emby username (``UserName``). Persisted
            to ``session_telemetry.emby_user_name``.
        remote_endpoint: Client IP the Emby session originated from
            (``RemoteEndPoint``). Used as a sanity check in the resolver.
        now_playing_item_name: ``NowPlayingItem.Name`` if the session is
            actively playing something, else ``None`` (idle session).
            For live-TV sessions Emby formats this as
            ``"<channel_number> | <channel_name>"`` (e.g.
            ``"408 | ESPN"``); for VOD it is the movie/episode title.
        now_playing_channel_name: ``NowPlayingItem.ChannelName`` for live
            TV sessions, else ``None`` (VOD or idle). NOTE: live observation
            shows this is often ``None`` even on ``Type='TvChannel'`` items —
            Emby uses ``Name`` for the live-TV display string. The resolver
            therefore does not rely on this field for the primary live-TV
            match (parses ``now_playing_item_name`` instead).
        channel_number: ``NowPlayingItem.ChannelNumber`` for live-TV
            sessions, else ``None``. String per the upstream payload
            (Dispatcharr stores channel numbers as numeric but Emby
            surfaces them as strings; the resolver string-compares so
            preserving the raw type avoids an int-parse step that would
            reject sub-channel numbers like ``"408.1"``).
        last_activity_date: ISO timestamp string of the last server-side
            activity for this session (``LastActivityDate``). Used to
            break ties when multiple Emby sessions match the same ECM
            stream (most-recent-wins per bd-2cenq matching algorithm).
    """

    session_id: str
    user_id: str
    user_name: str
    remote_endpoint: str
    now_playing_item_name: str | None
    now_playing_channel_name: str | None
    last_activity_date: str | None
    channel_number: str | None = None


@dataclass(frozen=True)
class EmbyLiveTvChannel:
    """A single Emby Live TV channel as exposed by ``GET /LiveTv/Channels``.

    Deliberately minimal — the "Clear Emby Logos" feature (GH #475, bd-v9tp7)
    only needs the item ``Id`` to address the image endpoint and the ``Name``
    for operator-facing progress/logging.

    Attributes:
        channel_id: Emby item id (``Id``). Addresses
            ``DELETE /Items/{channel_id}/Images/{type}``.
        name: Display name (``Name``), e.g. ``"ESPN"``. Logging only.
        channel_number: ``ChannelNumber`` if present (string, verbatim), used
            only to label progress and to support an optional future
            channel-number filter — never parsed.
    """

    channel_id: str
    name: str
    channel_number: str | None = None


# Emby channel-logo image types the clear-logos feature may target. Mirrors
# Channel Identifiarr's whitelist (GH #475): the primary logo plus the two
# "light" theme variants Emby stores separately. Used to reject arbitrary
# operator-supplied image types before issuing any DELETE.
VALID_LOGO_IMAGE_TYPES: frozenset[str] = frozenset(
    {"Primary", "LogoLight", "LogoLightColor"}
)


class EmbyClientError(Exception):
    """Raised by :class:`EmbyClient` on any auth / network / non-2xx
    failure.

    Callers decide whether to swallow (e.g. :meth:`EmbyClient.test_connection`
    returns ``False`` on this) or surface (e.g. the resolver should log and
    fall back to the proxy-IP attribution).

    The underlying ``httpx`` exception is preserved in ``__cause__`` so
    structured loggers can still capture root cause without re-raising.
    """


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


# 5s connect / 10s read — bead spec. Tight enough that a misconfigured
# Emby URL fails the Settings UI 'Test Connection' button promptly, but
# generous enough to absorb a slow LAN response under load.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)


class EmbyClient:
    """Async HTTP client for the Emby ``/Sessions`` endpoint.

    Stateless across calls — Emby's ``X-Emby-Token`` auth header is
    attached per-request, no token-refresh lifecycle to manage (unlike
    Dispatcharr's JWT flow).
    """

    def __init__(self, base_url: str, api_key: str):
        # Strip exactly one trailing slash so ``base + "/Sessions"`` never
        # produces a double-slash. Preserve any sub-path the operator
        # configured for reverse-proxy setups (``http://proxy/emby``).
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_sessions(self) -> list[EmbySession]:
        """Fetch the live Emby session list.

        Always hits the API — caching is deliberately not in this layer
        (bd-gpeot owns the TTL cache around this method).

        Returns:
            List of :class:`EmbySession`. Empty list when Emby reports no
            active sessions (a normal idle-server state, not an error).

        Raises:
            EmbyClientError: On 401 (bad/expired API key), any non-2xx
                response, or any underlying network failure. The original
                exception is preserved as ``__cause__`` where applicable.
        """
        url = f"{self.base_url}/Sessions"
        headers = {"X-Emby-Token": self.api_key}

        logger.debug("[EMBY] GET %s", url)
        try:
            response = await self._client.request("GET", url, headers=headers)
        except httpx.HTTPError as exc:
            # ConnectError, ReadTimeout, RemoteProtocolError, etc. — any
            # transport-level failure. Wrap so callers see one exception
            # type regardless of whether the failure was DNS, TCP, or TLS.
            logger.warning("[EMBY] /Sessions request failed: %s", exc)
            raise EmbyClientError(f"Emby request failed: {exc}") from exc

        if response.status_code == 401:
            # Surface 401 distinctly in the message — the operator's most
            # common failure mode is a wrong/revoked API key, and the
            # Settings UI surface (bd-8wc6q) will route on this string.
            logger.warning("[EMBY] /Sessions returned 401 unauthorized")
            raise EmbyClientError(
                "Emby /Sessions returned 401 unauthorized — check API key"
            )

        if response.status_code >= 400:
            logger.warning(
                "[EMBY] /Sessions returned non-2xx: status=%s",
                response.status_code,
            )
            raise EmbyClientError(
                f"Emby /Sessions returned {response.status_code}"
            )

        payload = response.json()
        if not payload:
            # Empty list = no active sessions. Normal idle state, not an
            # error — return ``[]`` so resolver iteration works directly.
            return []

        sessions = [_map_session(item) for item in payload]
        logger.debug("[EMBY] /Sessions returned %d sessions", len(sessions))
        return sessions

    async def get_livetv_channels(self) -> list[EmbyLiveTvChannel]:
        """Fetch the operator's Emby Live TV channel list.

        Backs the "Clear Emby Logos" feature (GH #475, bd-v9tp7): the returned
        item ids address ``DELETE /Items/{id}/Images/{type}``. This call is also
        the auth gate for the clear-logos job — a bad/expired key surfaces here
        as an :class:`EmbyClientError` (401) so the job aborts before issuing a
        single delete.

        Returns:
            List of :class:`EmbyLiveTvChannel`. Empty list when Emby reports no
            Live TV channels (a configured-but-empty server, not an error).

        Raises:
            EmbyClientError: On 401 (bad/expired API key), any non-2xx
                response, or any underlying network failure.
        """
        url = f"{self.base_url}/LiveTv/Channels"
        headers = {"X-Emby-Token": self.api_key}

        logger.debug("[EMBY] GET %s", url)
        try:
            response = await self._client.request("GET", url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("[EMBY] /LiveTv/Channels request failed: %s", exc)
            raise EmbyClientError(f"Emby request failed: {exc}") from exc

        if response.status_code == 401:
            logger.warning("[EMBY] /LiveTv/Channels returned 401 unauthorized")
            raise EmbyClientError(
                "Emby /LiveTv/Channels returned 401 unauthorized — check API key"
            )
        if response.status_code >= 400:
            logger.warning(
                "[EMBY] /LiveTv/Channels returned non-2xx: status=%s",
                response.status_code,
            )
            raise EmbyClientError(
                f"Emby /LiveTv/Channels returned {response.status_code}"
            )

        payload = response.json() or {}
        # Emby wraps the list in ``{"Items": [...], "TotalRecordCount": N}``.
        items = payload.get("Items", []) if isinstance(payload, dict) else []
        channels = [
            _map_livetv_channel(item)
            for item in items
            if item.get("Id")  # skip malformed rows with no addressable id
        ]
        logger.debug("[EMBY] /LiveTv/Channels returned %d channels", len(channels))
        return channels

    async def delete_item_image(self, item_id: str, image_type: str) -> bool:
        """Delete one cached image from an Emby item (GH #475, bd-v9tp7).

        Issues ``DELETE /Items/{item_id}/Images/{image_type}`` — the same call
        Channel Identifiarr uses to flush a stale channel logo so Emby
        re-fetches it from its source on next access. No request body, no query
        params, no index suffix (deletes the whole image type).

        Args:
            item_id: Emby item id (a Live TV channel id).
            image_type: One of :data:`VALID_LOGO_IMAGE_TYPES`. Validated by the
                caller; passed through verbatim into the path.

        Returns:
            ``True`` if Emby deleted the image (2xx). ``False`` if the item had
            no image of that type (404) — a normal, non-fatal skip.

        Raises:
            EmbyClientError: On 401 (auth) or any other non-2xx, or on a
                network-level failure. Callers iterating many channels catch
                this per-channel and continue; the up-front
                :meth:`get_livetv_channels` call is the auth gate, so a 401 here
                is unexpected (key revoked mid-run).
        """
        url = f"{self.base_url}/Items/{item_id}/Images/{image_type}"
        headers = {"X-Emby-Token": self.api_key}

        logger.debug("[EMBY] DELETE %s", url)
        try:
            response = await self._client.request("DELETE", url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "[EMBY] image delete failed item=%s type=%s: %s",
                item_id, image_type, exc,
            )
            raise EmbyClientError(f"Emby image delete failed: {exc}") from exc

        if response.status_code == 404:
            # No image of this type on this channel — nothing to clear. Normal.
            return False
        if response.status_code == 401:
            logger.warning("[EMBY] image delete returned 401 unauthorized")
            raise EmbyClientError(
                "Emby image delete returned 401 unauthorized — check API key"
            )
        if response.status_code >= 400:
            logger.warning(
                "[EMBY] image delete returned non-2xx item=%s type=%s status=%s",
                item_id, image_type, response.status_code,
            )
            raise EmbyClientError(
                f"Emby image delete returned {response.status_code}"
            )
        return True

    async def test_connection(self) -> bool:
        """Verify the configured URL + API key reach a working Emby server.

        Wired into the Settings UI (bd-8wc6q) 'Test Connection' button.
        Swallows :class:`EmbyClientError` and returns ``False`` so the UI
        handler only needs to render a bool.

        Returns:
            ``True`` if ``/Sessions`` returned a 2xx response, ``False``
            on any auth / network / server failure.
        """
        try:
            await self.get_sessions()
        except EmbyClientError as exc:
            logger.info("[EMBY] test_connection failed: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` connection pool.

        Mirrors :meth:`DispatcharrClient.close` — call from a lifespan
        shutdown handler or test teardown to avoid leaking sockets.
        """
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _map_livetv_channel(item: dict) -> EmbyLiveTvChannel:
    """Map one raw Emby Live TV channel dict to :class:`EmbyLiveTvChannel`.

    ``ChannelNumber`` is preserved verbatim as a string (Emby surfaces it that
    way and we never parse it) and defaults to ``None`` when absent.
    """
    channel_number_raw = item.get("ChannelNumber")
    return EmbyLiveTvChannel(
        channel_id=str(item.get("Id", "")),
        name=item.get("Name", "") or "",
        channel_number=channel_number_raw if channel_number_raw is not None else None,
    )


def _map_session(item: dict) -> EmbySession:
    """Map one raw Emby session dict to an :class:`EmbySession`.

    Defensive on the ``NowPlayingItem`` sub-object — an idle session
    omits the field entirely, and ``ChannelName`` is only present for
    live-TV sessions (VOD playback has ``Name`` but no ``ChannelName``).
    """
    now_playing = item.get("NowPlayingItem") or {}
    # ChannelNumber is a string in Emby's payload — preserve verbatim
    # (do NOT int-cast) so sub-channel numbers like "408.1" survive.
    channel_number_raw = now_playing.get("ChannelNumber")
    return EmbySession(
        session_id=item.get("Id", ""),
        user_id=item.get("UserId", ""),
        user_name=item.get("UserName", ""),
        remote_endpoint=item.get("RemoteEndPoint", ""),
        now_playing_item_name=now_playing.get("Name"),
        now_playing_channel_name=now_playing.get("ChannelName"),
        last_activity_date=item.get("LastActivityDate"),
        channel_number=channel_number_raw if channel_number_raw is not None else None,
    )
