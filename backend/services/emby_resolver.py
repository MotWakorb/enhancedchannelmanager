"""Emby cross-ref resolver (bd-6802c, epic bd-2cenq).

Given a single ECM stream session (client IP + stream name), this
module answers one question: which Emby user — if any — is the actual
viewer? When operators watch ECM-mediated streams through Emby,
ECM only sees the Emby server's IP (every Emby viewer's pull comes
from the same proxy IP), so without this cross-reference Stats
collapses every Emby viewer into a single 'Emby server' identity.

Data flow (cross-references the cache, NEVER calls EmbyClient directly):

    BandwidthTracker (bd-gih6d, future) → resolve_emby_user(ip, name)
       → get_cached_emby_sessions()                 [bd-gpeot]
            → cache hit OR EmbyClient.get_sessions  [bd-6c0g6]
       → match stream_name against now-playing
       → return EmbyAttribution(user_id, user_name) | None

Match algorithm:

1. Extract the Emby server's IP from ``settings.emby_base_url``.
   For IP-literal URLs (``http://192.168.1.10:8096``) we compare
   directly. For hostname URLs (``https://emby.local:8920``) we resolve
   via :func:`socket.gethostbyname` ONCE per process and cache the
   result — repeated polls (every ~5s) must not thrash DNS.
2. If the ECM session's client IP does NOT equal the Emby server IP,
   short-circuit with ``None`` BEFORE calling the cache. Every poll
   cycle visits this branch for every non-Emby-mediated session, so the
   short-circuit is the load-bearing optimization.
3. Fetch cached Emby sessions. The cache transparently handles the
   "Emby disabled" case (returns ``[]``) so the resolver does not need
   to inspect ``settings.emby_enabled``.
4. For each session, score
   ``now_playing_item_name`` AND ``now_playing_channel_name`` (either
   may be the live-TV channel surface depending on the playback type):
   * exact (NFC + lowercase + strip) match wins immediately;
   * else RapidFuzz ``token_set_ratio / 100`` against the 0.85 floor
     from the bead spec.
5. Multiple matches (rare — same channel on multiple Emby clients):
   pick the most-recent ``last_activity_date``. Emby's ISO timestamps
   are lexicographically comparable so plain string compare works;
   ``None`` always loses to any populated timestamp.

Design constraints:

* **Never instantiate EmbyClient directly.** The cache owns the upstream
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
"""
from __future__ import annotations

import logging
import socket
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from config import get_settings
from emby_client import EmbySession
from services.emby_cache import get_cached_emby_sessions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


# Fuzzy match threshold per the bead spec. Operators do NOT tune this —
# Emby's now-playing names are typically clean enough that 0.85 gives
# very few false positives, and a per-deployment knob here would only
# encourage attribution drift in Stats. If experience proves it
# needs tuning, that's a follow-up bead, not a settings field.
FUZZY_MATCH_THRESHOLD: float = 0.85


# Sentinel for "we tried to resolve this hostname and it failed". Cached
# in ``_dns_cache`` so the WARN is logged once and subsequent polls
# return ``None`` cheaply without re-attempting the DNS lookup (which
# would just fail again at the same DNS cost).
_UNRESOLVED = object()


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------


# Map of hostname → resolved IP (or ``_UNRESOLVED`` sentinel). The
# resolver consults this BEFORE calling ``socket.gethostbyname`` so we
# never resolve the same hostname twice in one process lifetime. IP
# literals bypass this cache entirely.
_dns_cache: dict[str, str | object] = {}


def _reset_for_tests() -> None:
    """Clear the DNS cache — tests only.

    Tests share the same module instance across test functions; without
    this reset, a hostname-resolution result from one test would leak
    into the next and produce false matches (e.g. a test that mocks
    ``gethostbyname`` to return one IP would still see that IP in a
    later test that expects to fail). Production code paths do not call
    this.
    """
    _dns_cache.clear()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbyAttribution:
    """The resolved Emby identity for one ECM stream session.

    Frozen so callers cannot mutate the attribution between the resolver
    returning it and the BandwidthTracker writing it to
    ``session_telemetry.emby_user_id`` / ``emby_user_name``.

    Attributes:
        user_id: Emby user UUID (the ``UserId`` field from the live
            session payload). Persisted to
            ``session_telemetry.emby_user_id``.
        user_name: Human-readable Emby username. Persisted to
            ``session_telemetry.emby_user_name`` and surfaced in Stats >
            User Watch Time.
    """

    user_id: str
    user_name: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_emby_user(
    ecm_session_ip: str,
    ecm_stream_name: str,
) -> EmbyAttribution | None:
    """Cross-reference one ECM stream against the live Emby session list.

    Returns the matching Emby user's attribution when exactly one Emby
    session is playing this stream, else ``None``. Multiple matches
    (rare — same channel on multiple Emby clients) tie-break on
    most-recent ``last_activity_date``.

    Args:
        ecm_session_ip: The client IP of the Dispatcharr stream session
            ECM is trying to attribute. For Emby-mediated streams this
            will be the Emby server's IP; for everything else it will
            not, and the resolver short-circuits.
        ecm_stream_name: The Dispatcharr stream name (e.g. "CNN HD") —
            matched case-insensitively against each Emby session's
            ``now_playing_item_name`` and ``now_playing_channel_name``.

    Returns:
        ``EmbyAttribution`` with the resolved user_id + user_name, or
        ``None`` when the IP does not match the Emby server, no Emby
        session matches the stream name, or any defensive failure
        (DNS resolution failure, malformed base URL).

    Notes:
        Never raises. The BandwidthTracker poll loop calls this on every
        active session every ~5 seconds; raising would either kill the
        loop or force the caller to wrap every call. Defensive failures
        (bad URL, DNS failure) return ``None`` after logging.
    """
    settings = get_settings()
    base_url = getattr(settings, "emby_base_url", "") or ""

    emby_server_ip = _resolve_emby_server_ip(base_url)
    if emby_server_ip is None:
        # Could not determine the Emby server IP (empty/malformed URL or
        # DNS failure). Fail safe — no attribution possible.
        return None

    if ecm_session_ip != emby_server_ip:
        # Hot path: the vast majority of ECM sessions are NOT
        # Emby-mediated. Short-circuit before the cache call.
        return None

    sessions = await get_cached_emby_sessions()
    if not sessions:
        # Cache empty (Emby disabled, idle server, or fetch failure with
        # no prior cache). Nothing to match.
        return None

    matches = _find_matching_sessions(ecm_stream_name, sessions)
    if not matches:
        logger.debug(
            "[EMBY] No match for stream=%r against %d Emby session(s)",
            ecm_stream_name, len(sessions),
        )
        return None

    winner = _tiebreak_most_recent(matches)
    logger.debug(
        "[EMBY] Resolved stream=%r → user=%s (uid=%s) from %d match(es)",
        ecm_stream_name, winner.user_name, winner.user_id, len(matches),
    )
    return EmbyAttribution(user_id=winner.user_id, user_name=winner.user_name)


# ---------------------------------------------------------------------------
# Internals: server IP resolution
# ---------------------------------------------------------------------------


def _resolve_emby_server_ip(base_url: str) -> str | None:
    """Extract the Emby server's IP from the configured base URL.

    For IP-literal URLs (``http://192.168.1.10:8096``) the hostname
    component IS the IP — return it directly. For hostname URLs
    (``https://emby.local:8920``) resolve via :func:`socket.gethostbyname`
    and cache the result module-level so the next ~5-second poll cycle
    reuses it.

    Returns ``None`` on any failure (empty URL, malformed URL,
    unresolvable hostname). Each failure mode logs once at the
    appropriate level — operators need to know about DNS failures, but
    not on every poll cycle.
    """
    if not base_url:
        # Empty base_url indicates Emby is unconfigured. The cache layer
        # would also return [] in this state; we just exit early.
        return None

    try:
        parsed = urlparse(base_url)
    except ValueError:
        # Malformed URL — log once at WARN; an unreachable WARN cadence
        # would be alarming and the operator can only fix this once.
        logger.warning("[EMBY] Could not parse emby_base_url=%r", base_url)
        return None

    host = parsed.hostname
    if not host:
        logger.warning(
            "[EMBY] emby_base_url=%r has no extractable hostname", base_url,
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
            "[EMBY] Could not resolve emby_base_url hostname %r: %s. "
            "Emby attribution will be disabled until ECM restarts and "
            "DNS resolves successfully.",
            host, exc,
        )
        _dns_cache[host] = _UNRESOLVED
        return None

    _dns_cache[host] = resolved
    logger.debug("[EMBY] Resolved %s → %s (cached)", host, resolved)
    return resolved


def _looks_like_ip_literal(host: str) -> bool:
    """True when ``host`` is an IPv4/IPv6 literal, not a hostname.

    Use :func:`socket.inet_pton` for both families — it returns a packed
    address on success and raises ``OSError`` otherwise. This is more
    reliable than parsing dots because ``socket.inet_pton`` rejects
    malformed IPs that a naive regex would accept.
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
    """NFC + lowercase + strip — matches dedup_matcher's normalization.

    Centralised here (rather than importing from dedup_matcher) because
    the two services answer different questions: dedup_matcher is about
    "is this stream the same as an existing channel?" with a tunable
    threshold and a hard confidence floor; the Emby resolver is about
    "which Emby session is playing this stream?" with a fixed 0.85
    threshold. Sharing the normalize helper across them would couple
    two policies that may evolve independently — duplicating the
    one-liner keeps the contract local.
    """
    return unicodedata.normalize("NFC", value).lower().strip()


def _find_matching_sessions(
    ecm_stream_name: str,
    sessions: list[EmbySession],
) -> list[EmbySession]:
    """Return every session whose now-playing name matches the stream.

    Match contract: for each session, compare ``ecm_stream_name``
    against BOTH ``now_playing_item_name`` and
    ``now_playing_channel_name`` (live-TV sessions populate the channel
    name, VOD sessions populate only the item name, idle sessions
    populate neither). A session matches if EITHER candidate name
    passes the exact-or-fuzzy check; ``None`` candidate names are
    skipped, not scored.

    Returns the unfiltered list of matching sessions so the
    multi-match tiebreaker can operate on the full set.
    """
    normalized_stream = _normalize(ecm_stream_name)
    if not normalized_stream:
        return []

    matches: list[EmbySession] = []
    for session in sessions:
        if _session_matches(normalized_stream, session):
            matches.append(session)
    return matches


def _session_matches(normalized_stream: str, session: EmbySession) -> bool:
    """True iff this Emby session's now-playing name matches the stream.

    Tries item_name first, then channel_name. An exact (normalized)
    match on either short-circuits to True; otherwise we run
    :func:`rapidfuzz.fuzz.token_set_ratio` and return True iff the
    score normalized to [0, 1] meets ``FUZZY_MATCH_THRESHOLD``.
    """
    for candidate_name in (session.now_playing_item_name, session.now_playing_channel_name):
        if candidate_name is None:
            continue
        normalized_candidate = _normalize(candidate_name)
        if not normalized_candidate:
            continue
        if normalized_stream == normalized_candidate:
            return True
        # token_set_ratio returns 0–100; normalize to 0–1 to match the
        # threshold semantics in the bead spec.
        score = fuzz.token_set_ratio(normalized_stream, normalized_candidate) / 100.0
        if score >= FUZZY_MATCH_THRESHOLD:
            return True
    return False


def _tiebreak_most_recent(sessions: list[EmbySession]) -> EmbySession:
    """Pick the session with the most-recent ``last_activity_date``.

    Emby timestamps are ISO 8601 strings, which sort lexicographically
    in the same order as chronologically. A session with
    ``last_activity_date is None`` should never beat one with a real
    timestamp — we map ``None`` to the empty string for comparison so
    populated values always sort higher.

    Returns the input verbatim when there is only one session, so the
    caller does not need to special-case the common one-match path.
    """
    if len(sessions) == 1:
        return sessions[0]
    return max(sessions, key=lambda s: s.last_activity_date or "")
