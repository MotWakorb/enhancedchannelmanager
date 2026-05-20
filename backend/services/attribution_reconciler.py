"""Per-channel media-server attribution reconciliation (bd-mlcla).

This module replaces the brittle per-IP gate + per-IP broadcast that the
three media-server resolvers and their two call sites used to do. The old
model assumed every media-server-mediated Dispatcharr connection egressed
through the configured media-server IP, so it gated on
``ecm_session_ip == <source>_server_ip`` and broadcast a single resolver
hit to every connection. Both assumptions break under Docker networking:

* **Gate brittleness (bd-mlcla / bd-podx3 incident):** browser-direct
  playback (e.g. Jellyfin Web) is NAT'd through the media box's Docker
  bridge gateway, so ECM observes source ``172.18.0.1`` instead of the
  configured ``172.16.0.19``. The strict gate rejected it → "User #0".
* **Broadcast collapse (bd-ost8o):** matching ECM clients to sessions by
  channel name alone, with no per-connection identity, collapsed every
  viewer on a channel onto one media-server user.
* **Broadcast fan-out (bd-cat70):** stamping one resolver hit onto every
  connection broadcast one user to all clients.

The unlock: Dispatcharr ``/proxy/ts/status`` connections carry a stable
unique ``client_id``; media-server sessions carry a stable ``session_id``
+ ``user_name`` + ``last_activity_date``. The two sides' **source IPs do
NOT correlate** across the NAT boundary, so attribution cannot be an IP
join. Instead it is a per-channel **set reconciliation**:

* **Eligible connections** = Dispatcharr connections on the channel with
  NO URL identity (no username embedded in their XC/M3U URL). Connections
  WITH a URL username keep the bd-gy5nd provider/hostname attribution
  path and are excluded here. This "no-URL-identity" test is the
  discriminator that replaces the IP gate.
* **Candidate users** = the distinct media-server users the resolvers
  matched to the channel.
* **Assignment** = each user assigned to AT MOST ONE connection
  (structural anti-collapse). Connections ranked by IP-priority then
  ``connected_at``; users ranked by ``last_activity_date`` descending;
  paired 1:1.

Source IP is used ONLY to RANK connections (trusted/infrastructure IPs
sort first as most-likely media-mediated). It NEVER rejects: getting the
ranking wrong can only change tie-break order, never correctness.

Residual ambiguity (PO decision = Option B): when a group of connections
is genuinely indistinguishable (same IP-priority bucket, no per-connection
signal to order them) AND there are 2+ candidate users for that group, do
NOT pin a single possibly-wrong name to each row. Instead label each
connection in the ambiguous group with a rollup
``"N viewers: <comma-separated distinct user list>"``. See
:func:`reconcile_channel` and :data:`AMBIGUOUS_GROUP_PREDICATE` for the
exact predicate.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def normalize_client_ip(raw: Optional[str]) -> Optional[str]:
    """Normalize a media-server session's reported client endpoint to a bare IP.

    bd-7ncci. The three media servers report the requesting device's
    address in slightly different shapes:

    * Emby / Jellyfin ``RemoteEndPoint`` — usually a bare IPv4
      (``"47.203.164.8"``) but can carry a ``host:port`` suffix
      (``"47.203.164.8:51514"``) or an IPv6 literal, optionally
      bracketed with a port (``"[2001:db8::1]:51514"``).
    * Plex ``Player/@address`` — a bare IP in practice.

    This helper returns the bare IP for display, stripping any port and
    surrounding brackets, and validating the result is a real IP address
    so a malformed value never reaches the Stats UI. It also tolerates an
    XFF-style comma-separated list by taking the FIRST entry (the
    originating client) — defensive against a reverse-proxy-injected
    value, though the upstream session payloads do not currently produce
    that shape.

    Returns:
        The bare IPv4/IPv6 string, or ``None`` when ``raw`` is empty /
        whitespace / not parseable as an IP. ``None`` (not ``""``) is the
        "no client IP available" sentinel so the Stats field stays blank
        rather than rendering an empty string. Never raises.
    """
    if not raw or not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token:
        return None

    # XFF-style "client, proxy1, proxy2" — the first hop is the origin.
    if "," in token:
        token = token.split(",", 1)[0].strip()
        if not token:
            return None

    # Bracketed IPv6 with optional port: "[2001:db8::1]" or "[..]:51514".
    if token.startswith("["):
        host = token[1:].split("]", 1)[0]
    elif token.count(":") == 1:
        # Exactly one colon → "ipv4:port" form. Strip the port.
        host = token.split(":", 1)[0]
    else:
        # No colon (bare IPv4) or many colons (bare IPv6) → use as-is.
        host = token

    host = host.strip()
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


# Xtream-Codes (XC) live-stream URL path: ``/live/<user>/<pass>/<id>.ts``
# (the ``/live/`` segment is optional in some providers:
# ``/<user>/<pass>/<id>``). The presence of a username/password pair in the
# path is the "URL identity" signal — a connection served from such a URL is
# a genuine direct-IPTV client carrying its own credentials, attributed via
# the bd-gy5nd provider/hostname path, NOT via media-server reconciliation.
_XC_PATH_RE = re.compile(
    r"(?:^|/)(?:live|movie|series)/[^/]+/[^/]+/[^/]+",
    re.IGNORECASE,
)
# Bare ``/<user>/<pass>/<numeric-id>(.ext)`` form without a type segment.
_XC_BARE_PATH_RE = re.compile(
    r"^/[^/]+/[^/]+/\d+(?:\.\w+)?/?$",
)


def url_embeds_username(url: Optional[str]) -> bool:
    """True when a stream URL embeds XC/M3U credentials (a "URL identity").

    Detects the two common Xtream-Codes credential surfaces:

    * Query form: ``...get.php?username=X&password=Y`` (the M3U-playlist
      and ``player_api`` shape).
    * Path form: ``http://host/live/<user>/<pass>/<id>.ts`` (and the
      ``movie``/``series`` variants, plus the bare
      ``/<user>/<pass>/<id>`` form without a leading type segment).

    A connection whose active stream URL embeds credentials is a genuine
    direct-IPTV client — it is attributed via the bd-gy5nd
    provider/hostname path and EXCLUDED from media-server reconciliation.
    This is the discriminator that replaces the IP gate.

    Returns ``False`` for empty / unparsable / non-XC URLs (e.g. a
    Dispatcharr proxy URL with no embedded credentials) so those
    connections remain eligible for reconciliation. Never raises.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False

    # Query form: get.php?username=...&password=...
    if parsed.query:
        try:
            qs = parse_qs(parsed.query)
        except (ValueError, TypeError):
            qs = {}
        if any(qs.get(k) for k in ("username", "user")) and any(
            qs.get(k) for k in ("password", "pass")
        ):
            return True

    path = parsed.path or ""
    if _XC_PATH_RE.search(path):
        return True
    if _XC_BARE_PATH_RE.match(path):
        return True
    return False


def has_dispatcharr_account_identity(
    user_id: object = None, username: object = None
) -> bool:
    """True when a Dispatcharr ``/proxy/ts/status`` client carries an account.

    bd-rools. The per-connection discriminator that distinguishes a genuine
    direct-IPTV subscriber (a viewer authenticated to Dispatcharr with a
    real sub-account) from an anonymous media-server pull (the transcoding
    proxy AND NAT'd browser-direct playback, which Dispatcharr serves
    anonymously).

    Dispatcharr surfaces ``user_id == "0"`` / ``0`` / ``None`` / ``""`` for
    an anonymous connection — there is no ``users.id == 0`` row, it is the
    project-wide "User #0" sentinel (see
    ``bandwidth_tracker._coerce_session_user_id``). A connection has an
    account identity when EITHER:

    * ``user_id`` parses to a positive integer (the canonical signal — the
      Dispatcharr account primary key), OR
    * ``username`` is a non-empty, non-whitespace string that is not the
      ``"0"`` sentinel (defensive fallback for payloads that surface a name
      without a numeric id).

    Returns ``False`` for anonymous / missing / sentinel values so those
    connections stay eligible for media-server reconciliation. Never raises.
    """
    # Positive-integer user_id is the canonical account signal. Mirror
    # bandwidth_tracker._coerce_session_user_id's anonymous-sentinel rules
    # (None / "" / 0 / "0" / bool / float / non-positive → anonymous).
    if user_id is not None and not isinstance(user_id, (bool, float)):
        try:
            parsed = int(user_id)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return True
    # Defensive username fallback: a real, non-sentinel name.
    if isinstance(username, str):
        token = username.strip()
        if token and token != "0":
            return True
    return False


# ---------------------------------------------------------------------------
# IP-priority ranking buckets
# ---------------------------------------------------------------------------


# Connections whose source IP is in the "infrastructure" set (the resolved
# media-server IP, auto-detected local Docker bridge gateways, or operator-
# configured trusted CIDRs) are the most likely to be carrying media-server-
# mediated traffic, so they sort FIRST when pairing users to connections.
# This is a SOFT hint only: an unknown IP is never rejected, it just sorts
# after the trusted bucket. Lower numeric value sorts first.
IP_PRIORITY_TRUSTED = 0
IP_PRIORITY_UNKNOWN = 1


# Human-readable description of the Option-B ambiguous-group predicate,
# referenced from the docstring and tests so the rule has one canonical
# statement. A group of connections is "genuinely ambiguous" when ALL of:
#   1. The group has 2+ connections.
#   2. Every connection in the group shares the same IP-priority bucket
#      (no IP signal orders them).
#   3. The group is offered 2+ distinct candidate users.
# When a connection can instead be paired to exactly one user with no peer
# contending for the same slot, that connection shows the single name.
AMBIGUOUS_GROUP_PREDICATE = (
    "2+ connections in one IP-priority bucket with no per-connection signal "
    "to order them, AND 2+ distinct candidate users for that bucket"
)


# ---------------------------------------------------------------------------
# Public data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Connection:
    """One Dispatcharr ``/proxy/ts/status`` client eligible for reconciliation.

    Attributes:
        client_id: Stable unique per-connection id from Dispatcharr (e.g.
            ``"client_1779287570260_1682"``). The reconciler keys its
            per-connection assignment output on this id so the caller can
            map results back to the right client dict. Falls back to
            ``ip_address`` when Dispatcharr omits it (older payloads),
            which is still stable enough for one poll's reconciliation.
        ip_address: Source IP ECM observed for this connection. Used ONLY
            for ranking (priority bucket); never to gate.
        connected_at: Unix-epoch float (or comparable) of when the
            connection started. Secondary sort key after IP priority so
            the longest-connected viewer in a bucket sorts first. ``None``
            sorts last.
        has_url_identity: True when this connection's XC/M3U URL embeds a
            username — i.e. it is a genuine direct-IPTV client attributed
            via the bd-gy5nd provider/hostname path. Connections with a
            URL identity are EXCLUDED from media-server reconciliation by
            :func:`eligible_connections`; this field lets the caller pass
            the full connection list and let the module do the filtering.

            **bd-4w9w6 / bd-rools:** the call sites no longer derive this
            from the channel's shared upstream URL (that conflated channel
            SOURCE with client IDENTITY and dropped every media-server
            viewer to User #0). The per-client discriminator is now
            :attr:`has_account_identity`. ``has_url_identity`` is retained
            for the pure-reconciler API and any future caller that has a
            genuine per-connection credentialed URL, but both call sites
            currently set it ``False``.
        has_account_identity: True when this connection carries a genuine
            Dispatcharr ACCOUNT identity — a positive ``user_id`` (and/or a
            non-empty ``username``) on the Dispatcharr ``/proxy/ts/status``
            client dict, i.e. the viewer authenticated to Dispatcharr with
            a real sub-account (e.g. ``kmfelmer``, ``user_id=3``). Such a
            connection is a genuine direct-IPTV subscriber whose attribution
            comes from its Dispatcharr account (bd-gy5nd path), NOT from a
            media-server session.

            **bd-rools (re-fix for bd-cat70 direct-client cross-attribution):**
            Dispatcharr surfaces ``user_id == "0"`` / ``0`` / ``None`` for
            an ANONYMOUS connection — every media-server pull (transcoding
            proxy AND NAT'd browser-direct playback) is anonymous to
            Dispatcharr and therefore has NO account identity, so it stays
            eligible. A connection WITH an account identity is EXCLUDED from
            reconciliation by :func:`eligible_connections` (so a real
            subscriber on a channel shared with a media-server viewer can
            never absorb that viewer via the B1 direct-first ordering) and
            is never treated as the server proxy. Verified against live
            Dispatcharr data (the active media-server pull carries
            ``user_id="0"``; sub-accounts carry positive ids).
        is_server_proxy: True when this connection's source IP IS a resolved
            media-server IP — i.e. it is the media server's own transcoding
            proxy pull, which legitimately carries the channel's upstream
            viewers on one Dispatcharr connection (the bd-r5f0c.9
            multi-viewer-on-one-proxy case).

            **bd-mlcla B1 ordering:** the at-most-once 1:1 assignment runs
            on the DIRECT (non-proxy) connections FIRST, consuming distinct
            users; the server-proxy connection then carries only the
            REMAINING (unconsumed) users as its rollup. This guarantees a
            browser-direct viewer sharing a channel with a proxy pull always
            gets its own distinct name whenever an unconsumed matching user
            exists — the proxy never suppresses it to User #0.

            **Exact-IP-equality assumption (mis-fire edge):** this flag is
            set by the call sites as ``source_ip == resolved_server_ip`` with
            NO corroborating signal (no user-agent check, no session
            cross-reference). A browser-direct connection whose observed
            source IP happens to equal the configured server IP (e.g. the
            operator runs the browser on the media-server host itself) is
            therefore mis-flagged as the proxy. The B1 direct-first ordering
            bounds the blast radius: even when mis-flagged, distinct direct
            viewers on the channel are reconciled first, so the mis-flagged
            connection carries only the leftover set rather than swallowing
            everyone. The mis-fire can still mislabel that one connection's
            display (proxy rollup vs. single name); a corroborating signal is
            tracked as future work, not gated here.
    """

    client_id: str
    ip_address: Optional[str] = None
    connected_at: Optional[float] = None
    has_url_identity: bool = False
    has_account_identity: bool = False
    is_server_proxy: bool = False


@dataclass(frozen=True)
class CandidateUser:
    """One distinct media-server user the resolvers matched to the channel.

    Attributes:
        user_name: Human-readable media-server username (the attribution
            surface operators see). Used as the dedup key together with
            ``user_id`` so the same physical user matched by two sources
            does not appear twice.
        user_id: Stable upstream user id when the source exposes one
            (Emby / Jellyfin do; Plex's ``/status/sessions`` does not, so
            this is ``None`` for Plex). Primary dedup key when present.
        last_activity_date: Comparable recency value (ISO string for
            Emby/Jellyfin, float epoch for Plex normalized upstream).
            Users sort by this DESCENDING so the most-recently-active
            viewer pairs to the top-ranked connection. ``None`` sorts
            last.
        source: ``"emby"`` / ``"plex"`` / ``"jellyfin"`` — the media
            server that matched this user. Carried through to the
            assignment so the caller can stamp the right source badge.
        client_ip: bd-7ncci. The REAL requesting-device IP the media
            server reported for this viewer's session
            (Emby/Jellyfin ``RemoteEndPoint`` / Plex ``Player/@address``,
            normalized to a bare IP via :func:`normalize_client_ip`).
            ``None`` when the source did not expose it. This is distinct
            from the connection's Dispatcharr-observed source IP — it is
            the device behind the media server, surfaced as a separate
            "Client IP" Stats field. Carried through assignment so the
            caller can stamp it per viewer.
    """

    user_name: str
    user_id: Optional[str] = None
    last_activity_date: Optional[object] = None
    source: str = ""
    client_ip: Optional[str] = None


@dataclass(frozen=True)
class ConnectionAssignment:
    """The reconciliation outcome for ONE connection.

    Exactly one of the following holds:

    * ``user`` is set, ``rollup_users`` and ``proxy_viewers`` empty — the
      connection was unambiguously paired to a single user. Render that
      single name.
    * ``proxy_viewers`` is non-empty (2+ users) — the connection is the
      media server's transcoding PROXY, which genuinely carries every one
      of those distinct viewers (bd-r5f0c.9). After the bd-mlcla B1 fix the
      proxy carries the REMAINING users not already consumed by distinct
      direct connections on the same channel. Render the full viewer LIST
      (legacy single name = position 0). This is NOT ambiguous: the proxy
      knows all its sessions.
    * ``rollup_users`` is non-empty (2+ users) — the connection is in a
      genuinely-ambiguous browser-direct group (Option B). Render the
      ``"N viewers: ..."`` rollup; no single name is pinned because there
      is no signal to decide which connection is which viewer.
    * all empty — the connection stays User #0 (no candidate user reached
      it; never broadcast).
    """

    client_id: str
    user: Optional[CandidateUser] = None
    rollup_users: tuple[CandidateUser, ...] = ()
    proxy_viewers: tuple[CandidateUser, ...] = ()

    @property
    def is_rollup(self) -> bool:
        """True when this connection should render the Option-B rollup."""
        return len(self.rollup_users) > 0

    @property
    def is_proxy_multi(self) -> bool:
        """True when this connection is a proxy carrying a full viewer list."""
        return len(self.proxy_viewers) > 0


@dataclass(frozen=True)
class ReconciliationResult:
    """The full per-channel reconciliation outcome.

    Attributes:
        assignments: One :class:`ConnectionAssignment` per ELIGIBLE
            connection (URL-identity connections are excluded upstream and
            do not appear here), keyed positionally — the caller maps each
            back via ``assignment.client_id``. Connections that received no
            user are present with an empty assignment (User #0), so the
            caller can iterate the eligible set directly.
        channel_viewers: The full distinct candidate-user set for the
            channel, in rank order (most-recent first). This is what the
            channel-level ``*_viewers`` list surfaces. Surplus users
            (``users > connections``) appear ONLY here, never stamped onto
            a connection.
    """

    assignments: tuple[ConnectionAssignment, ...] = ()
    channel_viewers: tuple[CandidateUser, ...] = ()

    def assignment_for(self, client_id: str) -> Optional[ConnectionAssignment]:
        """Return the assignment for ``client_id``, or ``None`` if absent."""
        for a in self.assignments:
            if a.client_id == client_id:
                return a
        return None


# ---------------------------------------------------------------------------
# IP-priority ranking (soft hint, never a gate)
# ---------------------------------------------------------------------------


def _parse_networks(cidrs: list[str]) -> list[ipaddress._BaseNetwork]:
    """Parse a list of CIDR/IP strings into network objects, skipping junk.

    Bare IPs (``"172.16.0.19"``) are accepted and treated as /32 (or /128)
    host networks. Malformed entries are logged once at DEBUG and skipped —
    a bad operator-entered CIDR must NOT raise on the hot path; the worst
    case is that the entry contributes nothing to ranking (correctness is
    unaffected because ranking is a soft hint).
    """
    networks: list[ipaddress._BaseNetwork] = []
    for raw in cidrs or []:
        token = (raw or "").strip()
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            logger.debug(
                "[ATTR-RECONCILE] Skipping unparsable trusted-network entry %r",
                token,
            )
    return networks


def ip_priority(
    ip_address: Optional[str],
    trusted_networks: list[ipaddress._BaseNetwork],
) -> int:
    """Return the ranking bucket for one connection's source IP.

    :data:`IP_PRIORITY_TRUSTED` (sorts first) when the IP falls inside any
    trusted network; :data:`IP_PRIORITY_UNKNOWN` otherwise. NEVER returns a
    "reject" value — an unknown IP is still eligible, it just ranks lower.

    A missing or unparsable IP ranks UNKNOWN (it cannot be proven trusted)
    but is still eligible — preserving the networking-agnostic guarantee.
    """
    if not ip_address:
        return IP_PRIORITY_UNKNOWN
    try:
        addr = ipaddress.ip_address(ip_address.strip())
    except ValueError:
        return IP_PRIORITY_UNKNOWN
    for net in trusted_networks:
        # ``addr in net`` raises TypeError across v4/v6 families; guard it
        # so a v6 connection IP against a v4 trusted net just misses.
        try:
            if addr in net:
                return IP_PRIORITY_TRUSTED
        except TypeError:
            continue
    return IP_PRIORITY_UNKNOWN


def build_trusted_networks(
    *,
    server_ips: Optional[list[Optional[str]]] = None,
    configured_cidrs: Optional[list[str]] = None,
    detected_gateways: Optional[list[str]] = None,
) -> list[ipaddress._BaseNetwork]:
    """Assemble the trusted-network list used for ranking.

    The three inputs are unioned and all feed RANKING ONLY:

    * ``server_ips`` — resolved media-server IPs (Emby/Plex/Jellyfin).
      A connection egressing through the server IP is the classic
      media-mediated case and should rank first.
    * ``configured_cidrs`` — the operator's ``trusted_media_networks``
      setting (CIDRs or bare IPs). The override knob.
    * ``detected_gateways`` — auto-detected local Docker bridge gateway
      IPs. A SOFT hint: getting auto-detection wrong only reorders
      tie-breaks, never changes which users attribute.

    Returns a flat list of network objects. Order is irrelevant —
    :func:`ip_priority` treats membership in ANY entry as trusted.
    """
    tokens: list[str] = []
    for ip in server_ips or []:
        if ip:
            tokens.append(ip)
    tokens.extend(configured_cidrs or [])
    for gw in detected_gateways or []:
        if gw:
            tokens.append(gw)
    return _parse_networks(tokens)


# ---------------------------------------------------------------------------
# Eligibility + dedup
# ---------------------------------------------------------------------------


def eligible_connections(connections: list[Connection]) -> list[Connection]:
    """Return connections eligible for media-server reconciliation.

    Drops connections that carry a genuine per-client identity — either a
    URL identity (``has_url_identity``) or a Dispatcharr account identity
    (``has_account_identity``). Both mark a genuine direct-IPTV client
    attributed via the bd-gy5nd provider/hostname path; neither must be
    reconciled against media-server sessions.

    bd-rools (re-fix for the bd-cat70 direct-client cross-attribution):
    excluding account-identity connections is what stops a genuine direct
    XC subscriber (e.g. ``kmfelmer``, Dispatcharr ``user_id=3``) on a
    channel shared with a media-server viewer (``MotWakorb``) from being
    paired — under B1 direct-first ordering — to that media-server user and
    dropping the real viewer to User #0. Anonymous media-server pulls
    (``user_id == "0"`` / ``0`` / ``None``, including NAT'd browser-direct
    playback) carry no account identity and stay eligible. This is the
    per-connection discriminator that replaces both the IP gate and the
    (incorrect) channel-upstream-URL discriminator.
    """
    return [
        c for c in connections
        if not c.has_url_identity and not c.has_account_identity
    ]


def _user_dedup_key(user: CandidateUser) -> tuple:
    """Stable identity key for de-duplicating candidate users.

    Prefer ``(source, user_id)`` when an id is present; fall back to
    ``(source, user_name)`` for sources without a stable id (Plex). Keying
    on ``source`` keeps a same-named user matched by two media servers as
    two distinct candidates (rare but real for operators running multiple
    servers) — they are genuinely two viewers.
    """
    if user.user_id is not None:
        return ("id", user.source, user.user_id)
    return ("name", user.source, user.user_name)


def _recency_sort_key(user: CandidateUser):
    """Sort key placing the most-recent ``last_activity_date`` first.

    ``None`` always loses (sinks to the bottom). ISO strings and floats
    both compare correctly within their own type; the boolean
    ``has_activity`` primary key keeps populated values ahead of ``None``
    so mixed presence is well-ordered without comparing str to float.
    """
    has_activity = user.last_activity_date is not None
    return (has_activity, user.last_activity_date if has_activity else "")


def distinct_users(users: list[CandidateUser]) -> list[CandidateUser]:
    """De-dupe candidate users and return them ranked most-recent-first.

    Dedup is by :func:`_user_dedup_key`; on a collision the entry with the
    more-recent ``last_activity_date`` is kept (so a user matched by two
    tiers keeps the freshest activity timestamp). The returned list is
    sorted by recency descending — this is both the channel-level viewer
    order and the user-ranking the assignment pairs against.
    """
    best_by_key: dict[tuple, CandidateUser] = {}
    for user in users:
        if not user.user_name:
            continue
        key = _user_dedup_key(user)
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = user
            continue
        # Keep the one with the more-recent activity.
        if _recency_sort_key(user) > _recency_sort_key(existing):
            best_by_key[key] = user
    ranked = sorted(
        best_by_key.values(),
        key=_recency_sort_key,
        reverse=True,
    )
    return ranked


# ---------------------------------------------------------------------------
# Connection ranking
# ---------------------------------------------------------------------------


def _connection_sort_key(
    conn: Connection,
    trusted_networks: list[ipaddress._BaseNetwork],
):
    """Rank connections: trusted IPs first, then longest-connected first.

    Primary key is the IP-priority bucket (ascending — trusted=0 sorts
    before unknown=1). Secondary key is ``connected_at`` ascending
    (earlier connection = longer-watching = sorts first). ``None``
    ``connected_at`` sorts last within its bucket. The trailing
    ``client_id`` is a deterministic final tie-break so equal-rank
    connections have a stable, reproducible order across polls.
    """
    prio = ip_priority(conn.ip_address, trusted_networks)
    has_connected_at = conn.connected_at is not None
    # ``not has_connected_at`` so present timestamps (False) sort before
    # absent (True); within present, smaller connected_at sorts first.
    connected_at = conn.connected_at if has_connected_at else 0.0
    return (prio, not has_connected_at, connected_at, conn.client_id)


# ---------------------------------------------------------------------------
# Core reconciliation
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    """An ordered run of connections sharing one IP-priority bucket."""

    priority: int
    connections: list[Connection] = field(default_factory=list)


def _group_by_priority(
    ranked_connections: list[Connection],
    trusted_networks: list[ipaddress._BaseNetwork],
) -> list[_Group]:
    """Split rank-ordered connections into contiguous same-priority runs.

    Connections are already sorted by ``_connection_sort_key`` (priority
    first), so equal-priority connections are contiguous. Each run becomes
    a group; the Option-B ambiguity test applies WITHIN a group (same IP
    signal, no further per-connection ordering).
    """
    groups: list[_Group] = []
    for conn in ranked_connections:
        prio = ip_priority(conn.ip_address, trusted_networks)
        if groups and groups[-1].priority == prio:
            groups[-1].connections.append(conn)
        else:
            groups.append(_Group(priority=prio, connections=[conn]))
    return groups


def reconcile_channel(
    connections: list[Connection],
    users: list[CandidateUser],
    *,
    trusted_networks: Optional[list[ipaddress._BaseNetwork]] = None,
    channel_label: Optional[str] = None,
) -> ReconciliationResult:
    """Reconcile one channel's eligible connections against its candidate users.

    Implements the four PO constraints structurally:

    1. **Networking-agnostic.** Source IP is read only via
       :func:`ip_priority` to rank groups; it never excludes a
       connection. Host IP, container IP, bridge gateway, NAT'd source,
       and the configured server IP all reach the same assignment — only
       tie-break order can differ.
    2. **Anti-collapse.** Each candidate user is consumed AT MOST ONCE
       (popped from the ranked user list as it is assigned), so one user
       can never land on two connections in a single poll.
    3. **Anti-broadcast.** Assignment is per-connection (a user is paired
       to one ``client_id``); there is no path that stamps one user onto
       every connection.
    4. **No phantom clients.** When ``users > connections`` the surplus
       users appear only in :attr:`ReconciliationResult.channel_viewers`,
       never as a synthesized connection.

    Count behavior:

    * ``users == connections`` → exact 1:1 by rank.
    * ``users < connections`` → fill the top-ranked connections; the
      remainder stay User #0 (empty assignment).
    * ``users > connections`` → assign the top N users; surplus surface
      only in ``channel_viewers``.

    Server-proxy topology (bd-mlcla B1 + bd-r5f0c.9 preservation): a
    connection flagged ``is_server_proxy`` IS the media server's own
    transcoding pull and can carry multiple of the channel's viewers on one
    Dispatcharr connection. **The direct (non-proxy) connections are
    reconciled FIRST** — each distinct direct connection consumes a distinct
    candidate user at most once (the same anti-collapse group walk used in
    the proxy-free topology). **The server-proxy connection then carries the
    REMAINING (unconsumed) users** as its rollup (2+ remaining → full list,
    1 → single user, 0 → User #0). This guarantees a browser-direct viewer
    sharing a channel with a proxy pull always gets its own distinct name
    whenever an unconsumed matching user exists (the TSN5 mixed case), while
    a proxy serving N app-viewers with no direct connection still carries the
    full set (bd-r5f0c.9). This is what lets the same model serve both
    server-mediated and browser-direct playback without collapsing,
    broadcasting, or dropping a viewer to User #0.

    When two or more connections are flagged ``is_server_proxy`` on the same
    channel (an exotic multi-proxy topology), they are processed in input
    order: the first consumes the remaining set, the rest get User #0 (one
    proxy already represents every upstream session, so a second would
    double-count).

    Option B (residual ambiguity): within a single IP-priority group of
    NON-proxy connections, if the group has 2+ connections AND is offered
    2+ users, the group is genuinely unorderable, so every connection in it
    gets the SAME ``"N viewers: ..."`` rollup of the users that group
    consumed — no single (possibly-wrong) name is pinned. A group offered
    exactly one user, or a group of one connection, resolves to a single
    name.

    Args:
        connections: The channel's connections. URL-identity connections
            are filtered out internally via :func:`eligible_connections`.
        users: The distinct candidate users the resolvers matched. De-duped
            and ranked internally via :func:`distinct_users`.
        trusted_networks: Pre-built trusted-network list (see
            :func:`build_trusted_networks`). ``None`` → empty → every
            connection ranks UNKNOWN and ordering falls back to
            ``connected_at`` (still correct; IP only breaks ties).
        channel_label: Optional human-readable channel identifier (e.g.
            ``"494 | TSN 5"`` or a channel UUID + source) stamped into the
            ``[RECONCILE]`` result log so the per-channel assignment outcome
            is diagnosable in one log read. Logging-only; never affects the
            result. ``None`` → the label renders as ``"?"``.

    Returns:
        :class:`ReconciliationResult` — one assignment per eligible
        connection plus the channel-level viewer set.
    """
    nets = trusted_networks or []
    eligible = eligible_connections(connections)
    ranked_users = distinct_users(users)

    # Channel-level viewer set is the full ranked user list regardless of
    # how many connections exist (surplus users live here only).
    channel_viewers = tuple(ranked_users)

    if not eligible:
        result = ReconciliationResult(
            assignments=(), channel_viewers=channel_viewers
        )
        _log_reconcile_result(
            channel_label, connections, eligible, ranked_users, result,
        )
        return result

    # Split server-proxy connections (carry the remainder) from the rest.
    proxy_conns = [c for c in eligible if c.is_server_proxy]
    direct_conns = [c for c in eligible if not c.is_server_proxy]

    assignments_by_id: dict[str, ConnectionAssignment] = {}

    # --- bd-mlcla B1: DIRECT connections are reconciled FIRST. Each distinct
    # direct connection consumes a distinct candidate user (at most once),
    # via the same anti-collapse / Option-B group walk used in the
    # proxy-free topology. ``user_queue`` is consumed front-to-back; whatever
    # is left over is what the server-proxy connection carries. This is the
    # PO-approved "never drop the viewer" ordering — a browser-direct viewer
    # sharing a channel with a proxy pull is reconciled before the proxy, so
    # it always gets its own name when an unconsumed matching user exists.
    user_queue = list(ranked_users)  # consumed front-to-back
    _reconcile_direct_groups(
        direct_conns, user_queue, nets, assignments_by_id,
    )

    # --- Server-proxy connections carry the REMAINING (unconsumed) users.
    # The transcoding proxy genuinely carries every upstream session that no
    # distinct direct connection already claimed (bd-r5f0c.9 multi-viewer-on-
    # one-proxy). 2+ remaining → ``proxy_viewers`` full list (NOT an Option-B
    # rollup: the proxy is not ambiguous, it knows all its sessions); 1 →
    # single user; 0 → User #0. When 2+ connections are flagged proxy (exotic
    # multi-proxy topology), only the first carries the remainder — a second
    # proxy would double-count the same upstream sessions.
    remaining_users = tuple(user_queue)
    for proxy_idx, conn in enumerate(proxy_conns):
        if proxy_idx > 0 or len(remaining_users) == 0:
            assignments_by_id[conn.client_id] = ConnectionAssignment(
                client_id=conn.client_id,
            )
        elif len(remaining_users) >= 2:
            assignments_by_id[conn.client_id] = ConnectionAssignment(
                client_id=conn.client_id,
                proxy_viewers=remaining_users,
            )
        else:  # exactly one remaining user
            assignments_by_id[conn.client_id] = ConnectionAssignment(
                client_id=conn.client_id,
                user=remaining_users[0],
            )

    # Emit ALL eligible connections (direct + proxy) in input order, each
    # carrying its assignment.
    result = ReconciliationResult(
        assignments=tuple(assignments_by_id[c.client_id] for c in eligible),
        channel_viewers=channel_viewers,
    )
    _log_reconcile_result(
        channel_label, connections, eligible, ranked_users, result,
    )
    return result


def _log_reconcile_result(
    channel_label: Optional[str],
    connections: list[Connection],
    eligible: list[Connection],
    ranked_users: list[CandidateUser],
    result: ReconciliationResult,
) -> None:
    """Emit one INFO ``[RECONCILE]`` line summarising a channel's outcome.

    bd-4w9w6. Closes the bd-mlcla diagnosability gap: the reconciler had no
    result logging, so a "resolver matched but Stats shows User #0" failure
    (the reconciler dropping a viewer, or never reaching it) produced ZERO
    log lines downstream of the resolver. This line captures, per channel:
    the eligible-vs-total connection count (so an all-excluded channel is
    obvious), the candidate users offered, the per-connection assignments
    (``client_id -> user | rollup | proxy[...] | #0``), and the
    unattributed (User #0) count. Logging-only; never raises on the hot
    path (any formatting failure is swallowed).
    """
    try:
        candidate_names = [u.user_name for u in ranked_users]
        assignments_repr: dict[str, str] = {}
        unattributed = 0
        for a in result.assignments:
            if a.is_proxy_multi:
                assignments_repr[a.client_id] = (
                    "proxy[" + ", ".join(u.user_name for u in a.proxy_viewers) + "]"
                )
            elif a.is_rollup:
                assignments_repr[a.client_id] = (
                    "rollup[" + ", ".join(u.user_name for u in a.rollup_users) + "]"
                )
            elif a.user is not None:
                assignments_repr[a.client_id] = a.user.user_name
            else:
                assignments_repr[a.client_id] = "#0"
                unattributed += 1
        logger.info(
            "[RECONCILE] channel=%s conns=%d eligible=%d candidate_users=%s "
            "assignments=%s unattributed=%d (bd-4w9w6)",
            channel_label or "?",
            len(connections),
            len(eligible),
            candidate_names,
            assignments_repr,
            unattributed,
        )
    except Exception:  # noqa: BLE001 — logging must never break reconciliation
        logger.debug("[RECONCILE] result-log formatting failed", exc_info=True)


def _reconcile_direct_groups(
    direct_conns: list[Connection],
    user_queue: list[CandidateUser],
    trusted_networks: list[ipaddress._BaseNetwork],
    assignments_by_id: dict[str, ConnectionAssignment],
) -> None:
    """Assign users to direct (non-proxy) connections, consuming the queue.

    bd-mlcla B1. Walks the rank-ordered IP-priority groups of
    ``direct_conns``, consuming users from the FRONT of ``user_queue``
    (mutated in place — a user popped here is unavailable to later groups and
    to the server-proxy carrier). Writes one
    :class:`ConnectionAssignment` per direct connection into
    ``assignments_by_id``. Implements:

    * **Anti-collapse** — each user popped at most once.
    * **Anti-broadcast** — a user pairs to one ``client_id``, never all.
    * **Option B** — a single IP-priority group with 2+ connections AND 2+
      offered users is genuinely ambiguous → every row in the group gets the
      same ``rollup_users`` set; no single (possibly-wrong) name is pinned.
    * **User #0** — a connection with no user left in its group's slice gets
      an empty assignment.

    Connections with no users remaining all become User #0.
    """
    ranked_connections = sorted(
        direct_conns,
        key=lambda c: _connection_sort_key(c, trusted_networks),
    )

    if not user_queue or not ranked_connections:
        # No users left, or no direct connections — every direct connection
        # stays User #0. (The user_queue is untouched, so the proxy carrier
        # downstream still sees the full remaining set.)
        for conn in ranked_connections:
            assignments_by_id[conn.client_id] = ConnectionAssignment(
                client_id=conn.client_id
            )
        return

    groups = _group_by_priority(ranked_connections, trusted_networks)

    for group in groups:
        n_conns = len(group.connections)
        # Users this group may consume = up to one per connection, from the
        # front of the remaining queue.
        group_users = user_queue[:n_conns]
        del user_queue[:len(group_users)]

        if not group_users:
            # No users left for this group — all stay User #0.
            for conn in group.connections:
                assignments_by_id[conn.client_id] = ConnectionAssignment(
                    client_id=conn.client_id
                )
            continue

        # Option-B predicate: 2+ connections in this single IP-priority
        # group AND 2+ users offered to it → genuinely ambiguous, no signal
        # to decide which connection is which user. Roll up.
        ambiguous = n_conns >= 2 and len(group_users) >= 2
        if ambiguous:
            rollup = tuple(group_users)
            for conn in group.connections:
                assignments_by_id[conn.client_id] = ConnectionAssignment(
                    client_id=conn.client_id,
                    rollup_users=rollup,
                )
            continue

        # Unambiguous within this group: either one user (pairs to the
        # top-ranked connection, rest User #0) or one connection (pairs to
        # the one user). Pair by rank position 1:1.
        for idx, conn in enumerate(group.connections):
            if idx < len(group_users):
                assignments_by_id[conn.client_id] = ConnectionAssignment(
                    client_id=conn.client_id,
                    user=group_users[idx],
                )
            else:
                assignments_by_id[conn.client_id] = ConnectionAssignment(
                    client_id=conn.client_id
                )


# ---------------------------------------------------------------------------
# Rendering helper (shared by stats.py + bandwidth_tracker.py)
# ---------------------------------------------------------------------------


def rollup_label(users: tuple[CandidateUser, ...]) -> str:
    """Render the Option-B ``"N viewers: <comma list>"`` label.

    The distinct user list preserves the input (recency) order so the
    most-recent viewer leads. Duplicate names are collapsed (a user matched
    by two tiers should not appear twice in the label) while keeping first-
    seen order.
    """
    seen: set[str] = set()
    names: list[str] = []
    for u in users:
        if u.user_name and u.user_name not in seen:
            seen.add(u.user_name)
            names.append(u.user_name)
    return f"{len(names)} viewers: {', '.join(names)}"


def rollup_client_ips(users: tuple[CandidateUser, ...]) -> list[str]:
    """Return the distinct set of real client IPs across an Option-B rollup.

    bd-7ncci. The Option-B rollup labels a genuinely-ambiguous group of
    connections with ``"N viewers: <names>"`` rather than pinning one
    name per row. The matching Client IP display mirrors that: render the
    SET of distinct client IPs the rolled-up viewers reported, in the
    same recency order as :func:`rollup_label`, so the operator sees
    every real device IP behind the ambiguous group without a
    possibly-wrong 1:1 pin.

    Skips viewers whose ``client_ip`` is ``None`` (a source that did not
    expose the IP) so the set carries only real values. Duplicate IPs are
    collapsed (two viewers behind one NAT, or one viewer matched by two
    tiers) while preserving first-seen order. Returns an empty list when
    no viewer in the rollup carried a client IP.
    """
    seen: set[str] = set()
    ips: list[str] = []
    for u in users:
        ip = u.client_ip
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips
