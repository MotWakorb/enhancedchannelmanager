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


def _reset_for_tests() -> None:
    """Clear the DNS cache and warn timestamp — tests only.

    Tests share the same module instance across test functions; without
    this reset, a hostname-resolution result from one test would leak
    into the next and produce false matches.
    """
    global _jellyfin_resolver_last_warn_at
    _dns_cache.clear()
    _jellyfin_resolver_last_warn_at = None


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


async def resolve_jellyfin_user(
    ecm_session_ip: str,
    ecm_stream_name: str,
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> JellyfinAttribution | None:
    """Cross-reference one ECM stream against the live Jellyfin session list.

    Returns the matching Jellyfin user's attribution when exactly one
    Jellyfin session is playing this stream, else ``None``. Multiple
    matches (rare — same channel on multiple Jellyfin clients) tie-break on
    most-recent ``last_activity_date``.

    Matching is tiered:

    1. **Tier 1 — channel_name.** Parse each Jellyfin session's
       ``now_playing_item_name`` as ``"<number> | <name>"``; compare the
       right-hand part (or the whole string if no pipe) case-insensitively
       to ``ecm_channel_name``. Jellyfin often omits the pipe prefix so
       the whole-string comparison is the primary Jellyfin live-TV path.
    2. **Tier 2 — channel_number exact.** When both sides present, string
       compare. Defensive against name divergence.
    3. **Tier 3 — fuzzy stream_name fallback.** Legacy path for VOD /
       movies where no channel args are available.

    Args:
        ecm_session_ip: The client IP of the Dispatcharr stream session
            ECM is trying to attribute.
        ecm_stream_name: The Dispatcharr stream name — matched in tier-3.
            Required for back-compat.
        ecm_channel_name: ECM channel name (e.g. "ESPN"). When populated,
            the resolver tries tier 1 first.
        ecm_channel_number: ECM channel number. When populated, the
            resolver tries tier 2. Accepts ``int`` or ``str``.

    Returns:
        ``JellyfinAttribution`` with the resolved user_id + user_name, or
        ``None`` when the IP does not match the Jellyfin server, no
        Jellyfin session matches across any tier, or any defensive failure
        (DNS resolution failure, malformed base URL).

    Notes:
        Never raises. The BandwidthTracker poll loop calls this on every
        active session every ~5 seconds; raising would kill the loop or
        force the caller to wrap every call.
    """
    settings = get_settings()
    base_url = getattr(settings, "jellyfin_base_url", "") or ""

    jellyfin_server_ip = _resolve_jellyfin_server_ip(base_url)
    if jellyfin_server_ip is None:
        # Could not determine the Jellyfin server IP (empty/malformed URL or
        # DNS failure). Fail safe — no attribution possible.
        return None

    if ecm_session_ip != jellyfin_server_ip:
        # Hot path: the vast majority of ECM sessions are NOT
        # Jellyfin-mediated. Short-circuit before the cache call.
        return None

    sessions = await get_cached_jellyfin_sessions()
    if not sessions:
        return None

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
        return None

    winner = _tiebreak_most_recent(matches)
    if len(matches) > 1:
        # Rate-limited WARN for disambiguation so operators can see the
        # tie-break in logs without flooding. DEBUG on every call.
        _maybe_warn_disambiguation(
            len(matches), ecm_session_ip,
            ecm_channel_name or ecm_stream_name,
            winner.user_name,
        )
        logger.debug(
            "[JELLYFIN] resolver: %d candidates for ip=%s name=%s, "
            "picked %s by recency",
            len(matches), ecm_session_ip,
            ecm_channel_name or ecm_stream_name,
            winner.user_name,
        )
    else:
        logger.debug(
            "[JELLYFIN] Resolved stream=%r → user=%s (uid=%s) from 1 match",
            ecm_stream_name, winner.user_name, winner.user_id,
        )
    return JellyfinAttribution(user_id=winner.user_id, user_name=winner.user_name)


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
    """
    if len(sessions) == 1:
        return sessions[0]
    return max(sessions, key=lambda s: s.last_activity_date or "")


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
