"""WebSocket ``channel_stats`` subscriber (ADR-013, bead 312nk.2).

``ChannelStatsSubscriber`` maintains ONE long-lived WebSocket connection to
Dispatcharr's ``"updates"`` consumer group
(``ws://<host>:<port>/ws/?token=<JWT>``) and feeds the ``channel_stats``
broadcast — the SAME snapshot ``/proxy/ts/status`` returns over REST — into the
tracker's ``_process_channel_snapshot``. The poll loop is NOT removed; it is the
permanent fallback (ADR-013 §D5). The whole feature is behind
``use_ws_channel_stats`` (default OFF).

This module is intentionally standalone (separate from ``bandwidth_tracker`` for
testability): the connection, parsing, watchdog, and backoff each have a small
seam that the unit tests exercise without touching a real Dispatcharr.

Scope of THIS bead:
* connect / JWT-on-socket + refresh on auth-class close
* watchdog/staleness -> flip ``ws_healthy`` False so the poll re-engages
* exponential backoff + jitter (cap 30s), reset on a successful event
* ``channel_stats`` -> ``_process_channel_snapshot``; ignore all other types

OUT OF SCOPE (later beads): ``stream_rehash``/``channels_created`` cache
invalidation (.4), telemetry write-cadence throttle (.3). NOTE: while WS is
enabled+healthy, ``_process_channel_snapshot`` is driven on EVERY event (~2s),
which is ~5x today's telemetry write volume. This amplification is KNOWN and
fixed by bead 312nk.3 (the ``telemetry_write_interval`` throttle); do not add it
here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger(__name__)

# Watchdog default: 3x the ~2s Celery-beat ``channel_stats`` cadence (ADR-013
# §D5). If no ``channel_stats`` arrives within this window the socket is
# declared stale, ``ws_healthy`` flips False (the poll re-engages), and we
# tear down + reconnect.
DEFAULT_WS_STALENESS_TIMEOUT = 6.0

# Reconnect backoff (ADR-013 §D5): 1s -> 2s -> 4s -> ... cap 30s, with jitter,
# reset to base on a successful event. Backoff protects Dispatcharr from a
# reconnect storm if it restarts.
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_CAP = 30.0

# Watchdog poll cadence — how often the staleness check runs.
_WATCHDOG_INTERVAL = 2.0

# WebSocket close codes that indicate an auth-class failure, warranting a token
# refresh before reconnect (ADR-013 §D1/§D5). 1008 = policy violation; the
# 4000-4099 application range is what ``JWTAuthMiddleware`` uses to reject a
# bad/expired token on the channels handshake.
_AUTH_CLOSE_CODES = {1008}
_AUTH_CLOSE_RANGE = range(4000, 4100)


class ChannelStatsSubscriber:
    """Owns one WS connection to Dispatcharr's ``updates`` group and drives the
    tracker's snapshot pipeline from ``channel_stats`` events.

    The subscriber is OWNED by ``BandwidthTracker`` (constructed in
    ``__init__``, ``run()`` started as a task in ``start()`` only when
    ``use_ws_channel_stats`` is true, cancelled in ``stop()``). It inherits the
    tracker's settings-restart and HTTPS-subprocess guard for free.
    """

    def __init__(
        self,
        client,
        tracker,
        *,
        ws_staleness_timeout: float = DEFAULT_WS_STALENESS_TIMEOUT,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
        connect_factory: Optional[Callable[..., Any]] = None,
    ):
        self.client = client
        self.tracker = tracker
        self.ws_staleness_timeout = ws_staleness_timeout
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        # Injectable for tests; defaults to the real websockets connect.
        self._connect_factory = connect_factory or _ws_connect

        self._running = False
        self._ws_healthy = False
        # Wall-clock not used for the watchdog — monotonic avoids clock skew.
        self._last_event_at: float = 0.0
        self._backoff_attempt = 0

        # Observability counters (§D7) — read by tests and future metrics.
        self.events_received = 0
        self.fallback_activations = 0

        # Last WS snapshot summary for the poll's cross-validation line (§D5):
        # channel count + summed cumulative ``total_bytes``.
        self.last_snapshot_channel_count = 0
        self.last_snapshot_total_bytes = 0

    # ------------------------------------------------------------------
    # Public read-only surface
    # ------------------------------------------------------------------

    @property
    def ws_healthy(self) -> bool:
        """True while a fresh ``channel_stats`` stream is flowing. The poll
        loop reads this each tick to decide whether to suppress / cross-validate
        (ADR-013 §D5)."""
        return self._ws_healthy

    # ------------------------------------------------------------------
    # URL derivation
    # ------------------------------------------------------------------

    def _build_ws_url(self) -> str:
        """Derive ``ws(s)://<host>[:<port>]/ws/?token=<JWT>`` from the REST
        client's base URL. Scheme maps http->ws, https->wss. The token is read
        live (not cached at construction) so a refresh is picked up on the next
        connect."""
        parts = urlsplit(self.client.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        netloc = parts.netloc or parts.path  # tolerate a bare host
        token = self.client.access_token or ""
        return f"{scheme}://{netloc}/ws/?token={token}"

    # ------------------------------------------------------------------
    # Backoff
    # ------------------------------------------------------------------

    def _next_backoff(self) -> float:
        """Exponential backoff with full jitter, capped. Advances the attempt
        counter. ``random.uniform(0, ceiling)`` spreads reconnects so a fleet
        of clients does not retry in lockstep (here: just one client, but the
        jitter still avoids hammering Dispatcharr on a fixed cadence)."""
        ceiling = min(self.backoff_base * (2 ** self._backoff_attempt), self.backoff_cap)
        self._backoff_attempt += 1
        return random.uniform(0.0, ceiling)

    def _reset_backoff(self) -> None:
        self._backoff_attempt = 0

    # ------------------------------------------------------------------
    # Watchdog / staleness
    # ------------------------------------------------------------------

    def _check_staleness(self) -> bool:
        """Return True if the socket just transitioned healthy -> stale.

        Flips ``ws_healthy`` False and bumps ``fallback_activations`` exactly
        once per transition (a load-bearing WARN metric, §D7 — a rising count
        means the WS is flapping and ECM is silently on the poll). A socket that
        is already unhealthy does not re-count."""
        if not self._ws_healthy:
            return False
        if time.monotonic() - self._last_event_at <= self.ws_staleness_timeout:
            return False
        self._ws_healthy = False
        self.fallback_activations += 1
        logger.warning(
            "[WS] channel_stats stale (no event in %.1fs > %.1fs timeout); "
            "poll re-engaging — fallback_activations_total=%s",
            time.monotonic() - self._last_event_at,
            self.ws_staleness_timeout,
            self.fallback_activations,
        )
        return True

    # ------------------------------------------------------------------
    # Auth-class close detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_auth_close(code: Optional[int]) -> bool:
        if code is None:
            return False
        return code in _AUTH_CLOSE_CODES or code in _AUTH_CLOSE_RANGE

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, raw: Any) -> None:
        """Parse one inbound WS frame and dispatch by event ``type``.

        * ``channel_stats`` -> ``json.loads(stats)`` (``stats`` is a JSON
          STRING), extract ``channels``, drive ``_process_channel_snapshot``
          with ``observed_at_ms = int(time.time()*1000)`` (the same stamp the
          poll uses). Marks the socket healthy and resets backoff.
        * everything else (``stream_rehash``, ``channels_created``, unknown) is
          ignored gracefully (cache invalidation is bead 312nk.4).

        Never raises on a malformed frame — a bad event must not kill the
        read loop."""
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            logger.debug("[WS] ignoring non-JSON frame")
            return
        if not isinstance(event, dict):
            logger.debug("[WS] ignoring non-object frame")
            return

        etype = event.get("type")
        if etype != "channel_stats":
            # stream_rehash / channels_created / anything else — ignored for
            # this bead (no crash on unknown types). Cache invalidation is .4.
            logger.debug("[WS] ignoring event type=%s", etype)
            return

        stats_raw = event.get("stats")
        if not isinstance(stats_raw, str):
            logger.debug("[WS] channel_stats has non-string stats (%s); ignoring", type(stats_raw).__name__)
            return
        try:
            stats = json.loads(stats_raw)
        except (ValueError, TypeError):
            logger.debug("[WS] channel_stats stats not parseable JSON; ignoring")
            return
        if not isinstance(stats, dict):
            logger.debug("[WS] channel_stats stats not an object; ignoring")
            return

        channels = stats.get("channels", [])
        if not isinstance(channels, list):
            channels = []

        observed_at_ms = int(time.time() * 1000)

        # Record the summary for the poll's cross-validation drift line (§D5).
        self.last_snapshot_channel_count = len(channels)
        self.last_snapshot_total_bytes = sum(
            (c.get("total_bytes", 0) or 0) for c in channels if isinstance(c, dict)
        )

        # A successful event proves the socket is alive: mark healthy, refresh
        # the watchdog timestamp, reset backoff.
        self.events_received += 1
        if not self._ws_healthy:
            logger.info("[WS] channel_stats stream healthy (events_received=%s)", self.events_received)
        self._ws_healthy = True
        self._last_event_at = time.monotonic()
        self._reset_backoff()

        await self.tracker._process_channel_snapshot(channels, observed_at_ms)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect/read/reconnect loop. Runs until cancelled by ``stop()``.

        Throughout backoff and any outage, the poll path is the live driver —
        there is no window where ECM is blind (ADR-013 §D5)."""
        self._running = True
        logger.info("[WS] ChannelStatsSubscriber starting")
        try:
            while self._running:
                try:
                    await self._connect_and_read()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — any failure -> backoff+reconnect
                    logger.warning("[WS] connection error: %s", e)

                if not self._running:
                    break
                # On any disconnect ws is not healthy; the poll re-engages.
                self._ws_healthy = False
                delay = self._next_backoff()
                logger.info(
                    "[WS] reconnecting in %.1fs (attempt #%s, cap %.0fs)",
                    delay,
                    self._backoff_attempt,
                    self.backoff_cap,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
        finally:
            self._ws_healthy = False
            self._running = False
            logger.info("[WS] ChannelStatsSubscriber stopped")

    async def _connect_and_read(self) -> None:
        """One connection lifetime: connect, then read until the socket closes.

        On an auth-class close, refresh the token before returning so the next
        ``run()`` iteration reconnects with a fresh credential (ADR-013 §D5)."""
        url = self._build_ws_url()
        # Log without the token query param (it is a credential).
        safe_url = url.split("?", 1)[0]
        logger.info("[WS] connecting to %s", safe_url)
        watchdog_task: Optional[asyncio.Task] = None
        try:
            async with self._connect_factory(url) as ws:
                logger.info("[WS] connected to %s", safe_url)
                # Anchor the watchdog at connect so a connection that never
                # delivers an event still trips staleness.
                self._last_event_at = time.monotonic()
                watchdog_task = asyncio.create_task(self._watchdog(ws))
                async for raw in ws:
                    await self._handle_message(raw)
        except InvalidStatus as e:
            # Handshake rejected (e.g. 401 from JWTAuthMiddleware on a bad/
            # expired token). Refresh and let the loop reconnect.
            status = getattr(getattr(e, "response", None), "status_code", None)
            logger.warning("[WS] handshake rejected (status=%s); refreshing token", status)
            if status in (401, 403):
                await self._refresh_token()
        except ConnectionClosed as e:
            code = getattr(e, "code", None)
            if code is None:
                code = getattr(getattr(e, "rcvd", None), "code", None)
            logger.info("[WS] connection closed (code=%s)", code)
            if self._is_auth_close(code):
                logger.warning("[WS] auth-class close (code=%s); refreshing token", code)
                await self._refresh_token()
        finally:
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _refresh_token(self) -> None:
        """Reuse the REST client's token refresh — the socket shares the SAME
        JWT the REST client manages (ADR-013 §D1). Best-effort: a refresh
        failure just means the next connect retries with the stale token and
        backs off."""
        try:
            await self.client._refresh_access_token()
            logger.info("[WS] access token refreshed for reconnect")
        except Exception as e:  # noqa: BLE001
            logger.warning("[WS] token refresh failed: %s", e)

    async def _watchdog(self, ws) -> None:
        """Periodically check for staleness; on a stale socket, close it so the
        read loop unblocks and the outer ``run()`` reconnects."""
        try:
            while True:
                await asyncio.sleep(_WATCHDOG_INTERVAL)
                if self._check_staleness():
                    logger.info("[WS] watchdog closing stale socket")
                    try:
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    return
        except asyncio.CancelledError:
            return
