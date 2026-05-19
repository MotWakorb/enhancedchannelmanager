"""Plex API client (bd-r5f0c.2, epic bd-r5f0c; bd-ma6r3 EPG cross-reference).

Read-only async client for the operator's Plex Media Server. Two concerns:

1. Fetch ``/status/sessions`` so downstream code (plex_cache,
   plex_resolver, BandwidthTracker enrichment) can cross-reference live
   Plex viewers against ECM's active streams and attribute the real Plex
   username instead of collapsing every Plex-mediated pull to the proxy
   IP.
2. (bd-ma6r3) Fetch the EPG-section airings under each DVR so the Plex
   Live TV resolver tier can cross-reference an unknown ``@grandparentTitle``
   (the PROGRAM currently airing — NOT the channel) against the EPG entry
   whose time window contains "now" and pull the ``channelCallSign`` off the
   ``<Media>`` element. Plex's ``/status/sessions`` XML carries the program
   name but NOT the channel for Live TV, so the EPG is the only Plex API
   surface that exposes the channel identity for an active Live TV session.

This module is intentionally narrow:

* No caching here — that lives in plex_cache + plex_epg_cache.
* No resolver / matching logic — plex_resolver owns ``ECM stream → Plex user``.
* No Settings UI plumbing — W4 wires ``test_connection`` into Settings.

Mirrors ``emby_client.py``'s shape (async httpx, ``[PLEX]`` log prefix,
dataclass DTOs, dedicated error class) so the patterns stay consistent
across the two outbound HTTP clients.

Plex-specific differences from Emby:
* Auth header: ``X-Plex-Token: <token>`` (NOT ``X-Emby-Token``)
* Endpoints:

  * ``GET /status/sessions`` — live sessions (NOT ``/Sessions``).
  * ``GET /livetv/dvrs`` — list of configured DVRs (each with a ``key``
    we use to address its EPG provider).
  * ``GET /tv.plex.providers.epg.xmltv:<dvr_key>/sections`` — list of
    EPG sections (Movies / News / Shows / Sports for the typical
    Dispatcharr-HDHomeRun-fronted setup the bead targets).
  * ``GET /tv.plex.providers.epg.xmltv:<dvr_key>/sections/<sid>/all?type=4``
    — episode airings for that section. Each ``<Video>`` carries one
    or more ``<Media>`` elements with ``channelCallSign``,
    ``beginsAt`` / ``endsAt``, and ``protocol="livetv"``. The resolver
    filters to the airing whose ``beginsAt <= now <= endsAt``.

* Response format: XML (NOT JSON). Parsed via stdlib
  ``xml.etree.ElementTree`` — no additional deps required. Plex is a
  configured-by-operator trusted upstream so the XXE risk of stdlib is
  acceptable (defusedxml is unnecessary here).

bd-ma6r3 sizing notes (observed against PO's running Plex 2026-05-18):

* DVR sweep latency for 4 sections (1793 / 53 / 226 / 0 entries): ~0.43s
  sequential. Section 2 (Shows) is the long pole at ~3.3 MB and ~0.33s.
* The Plex API ignores client-side query filters like ``?onAir=1`` —
  filtering to current airings must be done client-side after the parse.
* Each ``<Video>`` may have MULTIPLE ``<Media>`` children covering
  different upcoming airings of the same episode. We emit one
  :class:`PlexEpgEntry` per ``<Media>`` so the resolver can window-filter
  on each ``beginsAt``/``endsAt`` pair independently.
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
class PlexEpgEntry:
    """One Plex EPG airing slot for a Live TV channel (bd-ma6r3).

    Each ``<Media>`` element under a ``<Video>`` returned by
    ``/tv.plex.providers.epg.xmltv:<dvr_key>/sections/<sid>/all?type=4``
    becomes one entry. A ``<Video>`` (episode) can carry multiple
    ``<Media>`` elements representing different upcoming airings of the
    same episode — flattening to one entry per airing is what lets the
    resolver window-filter on the airing whose time range contains
    "now" without needing to flatten inside the resolver.

    Fields are a deliberate subset of the upstream XML — only what the
    resolver tier needs. ``grandparent_title`` is the episode's
    ``<Video>/@grandparentTitle`` (e.g., ``"MLB Baseball"``) and IS
    what Plex's ``/status/sessions`` carries on the live session, so
    the resolver can match a session's ``now_playing_channel_name``
    (which comes from the same ``@grandparentTitle`` on the session)
    against this entry's ``grandparent_title``.

    Attributes:
        grandparent_title: The owning episode's ``@grandparentTitle``
            (e.g., ``"MLB Baseball"``). The resolver matches this
            against the session's ``now_playing_channel_name``.
        channel_call_sign: ``<Media>/@channelCallSign`` — the
            operator-visible channel name including any pipe-prefix
            (e.g., ``"8.1 | NBC: KGW Portland"``). This is what the
            resolver compares against ECM's ``channel_name``; the
            existing tier-1 pipe-suffix tolerance handles the prefix.
            ``None`` when the attribute is absent.
        channel_identifier: ``<Media>/@channelIdentifier`` — short
            channel key (e.g., ``"8.1"``). Surfaced for forensic
            logging; not used in matching.
        channel_title: ``<Media>/@channelTitle`` — Plex's display title
            for the channel. Surfaced for forensic logging.
        channel_vcn: ``<Media>/@channelVcn`` — virtual channel number.
            Surfaced for forensic logging.
        begins_at: ``<Media>/@beginsAt`` (epoch seconds) parsed into a
            timezone-aware datetime. ``None`` when the attribute is
            absent or unparseable; such entries are filtered out by
            the current-time window check.
        ends_at: ``<Media>/@endsAt`` (epoch seconds) parsed into a
            timezone-aware datetime. Same fallback semantics as
            ``begins_at``.
        protocol: ``<Media>/@protocol`` — expected to be ``"livetv"``
            for the bead's target use case. Stored verbatim so the
            resolver can defensively filter on it.
    """

    grandparent_title: str
    channel_call_sign: str | None
    channel_identifier: str | None
    channel_title: str | None
    channel_vcn: str | None
    begins_at: datetime | None
    ends_at: datetime | None
    protocol: str | None


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
        now_playing_item_name: The ``Video/@title`` attribute. For Plex Live
            TV from a DVR/Tuner this is the PROGRAM currently airing (e.g.,
            ``"Saturday Night Live"``) — NOT the channel. Some operator setups
            still produce ``"<number> | <channel_name>"`` here when the upstream
            tuner injects the pipe-prefix shape directly. ``None`` if
            unparseable.
        now_playing_channel_name: The ``Video/@grandparentTitle`` attribute.
            For Plex Live TV this carries the CHANNEL name (e.g., ``"ESPN HD"``
            or ``"NBC"``) and is what the operator's ECM channel_name needs
            to match against. ``None`` for VOD / movies (no grandparent) and
            when the attribute is absent. bd-2zcvf: this field exists so the
            Plex resolver can match by channel just like the Emby and
            Jellyfin resolvers do via their own dedicated ``ChannelName``
            field. Without this, Plex Live TV attribution would fail whenever
            ``@title`` carried the program rather than the channel.
        now_playing_parent_title: The ``Video/@parentTitle`` attribute.
            Carries a secondary channel-name surface in some Plex setups
            (e.g., season title where Plex DVR organizes Live TV episodes
            under a per-channel "show" with the channel name on the parent).
            Kept as a third candidate so the resolver tier-1 can match the
            broadest range of Plex Live TV shapes without bloating the
            extraction code. ``None`` when absent.
        last_activity_date: ``datetime`` parsed from ``Video/@lastViewedAt``
            (epoch seconds). Used to break ties when multiple Plex sessions
            match the same ECM stream (most-recent-wins). ``None`` when the
            attribute is absent or unparseable.
        is_live: ``True`` when the upstream ``<Video>`` carries ``@live="1"``
            — Plex flags Live TV (DVR / tuner) sessions distinctly from VOD
            and personal-library episodes. The bd-ma6r3 EPG resolver tier
            uses this to gate the EPG cross-reference: VOD sessions have no
            broadcast schedule to look up, and firing the EPG fetch for them
            wastes the upstream call. Defaults to ``False`` when the
            attribute is absent so existing VOD / movie tests are
            unaffected.
    """

    session_id: str
    user_id: str
    user_name: str
    remote_endpoint: str
    now_playing_item_name: str | None
    now_playing_channel_name: str | None
    now_playing_parent_title: str | None
    last_activity_date: datetime | None
    is_live: bool = False


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
        # bd-ma6r3: delegate HTTP plumbing to ``_get_xml`` so the
        # ``/status/sessions`` path and the new EPG paths share one
        # error-translation surface (401 / non-2xx / transport).
        xml_text = await self._get_xml("/status/sessions")
        sessions = _parse_sessions_xml(xml_text)
        logger.debug(
            "[PLEX] /status/sessions returned %d sessions", len(sessions),
        )
        return sessions

    async def get_current_live_epg_channels(
        self,
        *,
        now: datetime | None = None,
    ) -> list[PlexEpgEntry]:
        """Fetch the union of CURRENT Live TV EPG airings across all DVR sections.

        bd-ma6r3 — the composed entry point the resolver calls. Discovers
        DVRs, walks each DVR's EPG sections, fetches the airings for each
        section, and returns a flat de-duplicated list of
        :class:`PlexEpgEntry` filtered to the airings whose
        ``beginsAt <= now <= endsAt`` AND ``protocol == "livetv"``.

        Design decision (composed vs flat): the bead is silent on whether
        to expose the DVR/section walk separately or hide it behind a
        single composed call. Composed wins because (a) every caller —
        and there is only one, the resolver — wants the union, never a
        per-DVR breakdown; (b) the cache layer's TTL is whole-snapshot,
        so partial returns from one DVR are not useful; and (c) hiding
        the walk inside the client lets the resolver stay focused on
        matching rather than orchestrating Plex API surfaces. The
        granular helpers (:meth:`get_dvr_keys`,
        :meth:`get_dvr_section_keys`, :meth:`get_section_epg_entries`)
        remain public for tests and future MCP surfaces but are not
        load-bearing on the production hot path.

        Args:
            now: Override the current time for testing. Defaults to
                ``datetime.now(timezone.utc)``. Pass a fixed datetime in
                tests so the time-window filter is deterministic against
                a fixed-shape fixture.

        Returns:
            List of current Live TV airings across all DVRs. Empty list
            when no DVRs are configured, no sections exist, or no airings
            cover the current time. Always returns a list — failures are
            absorbed by the caller-side cache layer; this method raises
            on any HTTP/XML error so the cache can WARN-log and serve
            stale data per :mod:`services.plex_epg_cache`.

        Raises:
            PlexClientError: On any non-2xx response or XML parse failure
                during the DVR/section discovery OR airing fetch. The
                cache layer wraps this with stale-fallback semantics.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        dvr_keys = await self.get_dvr_keys()
        if not dvr_keys:
            logger.debug("[PLEX] No DVRs configured; EPG sweep returns []")
            return []

        # bd-ma6r3: walk DVRs sequentially. Operator-typical setup is one
        # DVR (the bead's probe found exactly one). Even on multi-DVR
        # setups the sweep latency is bounded by the section count, not
        # the DVR count, so sequential iteration here keeps the code
        # readable without adding a parallel-fetch coordinator.
        current: list[PlexEpgEntry] = []
        for dvr_key in dvr_keys:
            try:
                section_keys = await self.get_dvr_section_keys(dvr_key)
            except PlexClientError as exc:
                # One DVR's section listing failed — log + continue so a
                # broken DVR doesn't disable EPG attribution for the
                # others. Re-raising would let the cache stale-fallback
                # absorb it, but partial-success here is friendlier on
                # multi-DVR operators.
                logger.warning(
                    "[PLEX] EPG sweep: section list failed for dvr=%s: %s",
                    dvr_key, exc,
                )
                continue
            for section_key in section_keys:
                try:
                    entries = await self.get_section_epg_entries(
                        dvr_key=dvr_key, section_key=section_key,
                    )
                except PlexClientError as exc:
                    logger.warning(
                        "[PLEX] EPG sweep: airings failed for "
                        "dvr=%s section=%s: %s",
                        dvr_key, section_key, exc,
                    )
                    continue
                for entry in entries:
                    if _epg_entry_is_current(entry, now=now):
                        current.append(entry)

        logger.debug(
            "[PLEX] EPG sweep: %d current Live TV airing(s) across "
            "%d DVR(s)",
            len(current), len(dvr_keys),
        )
        return current

    async def get_dvr_keys(self) -> list[str]:
        """Return the list of DVR ``key`` attributes from ``/livetv/dvrs``.

        bd-ma6r3. Each DVR's ``key`` is the integer Plex assigns when the
        operator configures a tuner provider; it's the same value baked
        into the ``tv.plex.providers.epg.xmltv:<key>`` endpoint paths.

        Returns:
            List of DVR keys (strings). Empty list when no DVRs are
            configured — a normal state for non-LiveTV Plex installs,
            not an error.

        Raises:
            PlexClientError: On any non-2xx / malformed XML.
        """
        xml = await self._get_xml("/livetv/dvrs")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            logger.warning(
                "[PLEX] /livetv/dvrs returned malformed XML: %s", exc,
            )
            raise PlexClientError(
                f"Plex /livetv/dvrs returned malformed XML: {exc}",
            ) from exc
        return [
            dvr.get("key", "") for dvr in root.iter("Dvr")
            if dvr.get("key")
        ]

    async def get_dvr_section_keys(self, dvr_key: str) -> list[str]:
        """Return the section ``key`` attributes for one DVR.

        bd-ma6r3. Each Plex DVR exposes its EPG under the synthetic
        provider id ``tv.plex.providers.epg.xmltv:<dvr_key>``, and the
        airings are organized into sections matching Plex's library
        categories (Movies, News, Shows, Sports for the typical
        Dispatcharr-fronted setup the bead targets).

        Args:
            dvr_key: A DVR key returned by :meth:`get_dvr_keys`.

        Returns:
            List of section keys (strings). Empty list if the DVR has no
            sections — unusual, but treated as the "nothing to fetch"
            answer rather than an error.

        Raises:
            PlexClientError: On any non-2xx / malformed XML.
        """
        path = f"/tv.plex.providers.epg.xmltv:{dvr_key}/sections"
        xml = await self._get_xml(path)
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            logger.warning("[PLEX] %s returned malformed XML: %s", path, exc)
            raise PlexClientError(
                f"Plex {path} returned malformed XML: {exc}",
            ) from exc
        return [
            d.get("key", "") for d in root.iter("Directory")
            if d.get("key")
        ]

    async def get_section_epg_entries(
        self,
        *,
        dvr_key: str,
        section_key: str,
    ) -> list[PlexEpgEntry]:
        """Return ALL EPG airings for one DVR section (no time-window filter).

        bd-ma6r3. Calls
        ``GET /tv.plex.providers.epg.xmltv:<dvr_key>/sections/<sid>/all?type=4``
        and emits one :class:`PlexEpgEntry` per ``<Media>`` element under
        every returned ``<Video>``. The ``?type=4`` query is Plex's
        episode-content-type filter — without it the response also
        includes show/season directory entries which carry no
        ``channelCallSign``.

        The caller (typically
        :meth:`get_current_live_epg_channels`) is responsible for the
        ``beginsAt <= now <= endsAt`` time-window filter so this helper
        stays a pure fetch+parse with no clock side effects.

        Args:
            dvr_key: DVR key from :meth:`get_dvr_keys`.
            section_key: Section key from :meth:`get_dvr_section_keys`.

        Returns:
            List of :class:`PlexEpgEntry` (one per ``<Media>``). Empty
            list when the section has no episodes — normal for unused
            sections like Movies in PO's all-Dispatcharr setup.

        Raises:
            PlexClientError: On any non-2xx / malformed XML.
        """
        path = (
            f"/tv.plex.providers.epg.xmltv:{dvr_key}"
            f"/sections/{section_key}/all"
        )
        xml = await self._get_xml(path, params={"type": "4"})
        return _parse_epg_xml(xml)

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
    # HTTP plumbing
    # ------------------------------------------------------------------

    async def _get_xml(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        """Shared GET implementation for the XML endpoints (bd-ma6r3).

        Centralizes the 401 / non-2xx / transport-error handling that
        the bd-r5f0c.2 :meth:`get_sessions` implementation pioneered so
        the bd-ma6r3 EPG endpoints inherit the same error-translation
        contract verbatim. All XML endpoints use the same
        ``X-Plex-Token`` header and same expected status codes.

        SSRF posture: ``self.base_url`` was sanitized at client
        construction time (bd-r5f0c.4 test-connection endpoint) — every
        path concatenation here goes through that same trusted host.
        We never accept a host override per-call.
        """
        url = f"{self.base_url}{path}"
        headers = {"X-Plex-Token": self.api_key}

        logger.debug("[PLEX] GET %s params=%s", url, params)
        try:
            response = await self._client.request(
                "GET", url, headers=headers, params=params,
            )
        except httpx.HTTPError as exc:
            logger.warning("[PLEX] %s request failed: %s", path, exc)
            raise PlexClientError(f"Plex request failed: {exc}") from exc

        if response.status_code == 401:
            logger.warning("[PLEX] %s returned 401 unauthorized", path)
            raise PlexClientError(
                f"Plex {path} returned 401 unauthorized — check token",
            )

        if response.status_code >= 400:
            logger.warning(
                "[PLEX] %s returned non-2xx: status=%s",
                path, response.status_code,
            )
            raise PlexClientError(
                f"Plex {path} returned {response.status_code}",
            )

        return response.text

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

    # bd-2zcvf: Plex Live TV puts the channel name in ``@grandparentTitle``
    # while ``@title`` carries the PROGRAM currently airing (e.g., the show
    # episode title). Without extracting ``@grandparentTitle``, the resolver
    # has nothing to compare an operator's ECM channel_name against and
    # falls all the way through to "User #N" in Stats. ``@parentTitle`` is
    # captured as a secondary channel surface — some Plex DVR setups put
    # the channel name on the parent (the "show" that groups all Live TV
    # episodes by channel) rather than the grandparent.
    # bd-ma6r3: ``@live="1"`` flags Plex Live TV sessions (DVR / tuner)
    # distinctly from VOD. The resolver's EPG cross-reference tier only
    # runs for live sessions — VOD has no broadcast schedule to look up,
    # and firing the EPG fetch on every VOD-only poll cycle would waste
    # the upstream call. ``@live`` can be "1", "0", or absent; we coerce
    # any other value to False so future Plex format changes degrade
    # gracefully (live sessions still flow through tier-1/2/3 normally).
    is_live = item.get("live") == "1"

    return PlexSession(
        session_id=item.get("ratingKey", ""),
        user_id=user_el.get("id", ""),
        user_name=user_el.get("title", ""),
        remote_endpoint=remote_endpoint,
        now_playing_item_name=item.get("title"),
        now_playing_channel_name=item.get("grandparentTitle"),
        now_playing_parent_title=item.get("parentTitle"),
        last_activity_date=last_activity,
        is_live=is_live,
    )


# ---------------------------------------------------------------------------
# EPG parsing helpers (bd-ma6r3)
# ---------------------------------------------------------------------------


def _parse_epg_xml(xml_text: str) -> list[PlexEpgEntry]:
    """Parse one section's EPG response into a list of :class:`PlexEpgEntry`.

    bd-ma6r3. Plex returns a ``<MediaContainer>`` of ``<Video>`` episode
    elements, each carrying one or more ``<Media>`` children. We emit one
    entry per ``<Media>`` so the caller's time-window filter can examine
    each airing's ``beginsAt`` / ``endsAt`` pair independently — a single
    episode that airs at multiple times produces one entry per airing.

    Raises:
        PlexClientError: On any XML parse error.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(
            "[PLEX] Failed to parse EPG section XML: %s", exc,
        )
        raise PlexClientError(
            f"Plex EPG section returned malformed XML: {exc}",
        ) from exc

    entries: list[PlexEpgEntry] = []
    for video in root.iter("Video"):
        # bd-ma6r3: ``@grandparentTitle`` is the field the resolver matches
        # against ``session.now_playing_channel_name`` (both come from the
        # same Plex attribute name on different endpoints). Missing
        # grandparent means we can't match an unknown session to this
        # airing — skip the whole video.
        grandparent = video.get("grandparentTitle")
        if not grandparent:
            continue
        for media in video.iter("Media"):
            entries.append(_map_epg_media(grandparent_title=grandparent, media=media))
    return entries


def _map_epg_media(*, grandparent_title: str, media: ET.Element) -> PlexEpgEntry:
    """Map one ``<Media>`` element to a :class:`PlexEpgEntry`.

    Time attributes (``beginsAt``, ``endsAt``) are epoch seconds; absent
    or unparseable values land as ``None`` (the time-window filter
    treats ``None``-bounded entries as never-current so they drop out
    of the live-EPG snapshot without raising).
    """
    return PlexEpgEntry(
        grandparent_title=grandparent_title,
        channel_call_sign=media.get("channelCallSign"),
        channel_identifier=media.get("channelIdentifier"),
        channel_title=media.get("channelTitle"),
        channel_vcn=media.get("channelVcn"),
        begins_at=_parse_epoch(media.get("beginsAt")),
        ends_at=_parse_epoch(media.get("endsAt")),
        protocol=media.get("protocol"),
    )


def _parse_epoch(raw: str | None) -> datetime | None:
    """Parse a Plex epoch-seconds attribute into a tz-aware ``datetime``.

    Returns ``None`` when the input is absent or malformed — the
    EPG-current filter handles ``None`` as "not currently airing" so
    a broken timestamp drops the entry from the snapshot rather than
    raising.
    """
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _epg_entry_is_current(entry: PlexEpgEntry, *, now: datetime) -> bool:
    """Return ``True`` iff ``entry`` is a Live TV airing covering ``now``.

    bd-ma6r3. Three constraints all required:

    * ``protocol == "livetv"`` — defensive against EPG entries Plex might
      surface for non-LiveTV sources in mixed-provider setups.
    * ``begins_at`` and ``ends_at`` both populated.
    * ``begins_at <= now <= ends_at``.

    Returning ``False`` simply filters the entry out of the live
    snapshot; the caller does not need to log or raise.
    """
    if entry.protocol != "livetv":
        return False
    if entry.begins_at is None or entry.ends_at is None:
        return False
    return entry.begins_at <= now <= entry.ends_at
