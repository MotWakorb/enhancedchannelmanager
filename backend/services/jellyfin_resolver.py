"""Jellyfin cross-ref resolver (bd-r5f0c.3, epic bd-r5f0c).

Given a single ECM stream session (client IP + stream name), this
module answers one question: which Jellyfin user — if any — is the actual
viewer? When operators watch ECM-mediated streams through Jellyfin,
ECM only sees the Jellyfin server's IP (every Jellyfin viewer's pull comes
from the same proxy IP), so without this cross-reference Stats
collapses every Jellyfin viewer into a single 'Jellyfin server' identity.

Data flow (cross-references the cache, NEVER calls JellyfinClient directly):

    BandwidthTracker (W4) → resolve_jellyfin_user(ip, name, channel, number)
       → get_cached_jellyfin_sessions()                 [jellyfin_cache]
            → cache hit OR JellyfinClient.get_sessions  [jellyfin_client]
       → match stream_name against now-playing
       → return JellyfinAttribution(user_id, user_name) | None

Match algorithm:

1. Extract the Jellyfin server's IP from ``settings.jellyfin_base_url``.
   For IP-literal URLs (``http://192.168.1.10:8096``) we compare
   directly. For hostname URLs (``https://jf.local:8920``) we resolve
   via :func:`socket.gethostbyname` ONCE per process and cache the
   result — repeated polls (every ~5s) must not thrash DNS.
2. If the ECM session's client IP does NOT equal the Jellyfin server IP,
   short-circuit with ``None`` BEFORE calling the cache. Every poll
   cycle visits this branch for every non-Jellyfin-mediated session, so
   the short-circuit is the load-bearing optimization.
3. Fetch cached Jellyfin sessions. The cache transparently handles the
   "Jellyfin disabled" case (returns ``[]``) so the resolver does not
   need to inspect ``settings.jellyfin_enabled``.
4. Tiered match (mirrors emby_resolver's tiered approach):
   * **Tier 1 — channel_name.** Parse each Jellyfin session's
     ``now_playing_item_name`` as ``"<number> | <name>"`` where the
     suffix is the channel name. TOLERANT: if no pipe is present in the
     item name, the resolver treats the WHOLE string as the channel name
     (Jellyfin's ``NowPlayingItem.Name`` is often bare, e.g. ``"ESPN"``
     rather than Emby's ``"408 | ESPN"``). Compare case-insensitively
     against ``ecm_channel_name``.
   * **Tier 2 — channel_number.** When both ``ecm_channel_number`` and
     the session's ``channel_number`` are present, string-compare.
   * **Tier 3 — fuzzy stream_name.** RapidFuzz ``token_set_ratio / 100``
     against ``FUZZY_MATCH_THRESHOLD`` (0.85). Back-compat for movies /
     VOD where neither channel argument is available.
5. Multiple matches (rare — same channel on multiple Jellyfin clients):
   pick the most-recent ``last_activity_date``. Jellyfin's ISO timestamps
   are lexicographically comparable so plain string compare works;
   ``None`` always loses to any populated timestamp.

Design constraints:

* **Never instantiate JellyfinClient directly.** The cache owns the
  upstream contract — going around it would defeat the thundering-herd
  guard, the stale-fallback policy, and the settings gate all at once.
* **DNS resolution is cached module-level.** A failed resolution is also
  cached (as the sentinel ``_UNRESOLVED``) so we log the WARN once and
  return ``None`` cheaply on every subsequent poll until the process
  restarts.
* **Logging is DEBUG-default.** This is on the BandwidthTracker poll
  loop hot path; INFO-level chatter per resolve call would drown the
  log. WARN is reserved for unresolvable hostnames (operator action
  required).
"""
from __future__ import annotations

import logging
import socket
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from config import get_settings
from jellyfin_client import JellyfinSession
from services.jellyfin_cache import get_cached_jellyfin_sessions
import observability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


# Fuzzy match threshold per the bead spec. Operators do NOT tune this.
FUZZY_MATCH_THRESHOLD: float = 0.85

# Minimum interval between WARN-level disambiguation log messages.
# On a busy system with many concurrent sessions on the same channel, a
# per-call WARN would flood the log. Rate-limit to at most one per minute.
_JELLYFIN_RESOLVER_WARN_INTERVAL: float = 60.0

# Sentinel for "we tried to resolve this hostname and it failed".
_UNRESOLVED = object()


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


# Map of hostname → resolved IP (or ``_UNRESOLVED`` sentinel).
_dns_cache: dict[str, str | object] = {}

# Wall-clock time of the last disambiguation WARN log. ``None`` means
# the WARN has never been emitted this process lifetime.
_jellyfin_resolver_last_warn_at: float | None = None


# bd-r5f0c.10: per-(client_ip, normalized_channel_name) rate-limit
# timestamps for the no-match forensic WARN. A dict (not a single global
# timestamp) so a noisy problem channel does not silence diagnosis for
# other channels the same operator is also having trouble with.
_jellyfin_resolver_no_match_last_warn_at: dict[tuple[str, str], float] = {}


# Minimum interval between WARN emissions for the same (ip, channel)
# pair. Same 60 s as the existing disambiguation WARN above and as the
# Emby/Plex no-match WARNs.
_JELLYFIN_NO_MATCH_WARN_INTERVAL: float = 60.0


# Max sessions in the forensic log line — defensive against log-bloat
# on operators with very large Jellyfin session counts.
# bd-r5f0c.11: bumped 10 → 30 to match emby_resolver after the PO's
# v0.17.1 forensic showed 17 live sessions and the cap-of-10 hid the
# session that mattered. 30 covers typical operator scale.
_JELLYFIN_NO_MATCH_MAX_SESSIONS: int = 30


def _reset_for_tests() -> None:
    """Clear the DNS cache, warn timestamp, and no-match rate-limit — tests only.

    Tests share the same module instance across test functions; without
    this reset, a hostname-resolution result from one test would leak
    into the next and produce false matches. bd-r5f0c.10 also added a
    per-(ip, channel) no-match WARN timestamp dict that must be cleared
    so rate-limit tests are isolated.
    """
    global _jellyfin_resolver_last_warn_at
    _dns_cache.clear()
    _jellyfin_resolver_last_warn_at = None
    _jellyfin_resolver_no_match_last_warn_at.clear()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JellyfinAttribution:
    """The resolved Jellyfin identity for one ECM stream session.

    Frozen so callers cannot mutate the attribution between the resolver
    returning it and the BandwidthTracker writing it to
    ``session_telemetry.jellyfin_user_id`` / ``jellyfin_user_name``.

    Attributes:
        user_id: Jellyfin user UUID (the ``UserId`` field from the live
            session payload). Persisted to
            ``session_telemetry.jellyfin_user_id``.
        user_name: Human-readable Jellyfin username. Persisted to
            ``session_telemetry.jellyfin_user_name`` and surfaced in
            Stats > User Watch Time.
    """

    user_id: str
    user_name: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_jellyfin_users(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> list[JellyfinAttribution]:
    """Cross-reference one ECM stream against the live Jellyfin session list (multi-viewer).

    bd-r5f0c.9 (parent epic bd-r5f0c). Jellyfin is a transcoding proxy
    — N upstream viewers share one ECM client (the Jellyfin server
    itself), so the resolver was matching all N sessions across the
    tiers but ``_tiebreak_most_recent`` was collapsing the list to a
    single winner. This plural variant returns the FULL list so every
    viewer is captured.

    Returns the list of every Jellyfin session that matched any tier,
    sorted ``last_activity_date`` descending so position 0 is the
    most-recent viewer. Empty list when:

    * The ECM session's client IP does NOT match the Jellyfin server IP,
    * No Jellyfin sessions are playing,
    * No tier matched,
    * Any defensive failure (DNS resolution failure, malformed base URL).

    Tier semantics (live-TV-tolerant, Jellyfin's bare-channel-name path):

    1. **Tier 1 — channel_name** (with bare-name tolerance).
    2. **Tier 2 — channel_number exact** (defensive fallback).
    3. **Tier 3 — fuzzy stream_name** (legacy VOD path).

    Metric semantics (W6 observability work):

    * ``ecm_user_attribution_resolved_total{source="jellyfin"}`` fires
      PER VIEWER — if N viewers matched, the counter increments by N.
    * ``ecm_user_attribution_unresolved_total{source="jellyfin"}`` fires
      ONLY when the resolver entered (IP matched the Jellyfin server)
      AND produced an empty list.

    Never raises. Defensive failures return ``[]``.
    """
    settings = get_settings()
    base_url = getattr(settings, "jellyfin_base_url", "") or ""

    jellyfin_server_ip = _resolve_jellyfin_server_ip(base_url)
    if jellyfin_server_ip is None:
        return []

    if ecm_session_ip != jellyfin_server_ip:
        return []

    sessions = await get_cached_jellyfin_sessions()
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
            "[JELLYFIN] No match for stream=%r channel=%r number=%r "
            "against %d Jellyfin session(s)",
            ecm_stream_name, ecm_channel_name, ecm_channel_number,
            len(sessions),
        )
        # bd-r5f0c.10: forensic WARN — IP matched, sessions exist, no
        # tier produced a match. Guarded on ``sessions`` non-empty so
        # the normal idle state stays silent.
        if sessions:
            _log_jellyfin_resolver_no_match(
                client_ip=ecm_session_ip,
                ecm_channel_name=ecm_channel_name,
                ecm_channel_number=ecm_channel_number,
                ecm_stream_name=ecm_stream_name,
                sessions=sessions,
            )
        observability.get_metric("user_attribution_unresolved_total").labels(source="jellyfin").inc()
        return []

    sorted_matches = _sort_by_recency_descending(matches)

    if len(sorted_matches) > 1:
        _maybe_warn_disambiguation(
            len(sorted_matches), ecm_session_ip,
            ecm_channel_name or ecm_stream_name,
            sorted_matches[0].user_name,
        )
        logger.debug(
            "[JELLYFIN] resolver: %d candidates for ip=%s name=%s, "
            "all returned (multi-viewer; most-recent first: %s)",
            len(sorted_matches), ecm_session_ip,
            ecm_channel_name or ecm_stream_name,
            sorted_matches[0].user_name,
        )
    else:
        logger.debug(
            "[JELLYFIN] Resolved stream=%r → user=%s (uid=%s) from 1 match",
            ecm_stream_name,
            sorted_matches[0].user_name,
            sorted_matches[0].user_id,
        )

    metric = observability.get_metric("user_attribution_resolved_total")
    for _ in sorted_matches:
        metric.labels(source="jellyfin").inc()

    return [
        JellyfinAttribution(user_id=session.user_id, user_name=session.user_name)
        for session in sorted_matches
    ]


async def resolve_jellyfin_user(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> JellyfinAttribution | None:
    """Back-compat wrapper — return the most-recent matching Jellyfin user.

    Pre-bd-r5f0c.9 callers (W4 ``BandwidthTracker`` shim, stats.py
    ``_enrich_one_source``, and the existing test surface) call this
    function expecting at most one attribution. bd-r5f0c.9 split the
    multi-viewer plural variant out as :func:`resolve_jellyfin_users`;
    this wrapper returns position 0 of that list (most-recent viewer)
    to preserve every existing caller's contract verbatim.

    Returns the matching Jellyfin user's attribution when one or more
    Jellyfin sessions are playing this stream, else ``None``. Multiple
    matches (multi-viewer scenarios — same channel on multiple Jellyfin
    clients) tie-break on most-recent ``last_activity_date`` exactly as
    before bd-r5f0c.9.

    Never raises. Defensive failures return ``None``.
    """
    users = await resolve_jellyfin_users(
        ecm_session_ip,
        ecm_stream_name,
        ecm_channel_name=ecm_channel_name,
        ecm_channel_number=ecm_channel_number,
    )
    return users[0] if users else None


# ---------------------------------------------------------------------------
# Internals: server IP resolution
# ---------------------------------------------------------------------------


def _resolve_jellyfin_server_ip(base_url: str) -> str | None:
    """Extract the Jellyfin server's IP from the configured base URL.

    For IP-literal URLs (``http://192.168.1.10:8096``) the hostname
    component IS the IP — return it directly. For hostname URLs
    (``https://jf.local:8920``) resolve via :func:`socket.gethostbyname`
    and cache the result module-level so the next ~5-second poll cycle
    reuses it.

    Returns ``None`` on any failure (empty URL, malformed URL,
    unresolvable hostname). Each failure mode logs once at the
    appropriate level.
    """
    if not base_url:
        return None

    try:
        parsed = urlparse(base_url)
    except ValueError:
        logger.warning("[JELLYFIN] Could not parse jellyfin_base_url=%r", base_url)
        return None

    host = parsed.hostname
    if not host:
        logger.warning(
            "[JELLYFIN] jellyfin_base_url=%r has no extractable hostname", base_url,
        )
        return None

    if _looks_like_ip_literal(host):
        return host

    # Hostname case: consult the DNS cache before calling out.
    cached = _dns_cache.get(host)
    if cached is _UNRESOLVED:
        return None
    if isinstance(cached, str):
        return cached

    try:
        resolved = socket.gethostbyname(host)
    except socket.gaierror as exc:
        logger.warning(
            "[JELLYFIN] Could not resolve jellyfin_base_url hostname %r: %s. "
            "Jellyfin attribution will be disabled until ECM restarts and "
            "DNS resolves successfully.",
            host, exc,
        )
        _dns_cache[host] = _UNRESOLVED
        return None

    _dns_cache[host] = resolved
    logger.debug("[JELLYFIN] Resolved %s → %s (cached)", host, resolved)
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
    """NFC + lowercase + strip — mirrors emby_resolver's normalization."""
    return unicodedata.normalize("NFC", value).lower().strip()


def _find_matching_sessions(
    *,
    ecm_stream_name: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
    sessions: list[JellyfinSession],
) -> list[JellyfinSession]:
    """Return every Jellyfin session that matches across any of the three tiers.

    Tiered match (mirrors emby_resolver's three-tier strategy, but with
    Jellyfin-specific no-pipe-suffix tolerance):

    * **Tier 1** — channel name primary. Parse ``item_name`` as
      ``"<number> | <name>"``; the right-hand part (or the WHOLE string
      when there's no ``"|"`` separator — Jellyfin tolerance) must equal
      ``ecm_channel_name`` after normalization.
    * **Tier 2** — channel number exact (string compare). Skipped
      when either side is missing.
    * **Tier 3** — RapidFuzz ``token_set_ratio`` on ``ecm_stream_name``
      against ``item_name`` / ``channel_name`` with the 0.85 floor.
      Back-compat for movies / VOD.

    A session matches if ANY tier accepts it. The list is the union of
    tier hits — duplicates are de-duped by session identity so the same
    physical session is not double-counted in the tie-break.
    """
    # Pre-normalize Emby session strings ONCE — hot-path performance.
    prepared: list[tuple[JellyfinSession, str, str, str]] = []
    for session in sessions:
        normalized_item = _normalize(session.now_playing_item_name or "")
        normalized_channel = _normalize(session.now_playing_channel_name or "")
        # Right-hand side of "<number> | <name>" (or empty when no pipe).
        # For Jellyfin: if no pipe, we use the whole item name for Tier-1
        # whole-string equality (see below).
        suffix = _parse_pipe_suffix(normalized_item)
        prepared.append((session, normalized_item, normalized_channel, suffix))

    matched_ids: set[str] = set()
    matches: list[JellyfinSession] = []

    def _accept(session: JellyfinSession) -> None:
        if session.session_id in matched_ids:
            return
        matched_ids.add(session.session_id)
        matches.append(session)

    # ----- Tier 1: channel name primary match (Jellyfin-tolerant)
    normalized_ecm_channel = _normalize(ecm_channel_name or "")
    # bd-r5f0c.11: when an operator imports channels with the Emby/M3U
    # pipe-prefix display format leaked into the ECM channel_name itself
    # (e.g. "109 | CNN"), tier-1 must also parse the ECM side so the
    # right-hand suffix can be compared against the session forms.
    # Reuses _parse_pipe_suffix as-is — it returns "" when the input
    # has no pipe, so this is a no-op for clean ECM names.
    ecm_channel_suffix = _parse_pipe_suffix(normalized_ecm_channel)
    if normalized_ecm_channel:
        for session, normalized_item, _ch, suffix in prepared:
            # Sub-case A: pipe-suffix exists and matches (e.g. "408 | ESPN"
            # where suffix="espn" matches ecm_channel_name="ESPN").
            if suffix and suffix == normalized_ecm_channel:
                _accept(session)
                continue
            # Sub-case B: Jellyfin tolerance — no pipe, whole item_name is
            # the channel name (e.g. NowPlayingItem.Name="ESPN" directly).
            # Also catches installs that DO use the pipe prefix when the
            # whole string happens to equal the channel name.
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

    # ----- Tier 2: channel number exact (string compare)
    if ecm_channel_number is not None:
        ecm_number_str = str(ecm_channel_number).strip()
        if ecm_number_str:
            for session, _it, _ch, _sfx in prepared:
                session_number = session.channel_number
                if session_number is None:
                    continue
                if str(session_number).strip() == ecm_number_str:
                    _accept(session)

    # ----- Tier 3: legacy fuzzy fallback on stream_name
    normalized_stream = _normalize(ecm_stream_name or "")
    if normalized_stream:
        for session, normalized_item, normalized_channel, _sfx in prepared:
            if _fuzzy_or_exact_match(normalized_stream, normalized_item):
                _accept(session)
                continue
            if _fuzzy_or_exact_match(normalized_stream, normalized_channel):
                _accept(session)

    return matches


def _parse_pipe_suffix(normalized_item_name: str) -> str:
    """Return the right-hand side of a ``"<number> | <name>"`` string.

    When there's no pipe (VOD or Jellyfin-style bare channel name), return
    the empty string. The caller checks whole-string equality separately
    as the Jellyfin tolerance path (Tier-1, Sub-case B).

    Jellyfin-specific: unlike Emby (which consistently uses the pipe prefix
    for live-TV), Jellyfin's ``NowPlayingItem.Name`` is often just the channel
    name with no prefix. Returning ``""`` here (rather than the whole name)
    lets the caller's Tier-1 Sub-case B handle that scenario explicitly so
    the two paths remain distinct and readable.

    Input is assumed already normalized (NFC + lowercase + strip).
    """
    if "|" not in normalized_item_name:
        return ""
    _prefix, _sep, suffix = normalized_item_name.partition("|")
    return suffix.strip()


def _fuzzy_or_exact_match(normalized_stream: str, normalized_candidate: str) -> bool:
    """True iff the normalized stream and candidate exact-match OR fuzzy-score
    above ``FUZZY_MATCH_THRESHOLD``.

    Empty candidate (None coerced to "" upstream, or whitespace-only)
    cannot match.
    """
    if not normalized_candidate:
        return False
    if normalized_stream == normalized_candidate:
        return True
    score = fuzz.token_set_ratio(normalized_stream, normalized_candidate) / 100.0
    return score >= FUZZY_MATCH_THRESHOLD


def _tiebreak_most_recent(sessions: list[JellyfinSession]) -> JellyfinSession:
    """Pick the session with the most-recent ``last_activity_date``.

    Jellyfin timestamps are ISO 8601 strings, which sort lexicographically
    in the same order as chronologically. ``None`` always loses to any
    populated timestamp (mapped to "" for comparison).

    Returns the input verbatim when there is only one session.

    bd-r5f0c.9: retained as the one-winner helper for legacy paths
    that still want a single match.
    """
    if len(sessions) == 1:
        return sessions[0]
    return max(sessions, key=lambda s: s.last_activity_date or "")


def _sort_by_recency_descending(
    sessions: list[JellyfinSession],
) -> list[JellyfinSession]:
    """Return ``sessions`` sorted by ``last_activity_date`` descending.

    bd-r5f0c.9 multi-viewer attribution: the plural resolver returns
    every matched session so every viewer is captured, with position 0
    being the most-recent viewer. Same ``None``-loses semantic as
    :func:`_tiebreak_most_recent`.

    Stable across equal timestamps.
    """
    return sorted(
        sessions,
        key=lambda s: s.last_activity_date or "",
        reverse=True,
    )


def _log_jellyfin_resolver_no_match(
    *,
    client_ip: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
    ecm_stream_name: str,
    sessions: list[JellyfinSession],
) -> None:
    """Emit a structured WARN once per (client_ip, channel_name) per 60 s
    when the Jellyfin resolver had sessions to compare against and the
    IP short-circuit passed, but no tier produced a match.

    bd-r5f0c.10 forensic logging — mirrors the Emby resolver helper.
    See :func:`emby_resolver._log_emby_resolver_no_match` for the full
    rationale. JellyfinSession's field shape matches EmbySession (same
    upstream Sessions API ancestry), so the per-session payload is
    identical: raw + normalized item_name, parsed pipe suffix,
    channel_name (raw + normalized), channel_number, last_activity.

    Callers MUST guard on non-empty ``sessions`` — an empty session list
    is a normal state and would spam the log with useless lines.
    """
    rate_key = (client_ip, _normalize(ecm_channel_name or ""))
    now = time.monotonic()
    last = _jellyfin_resolver_no_match_last_warn_at.get(rate_key, 0.0)
    if now - last < _JELLYFIN_NO_MATCH_WARN_INTERVAL:
        return
    _jellyfin_resolver_no_match_last_warn_at[rate_key] = now

    truncated = sessions[:_JELLYFIN_NO_MATCH_MAX_SESSIONS]
    session_payload = [
        {
            "session_id": (s.session_id[:8] if s.session_id else None),
            "item_name": s.now_playing_item_name,
            "item_name_norm": _normalize(s.now_playing_item_name or ""),
            "item_name_pipe_suffix": _parse_pipe_suffix(
                _normalize(s.now_playing_item_name or "")
            ),
            "channel_name": s.now_playing_channel_name,
            "channel_name_norm": _normalize(s.now_playing_channel_name or ""),
            "channel_number": s.channel_number,
            "last_activity": s.last_activity_date,
        }
        for s in truncated
    ]
    logger.warning(
        "[JELLYFIN-RESOLVER] no-match diagnostic: ip=%s ecm_channel=%r "
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


def _maybe_warn_disambiguation(
    candidate_count: int,
    ip: str,
    channel_label: str,
    picked_user: str,
) -> None:
    """Emit a WARN-rate-limited log when the resolver had to disambiguate.

    Disambiguation is rare but worth surfacing for operators debugging
    attribution. However, if the same channel is persistently watched by
    multiple clients, emitting a WARN per poll cycle (every 5s) would flood
    the log. Rate-limit to at most one WARN per ``_JELLYFIN_RESOLVER_WARN_INTERVAL``
    seconds.
    """
    global _jellyfin_resolver_last_warn_at
    now = time.monotonic()
    if (
        _jellyfin_resolver_last_warn_at is None
        or now - _jellyfin_resolver_last_warn_at >= _JELLYFIN_RESOLVER_WARN_INTERVAL
    ):
        logger.warning(
            "[JELLYFIN] resolver: %d candidates for ip=%s channel=%s; "
            "picked %s by recency. Multiple Jellyfin clients on same channel.",
            candidate_count, ip, channel_label, picked_user,
        )
        _jellyfin_resolver_last_warn_at = now
