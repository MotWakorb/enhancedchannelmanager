"""Plex cross-ref resolver (bd-r5f0c.2, epic bd-r5f0c).

Given a single ECM stream session (client IP + stream name), this
module answers one question: which Plex user — if any — is the actual
viewer? When operators watch ECM-mediated streams through Plex,
ECM only sees the Plex server's IP (every Plex viewer's pull comes
from the same proxy IP), so without this cross-reference Stats
collapses every Plex viewer into a single 'Plex server' identity.

Data flow (cross-references the cache, NEVER calls PlexClient directly):

    BandwidthTracker (W4, future) → resolve_plex_user(ip, name)
       → get_cached_plex_sessions()                [plex_cache]
            → cache hit OR PlexClient.get_sessions  [plex_client]
       → match stream_name against now-playing
       → return user_name | None

Match algorithm:

1. Extract the Plex server's IP from ``settings.plex_base_url``.
   For IP-literal URLs (``http://192.168.1.20:32400``) we compare
   directly. For hostname URLs (``https://plex.local:32400``) we resolve
   via :func:`socket.gethostbyname` ONCE per process and cache the
   result — repeated polls (every ~5s) must not thrash DNS.
2. If the ECM session's client IP does NOT equal the Plex server IP,
   short-circuit with ``None`` BEFORE calling the cache. Every poll
   cycle visits this branch for every non-Plex-mediated session, so the
   short-circuit is the load-bearing optimization.
3. Fetch cached Plex sessions. The cache transparently handles the
   "Plex disabled" case (returns ``[]``) so the resolver does not need
   to inspect ``settings.plex_enabled``.
4. For each session, score ``now_playing_item_name`` via three tiers:
   * Tier 1 — exact (NFC + lowercase + strip) match on channel_name
     parsed from pipe-suffix format ``"<number> | <name>"``;
   * Tier 2 — channel_number exact string compare;
   * Tier 3 — RapidFuzz ``token_set_ratio / 100`` against 0.85 floor.
5. Multiple matches (rare — same channel on multiple Plex clients):
   pick the most-recent ``last_activity_date`` datetime. ``None``
   always loses to any populated datetime.

Design constraints:

* **Never instantiate PlexClient directly.** The cache owns the upstream
  contract — going around it would defeat the thundering-herd guard,
  the stale-fallback policy, and the settings gate all at once.
* **DNS resolution is cached module-level.** A failed resolution is also
  cached (as the sentinel ``_UNRESOLVED``) so we log the WARN once and
  return ``None`` cheaply on every subsequent poll until the process
  restarts. Operators fixing DNS will restart ECM anyway.
* **Logging is DEBUG-default.** This is on the BandwidthTracker poll
  loop hot path; INFO-level chatter per resolve call would drown the
  log. WARN is reserved for unresolvable hostnames (operator action
  required).
* **Never raises.** Return None on any failure path; the bandwidth
  tracker depends on this guarantee.
"""
from __future__ import annotations

import logging
import socket
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from rapidfuzz import fuzz

from config import get_settings
from plex_client import PlexSession
from services.plex_cache import get_cached_plex_sessions
import observability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlexAttribution:
    """The resolved Plex identity for one ECM stream session.

    bd-r5f0c.9 multi-viewer addition. Pre-bd-r5f0c.9 the Plex resolver
    surfaced only ``user_name`` (Plex's ``/status/sessions`` exposes a
    numeric ``User/@id`` but the resolver didn't surface it). This
    dataclass mirrors :class:`EmbyAttribution` /
    :class:`JellyfinAttribution` for shape symmetry across the three
    resolvers' plural variants.

    The ``user_id`` slot is ``None`` when the Plex resolver does not
    expose a stable upstream identifier — the bandwidth_tracker writer
    tolerates this by writing ``None`` to ``plex_user_id`` (and to the
    ``user_id`` key in the encoded ``plex_viewers`` JSON list).
    """

    user_name: str
    user_id: str | None = None


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


# Fuzzy match threshold per the bead spec. Operators do NOT tune this —
# Plex's now-playing names are typically clean enough that 0.85 gives
# very few false positives, and a per-deployment knob here would only
# encourage attribution drift in Stats. If experience proves it
# needs tuning, that's a follow-up bead, not a settings field.
FUZZY_MATCH_THRESHOLD: float = 0.85


# Sentinel for "we tried to resolve this hostname and it failed". Cached
# in ``_dns_cache`` so the WARN is logged once and subsequent polls
# return ``None`` cheaply without re-attempting the DNS lookup.
_UNRESOLVED = object()

# Rate-limit the resolver WARN for multiple-candidate disambiguation
# so noisy Plex setups don't flood the log. Module-level timestamp.
_plex_resolver_last_warn_at: float | None = None
_WARN_RATE_LIMIT_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


# Map of hostname → resolved IP (or ``_UNRESOLVED`` sentinel). The
# resolver consults this BEFORE calling ``socket.gethostbyname`` so we
# never resolve the same hostname twice in one process lifetime.
_dns_cache: dict[str, str | object] = {}


# bd-r5f0c.10: per-(client_ip, normalized_channel_name) rate-limit
# timestamps for the no-match forensic WARN. A dict (not a single global
# timestamp) so a noisy problem channel does not silence diagnosis for
# other channels the same operator is also having trouble with.
_plex_resolver_no_match_last_warn_at: dict[tuple[str, str], float] = {}


# Minimum interval between WARN emissions for the same (ip, channel)
# pair. 60 s mirrors the Emby resolver helper for shape symmetry.
_PLEX_NO_MATCH_WARN_INTERVAL: float = 60.0


# Max sessions in the forensic log line — defensive against log-bloat
# on operators with very large Plex session counts.
# bd-r5f0c.11: bumped 10 → 30 to match emby_resolver after the PO's
# v0.17.1 forensic showed 17 live sessions and the cap-of-10 hid the
# session that mattered. 30 covers typical operator scale.
_PLEX_NO_MATCH_MAX_SESSIONS: int = 30


def _reset_for_tests() -> None:
    """Clear the DNS cache, warn rate-limit, and no-match rate-limit — tests only.

    Tests share the same module instance across test functions; without
    this reset, a hostname-resolution result from one test would leak
    into the next and produce false matches. bd-r5f0c.10 also added a
    per-(ip, channel) no-match WARN timestamp dict that must be cleared
    so rate-limit tests are isolated. Production code paths do not call
    this.
    """
    global _plex_resolver_last_warn_at
    _dns_cache.clear()
    _plex_resolver_last_warn_at = None
    _plex_resolver_no_match_last_warn_at.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_plex_users(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> list[PlexAttribution]:
    """Cross-reference one ECM stream against the live Plex session list (multi-viewer).

    bd-r5f0c.9 (parent epic bd-r5f0c). Plex is a transcoding proxy — N
    upstream viewers share one ECM client (the Plex server itself), so
    the resolver was matching all N sessions across the tiers but
    ``_tiebreak_most_recent`` was collapsing the list to a single
    winner. This plural variant returns the FULL list so every viewer
    is captured.

    Returns the list of every Plex session that matched any tier,
    sorted ``last_activity_date`` descending so position 0 is the
    most-recent viewer. Empty list when:

    * The ECM session's client IP does NOT match the Plex server IP,
    * No Plex sessions are playing,
    * No tier matched,
    * Any defensive failure (DNS resolution failure, malformed base URL).

    Tier semantics, hot-path discipline, and DNS caching are identical
    to the pre-bd-r5f0c.9 single-viewer path — see
    :func:`_find_matching_sessions` for the per-tier scoring.

    Metric semantics (W6 observability work):

    * ``ecm_user_attribution_resolved_total{source="plex"}`` fires
      PER VIEWER — if N viewers matched, the counter increments by N.
    * ``ecm_user_attribution_unresolved_total{source="plex"}`` fires
      ONLY when the resolver entered (IP matched the Plex server) AND
      produced an empty list.

    Never raises. The BandwidthTracker poll loop calls this on every
    active session every ~5 seconds; the top-level ``except Exception``
    in :func:`_resolve_plex_users_inner` backstops any unexpected
    failure path and returns ``[]`` with a DEBUG log so the caller
    always gets a result.
    """
    try:
        return await _resolve_plex_users_inner(
            ecm_session_ip=ecm_session_ip,
            ecm_stream_name=ecm_stream_name,
            ecm_channel_name=ecm_channel_name,
            ecm_channel_number=ecm_channel_number,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PLEX] Unexpected resolver error: %s", exc)
        return []


async def _resolve_plex_users_inner(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
) -> list[PlexAttribution]:
    """Inner implementation of :func:`resolve_plex_users` — may raise.

    Wrapped by ``resolve_plex_users`` which catches all exceptions so
    the BandwidthTracker poll loop never sees a raised exception from
    this path.
    """
    settings = get_settings()
    base_url = getattr(settings, "plex_base_url", "") or ""

    plex_server_ip = _resolve_plex_server_ip(base_url)
    if plex_server_ip is None:
        return []

    if ecm_session_ip != plex_server_ip:
        # Hot path short-circuit.
        return []

    try:
        sessions = await get_cached_plex_sessions()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PLEX] Unexpected error fetching cached sessions: %s", exc)
        return []

    if not sessions:
        return []

    matches = _find_matching_sessions(
        ecm_stream_name=ecm_stream_name,
        ecm_channel_name=ecm_channel_name,
        ecm_channel_number=ecm_channel_number,
        sessions=sessions,
    )
    if not matches:
        logger.debug(
            "[PLEX] No match for stream=%r channel=%r number=%r "
            "against %d Plex session(s)",
            ecm_stream_name, ecm_channel_name, ecm_channel_number,
            len(sessions),
        )
        # bd-r5f0c.10: forensic WARN — IP matched, sessions exist, no
        # tier produced a match. Guarded on ``sessions`` non-empty so
        # the normal idle state stays silent.
        if sessions:
            _log_plex_resolver_no_match(
                client_ip=ecm_session_ip,
                ecm_channel_name=ecm_channel_name,
                ecm_channel_number=ecm_channel_number,
                ecm_stream_name=ecm_stream_name,
                sessions=sessions,
            )
        observability.get_metric("user_attribution_unresolved_total").labels(source="plex").inc()
        return []

    sorted_matches = _sort_by_recency_descending(matches)

    if len(sorted_matches) > 1:
        _log_disambiguation_warn(
            count=len(sorted_matches),
            ecm_session_ip=ecm_session_ip,
            name=ecm_channel_name or ecm_stream_name,
            winner_name=sorted_matches[0].user_name,
        )
    else:
        logger.debug(
            "[PLEX] Resolved stream=%r → user=%s from 1 match",
            ecm_stream_name, sorted_matches[0].user_name,
        )

    metric = observability.get_metric("user_attribution_resolved_total")
    for _ in sorted_matches:
        metric.labels(source="plex").inc()

    # Plex's PlexSession does not expose a stable upstream user identifier
    # on the public ``/status/sessions`` surface today. The Attribution
    # carries ``user_id=None``; the column tolerates it (NULL).
    return [
        PlexAttribution(user_name=session.user_name, user_id=None)
        for session in sorted_matches
    ]


async def resolve_plex_user(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> str | None:
    """Back-compat wrapper — return the most-recent matching Plex user_name.

    Pre-bd-r5f0c.9 callers (W4 ``BandwidthTracker`` shim, stats.py
    ``_enrich_one_source``, and the existing test surface) call this
    function expecting at most one user_name string (the pre-bd-r5f0c.9
    contract: ``str | None``). bd-r5f0c.9 split the multi-viewer plural
    variant out as :func:`resolve_plex_users`; this wrapper returns
    position 0's user_name to preserve the existing contract verbatim.

    Returns the matching Plex user's name when one or more Plex sessions
    are playing this stream, else ``None``. Multiple matches (multi-
    viewer scenarios — same channel on multiple Plex clients) tie-break
    on most-recent ``last_activity_date`` exactly as before bd-r5f0c.9.

    The tiered matching algorithm is documented on
    :func:`resolve_plex_users`; this wrapper inherits the same tiering,
    short-circuits, and metric semantics.

    Never raises. Defensive failures return ``None``.
    """
    users = await resolve_plex_users(
        ecm_session_ip,
        ecm_stream_name,
        ecm_channel_name=ecm_channel_name,
        ecm_channel_number=ecm_channel_number,
    )
    return users[0].user_name if users else None


# ---------------------------------------------------------------------------
# Internals: server IP resolution
# ---------------------------------------------------------------------------


def _resolve_plex_server_ip(base_url: str) -> str | None:
    """Extract the Plex server's IP from the configured base URL.

    For IP-literal URLs (``http://192.168.1.20:32400``) the hostname
    component IS the IP — return it directly. For hostname URLs
    (``https://plex.local:32400``) resolve via :func:`socket.gethostbyname`
    and cache the result module-level so the next ~5-second poll cycle
    reuses it.

    Returns ``None`` on any failure (empty URL, malformed URL,
    unresolvable hostname). Each failure mode logs once at the
    appropriate level — operators need to know about DNS failures, but
    not on every poll cycle.
    """
    if not base_url:
        # Empty base_url indicates Plex is unconfigured. The cache layer
        # would also return [] in this state; we just exit early.
        return None

    try:
        parsed = urlparse(base_url)
    except ValueError:
        logger.warning("[PLEX] Could not parse plex_base_url=%r", base_url)
        return None

    host = parsed.hostname
    if not host:
        logger.warning(
            "[PLEX] plex_base_url=%r has no extractable hostname", base_url,
        )
        return None

    if _looks_like_ip_literal(host):
        # Hostname IS already an IP — no DNS needed.
        return host

    # Hostname case: consult the DNS cache before calling out.
    cached = _dns_cache.get(host)
    if cached is _UNRESOLVED:
        # Prior call failed and we've already WARN'd. Stay silent now.
        return None
    if isinstance(cached, str):
        return cached

    try:
        resolved = socket.gethostbyname(host)
    except socket.gaierror as exc:
        # DNS failure. WARN once (the cache prevents repeat WARNs) and
        # poison the cache with the sentinel so subsequent polls short
        # circuit cheaply.
        logger.warning(
            "[PLEX] Could not resolve plex_base_url hostname %r: %s. "
            "Plex attribution will be disabled until ECM restarts and "
            "DNS resolves successfully.",
            host, exc,
        )
        _dns_cache[host] = _UNRESOLVED
        return None

    _dns_cache[host] = resolved
    logger.debug("[PLEX] Resolved %s → %s (cached)", host, resolved)
    return resolved


def _looks_like_ip_literal(host: str) -> bool:
    """True when ``host`` is an IPv4/IPv6 literal, not a hostname.

    Use :func:`socket.inet_pton` for both families — it returns a packed
    address on success and raises ``OSError`` otherwise.
    """
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, host)
            return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Internals: matching
# ---------------------------------------------------------------------------


def _normalize(value: str) -> str:
    """NFC + lowercase + strip — same normalization as emby_resolver."""
    return unicodedata.normalize("NFC", value).lower().strip()


def _find_matching_sessions(
    *,
    ecm_stream_name: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
    sessions: list[PlexSession],
) -> list[PlexSession]:
    """Return every Plex session that matches across any of the three tiers.

    Tiered match (mirrors emby_resolver's bd-zldrq approach):

    * **Tier 1** — channel name primary. Parse ``item_name`` as
      ``"<number> | <name>"``; the right-hand part (or the whole
      string when there's no ``"|"`` separator) must equal
      ``ecm_channel_name`` after normalization.
    * **Tier 2** — channel number exact (string compare against the
      left-hand side of ``"<number> | <name>"``). Skipped when either
      side is missing.
    * **Tier 3** — RapidFuzz ``token_set_ratio`` on
      ``ecm_stream_name`` against ``item_name`` with the bead-spec
      0.85 floor. Back-compat for movies / VOD where neither channel
      argument is available.

    A session matches if ANY tier accepts it. The list is the union of
    tier hits — duplicates are de-duped by session identity so the same
    physical session is not double-counted in the tie-break.
    """
    # Pre-normalize Plex session strings ONCE — hot-path performance.
    prepared: list[tuple[PlexSession, str, str, str]] = []
    for session in sessions:
        normalized_item = _normalize(session.now_playing_item_name or "")
        # The right-hand side of "<number> | <name>" (or empty when
        # there's no pipe). Tier 1 compares this to ecm_channel_name.
        suffix = _parse_pipe_suffix(normalized_item)
        # The left-hand side of "<number> | <name>" — used for tier 2.
        prefix = _parse_pipe_prefix(normalized_item)
        prepared.append((session, normalized_item, suffix, prefix))

    # Track matched sessions by identity to avoid double-counting when
    # two tiers both accept the same physical session.
    matched_ids: set[str] = set()
    matches: list[PlexSession] = []

    def _accept(session: PlexSession) -> None:
        if session.session_id in matched_ids:
            return
        matched_ids.add(session.session_id)
        matches.append(session)

    # ----- Tier 1: channel name primary match
    normalized_ecm_channel = _normalize(ecm_channel_name or "")
    # bd-r5f0c.11: when an operator imports channels with the Emby/M3U
    # pipe-prefix display format leaked into the ECM channel_name itself
    # (e.g. "109 | CNN"), tier-1 must also parse the ECM side so the
    # right-hand suffix can be compared against the session forms.
    # Reuses _parse_pipe_suffix as-is — it returns "" when the input
    # has no pipe, so this is a no-op for clean ECM names.
    ecm_channel_suffix = _parse_pipe_suffix(normalized_ecm_channel)
    if normalized_ecm_channel:
        for session, normalized_item, suffix, _prefix in prepared:
            # Right-hand side of "<number> | <name>" matches (the
            # primary live-TV path), OR the whole item_name matches
            # (some Plex installs have no "<number> | " prefix).
            if suffix and suffix == normalized_ecm_channel:
                _accept(session)
                continue
            if normalized_item and normalized_item == normalized_ecm_channel:
                _accept(session)
                continue
            # bd-r5f0c.11: ECM-side pipe-prefix tolerance. When ECM's
            # channel_name itself carries "<number> | <name>" (M3U
            # import leak), compare the parsed ECM suffix against the
            # session's parsed suffix and its whole item_name. Two new
            # compares; additive — gated on ecm_channel_suffix being
            # non-empty so clean ECM names are unaffected.
            if ecm_channel_suffix:
                if suffix and suffix == ecm_channel_suffix:
                    _accept(session)
                    continue
                if normalized_item and normalized_item == ecm_channel_suffix:
                    _accept(session)

    # ----- Tier 2: channel number exact (string compare against prefix)
    if ecm_channel_number is not None:
        ecm_number_str = str(ecm_channel_number).strip()
        if ecm_number_str:
            for session, _it, _sfx, prefix in prepared:
                # Match the numeric prefix of "<number> | <name>"
                if prefix and prefix.strip() == ecm_number_str:
                    _accept(session)

    # ----- Tier 3: legacy fuzzy fallback on stream_name
    normalized_stream = _normalize(ecm_stream_name or "")
    if normalized_stream:
        for session, normalized_item, _sfx, _pfx in prepared:
            if _fuzzy_or_exact_match(normalized_stream, normalized_item):
                _accept(session)

    return matches


def _parse_pipe_suffix(normalized_item_name: str) -> str:
    """Return the right-hand side of a ``"<number> | <name>"`` string.

    Plex renders live-TV ``title`` as e.g. ``"408 | ESPN"``;
    the operator-visible channel name is the part after the pipe. When
    there's no pipe (VOD / movies / episodes), return the empty string.

    Input is assumed already normalized (NFC + lowercase + strip).
    """
    if "|" not in normalized_item_name:
        return ""
    _prefix, _sep, suffix = normalized_item_name.partition("|")
    return suffix.strip()


def _parse_pipe_prefix(normalized_item_name: str) -> str:
    """Return the left-hand side of a ``"<number> | <name>"`` string.

    Used by tier 2 to extract the channel number prefix for direct
    comparison against ``ecm_channel_number``.

    Input is assumed already normalized (NFC + lowercase + strip).
    """
    if "|" not in normalized_item_name:
        return ""
    prefix, _sep, _suffix = normalized_item_name.partition("|")
    return prefix.strip()


def _fuzzy_or_exact_match(normalized_stream: str, normalized_candidate: str) -> bool:
    """True iff the normalized stream and candidate exact-match OR fuzzy-score
    above ``FUZZY_MATCH_THRESHOLD``.

    Empty candidate cannot match.
    """
    if not normalized_candidate:
        return False
    if normalized_stream == normalized_candidate:
        return True
    # token_set_ratio returns 0–100; normalize to 0–1 to match the
    # threshold semantics in the bead spec.
    score = fuzz.token_set_ratio(normalized_stream, normalized_candidate) / 100.0
    return score >= FUZZY_MATCH_THRESHOLD


def _plex_recency_key(s: PlexSession) -> float:
    """Return a comparable recency score for one Plex session.

    Plex timestamps are ``datetime`` objects. ``None`` always loses to
    any populated datetime — mapped to ``float("-inf")`` so populated
    timestamps sort higher regardless of timezone awareness. The
    ``.timestamp()`` conversion handles both timezone-aware and naive
    datetimes uniformly.
    """
    if s.last_activity_date is None:
        return float("-inf")
    try:
        return s.last_activity_date.timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def _tiebreak_most_recent(sessions: list[PlexSession]) -> PlexSession:
    """Pick the session with the most-recent ``last_activity_date``.

    bd-r5f0c.9: retained as the one-winner helper for legacy paths
    that still want a single match. The multi-viewer path uses
    :func:`_sort_by_recency_descending` which returns the FULL sorted
    list.
    """
    if len(sessions) == 1:
        return sessions[0]
    return max(sessions, key=_plex_recency_key)


def _sort_by_recency_descending(sessions: list[PlexSession]) -> list[PlexSession]:
    """Return ``sessions`` sorted by ``last_activity_date`` descending.

    bd-r5f0c.9 multi-viewer attribution: the plural resolver returns
    every matched session so every viewer is captured, with position 0
    being the most-recent viewer. Same ``None``-loses semantic as
    :func:`_tiebreak_most_recent`.

    Stable across equal timestamps (Python's ``sorted`` is stable) so
    the input order is preserved for ties.
    """
    return sorted(sessions, key=_plex_recency_key, reverse=True)


def _log_plex_resolver_no_match(
    *,
    client_ip: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
    ecm_stream_name: str,
    sessions: list[PlexSession],
) -> None:
    """Emit a structured WARN once per (client_ip, channel_name) per 60 s
    when the Plex resolver had sessions to compare against and the IP
    short-circuit passed, but no tier produced a match.

    bd-r5f0c.10 forensic logging — mirrors the Emby resolver helper.
    See :func:`emby_resolver._log_emby_resolver_no_match` for the full
    rationale. PlexSession exposes a smaller field set
    (no separate ``now_playing_channel_name`` or ``channel_number`` — Plex
    encodes both as the ``<number> | <name>`` pipe form on
    ``now_playing_item_name``) so the per-session payload surfaces the
    parsed pipe prefix and suffix alongside the raw item name. The
    ``last_activity_date`` is a ``datetime`` here (vs an ISO string for
    Emby/Jellyfin), serialized as ``isoformat()`` for readability.

    Callers MUST guard on non-empty ``sessions`` — an empty session list
    is a normal state and would spam the log with useless lines.
    """
    rate_key = (client_ip, _normalize(ecm_channel_name or ""))
    now = time.monotonic()
    last = _plex_resolver_no_match_last_warn_at.get(rate_key, 0.0)
    if now - last < _PLEX_NO_MATCH_WARN_INTERVAL:
        return
    _plex_resolver_no_match_last_warn_at[rate_key] = now

    truncated = sessions[:_PLEX_NO_MATCH_MAX_SESSIONS]
    session_payload = [
        {
            "session_id": (s.session_id[:8] if s.session_id else None),
            "item_name": s.now_playing_item_name,
            "item_name_norm": _normalize(s.now_playing_item_name or ""),
            "item_name_pipe_prefix": _parse_pipe_prefix(
                _normalize(s.now_playing_item_name or "")
            ),
            "item_name_pipe_suffix": _parse_pipe_suffix(
                _normalize(s.now_playing_item_name or "")
            ),
            "last_activity": (
                s.last_activity_date.isoformat()
                if s.last_activity_date is not None
                else None
            ),
        }
        for s in truncated
    ]
    logger.warning(
        "[PLEX-RESOLVER] no-match diagnostic: ip=%s ecm_channel=%r "
        "ecm_channel_norm=%r ecm_channel_number=%r ecm_stream=%r "
        "ecm_stream_norm=%r sessions_count=%d sessions=%s",
        client_ip,
        ecm_channel_name,
        _normalize(ecm_channel_name or ""),
        ecm_channel_number,
        ecm_stream_name,
        _normalize(ecm_stream_name or ""),
        len(sessions),
        session_payload,
    )


def _log_disambiguation_warn(
    count: int,
    ecm_session_ip: str,
    name: str,
    winner_name: str,
) -> None:
    """Log a rate-limited DEBUG line when multiple candidates are found.

    Using DEBUG (not WARN) because this is on the hot path — multiple
    matches are rare but not operator-actionable. The rate-limit prevents
    flooding when a noisy Plex setup repeatedly matches multiple sessions.
    """
    global _plex_resolver_last_warn_at
    import time as _time

    now = _time.monotonic()
    if (
        _plex_resolver_last_warn_at is None
        or now - _plex_resolver_last_warn_at >= _WARN_RATE_LIMIT_SECONDS
    ):
        logger.debug(
            "[PLEX] resolver: %d candidates for ip=%s name=%s, "
            "picked %s by recency",
            count, ecm_session_ip, name, winner_name,
        )
        _plex_resolver_last_warn_at = now
