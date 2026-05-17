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
import observability

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
    ecm_channel_name: str | None = None,
    ecm_channel_number: str | int | None = None,
) -> EmbyAttribution | None:
    """Cross-reference one ECM stream against the live Emby session list.

    Returns the matching Emby user's attribution when exactly one Emby
    session is playing this stream, else ``None``. Multiple matches
    (rare — same channel on multiple Emby clients) tie-break on
    most-recent ``last_activity_date``.

    Matching is tiered (bd-zldrq fix-forward for v0.17.1-0033 — live
    test surfaced that Dispatcharr stream names like ``"US: ESPN FHD"``
    do not fuzzy-match Emby live-TV item names like ``"408 | ESPN"`` at
    the 0.85 floor, because the only token in common is "ESPN"):

    1. **Tier 1 — channel_name exact.** Parse each Emby session's
       ``now_playing_item_name`` as ``"<number> | <name>"`` and compare
       the right-hand part case-insensitively to ``ecm_channel_name``.
       Whole-string equal also counts (some Emby installs surface live
       TV without the ``"<number> | "`` prefix).
    2. **Tier 2 — channel_number exact.** When both ``ecm_channel_number``
       and the session's ``channel_number`` are present, string-compare
       (cast ECM input to str). Defensive against name divergence
       between ECM and Emby.
    3. **Tier 3 — fuzzy stream_name fallback.** The legacy path: score
       ``ecm_stream_name`` against ``now_playing_item_name`` and
       ``now_playing_channel_name`` via RapidFuzz ``token_set_ratio /
       100`` against ``FUZZY_MATCH_THRESHOLD``. Still the right behavior
       for non-live-TV Emby content (movies, episodes).

    Matches across tiers are pooled into one disambiguation set; the
    most-recent ``last_activity_date`` wins. When the candidate set has
    N > 1 entries, a ``[EMBY] resolver:`` DEBUG line names the count,
    ip, name, and picked user so operators can see the disambiguation
    in trace.

    Args:
        ecm_session_ip: The client IP of the Dispatcharr stream session
            ECM is trying to attribute. For Emby-mediated streams this
            will be the Emby server's IP; for everything else it will
            not, and the resolver short-circuits.
        ecm_stream_name: The Dispatcharr stream name (e.g. "CNN HD") —
            matched case-insensitively against each Emby session's
            ``now_playing_item_name`` and ``now_playing_channel_name``
            in the tier-3 fallback. Required for back-compat (the
            pre-bd-zldrq signature was ``(ip, stream_name)``).
        ecm_channel_name: ECM channel name (e.g. "ESPN"). When
            populated, the resolver tries tier 1 first. Optional so
            non-live-TV callers do not need to pass it.
        ecm_channel_number: ECM channel number (e.g. ``408`` or
            ``"408"``). When populated, the resolver tries tier 2.
            Optional — accepts ``int`` or ``str`` and casts to ``str``
            for compare.

    Returns:
        ``EmbyAttribution`` with the resolved user_id + user_name, or
        ``None`` when the IP does not match the Emby server, no Emby
        session matches across any tier, or any defensive failure
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

    matches = _find_matching_sessions(
        ecm_stream_name=ecm_stream_name,
        ecm_channel_name=ecm_channel_name,
        ecm_channel_number=ecm_channel_number,
        sessions=sessions,
    )
    if not matches:
        logger.debug(
            "[EMBY] No match for stream=%r channel=%r number=%r "
            "against %d Emby session(s)",
            ecm_stream_name, ecm_channel_name, ecm_channel_number,
            len(sessions),
        )
        observability.get_metric("user_attribution_unresolved_total").labels(source="emby").inc()
        return None

    winner = _tiebreak_most_recent(matches)
    if len(matches) > 1:
        # Surface disambiguation so operators can see the tie-break in
        # trace. Hot path — keep at DEBUG.
        logger.debug(
            "[EMBY] resolver: %d candidates for ip=%s name=%s, "
            "picked %s by recency",
            len(matches), ecm_session_ip,
            ecm_channel_name or ecm_stream_name,
            winner.user_name,
        )
    else:
        logger.debug(
            "[EMBY] Resolved stream=%r → user=%s (uid=%s) from 1 match",
            ecm_stream_name, winner.user_name, winner.user_id,
        )
    observability.get_metric("user_attribution_resolved_total").labels(source="emby").inc()
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
    *,
    ecm_stream_name: str,
    ecm_channel_name: str | None,
    ecm_channel_number: str | int | None,
    sessions: list[EmbySession],
) -> list[EmbySession]:
    """Return every Emby session that matches across any of the three tiers.

    bd-zldrq tiered match (live-TV fix for v0.17.1-0033):

    * **Tier 1** — channel name primary. Parse ``item_name`` as
      ``"<number> | <name>"``; the right-hand part (or the whole
      string when there's no ``"|"`` separator) must equal
      ``ecm_channel_name`` after normalization.
    * **Tier 2** — channel number exact (string compare). Skipped
      when either side is missing.
    * **Tier 3** — RapidFuzz ``token_set_ratio`` on
      ``ecm_stream_name`` against ``item_name`` / ``channel_name``
      with the bead-spec 0.85 floor. Back-compat for movies / VOD
      where neither channel argument is available.

    A session matches if ANY tier accepts it. The list is the union
    of tier hits — duplicates are de-duped by session identity so the
    same physical session is not double-counted in the tie-break.

    Hot-path discipline (bandwidth tracker calls this every ~5s per
    channel on every poll, with up to ~30 Emby sessions per call): the
    normalized Emby item names and channel names are computed ONCE up
    front into a list of (session, normalized_item, normalized_channel,
    normalized_channel_suffix) tuples so the inner loops avoid
    re-running NFC + lower + strip per tier per session. Without this,
    a busy poll with N channels × M sessions × 3 tiers would
    re-normalize the same Emby strings 3×N×M times.
    """
    # Pre-normalize Emby session strings ONCE — see hot-path note above.
    prepared: list[tuple[EmbySession, str, str, str]] = []
    for session in sessions:
        normalized_item = _normalize(session.now_playing_item_name or "")
        normalized_channel = _normalize(session.now_playing_channel_name or "")
        # The right-hand side of "<number> | <name>" (or empty when
        # there's no pipe). Tier 1 compares this to ecm_channel_name.
        suffix = _parse_pipe_suffix(normalized_item)
        prepared.append((session, normalized_item, normalized_channel, suffix))

    # Track matched sessions by identity to avoid double-counting when
    # two tiers both accept the same physical session.
    matched_ids: set[str] = set()
    matches: list[EmbySession] = []

    def _accept(session: EmbySession) -> None:
        if session.session_id in matched_ids:
            return
        matched_ids.add(session.session_id)
        matches.append(session)

    # ----- Tier 1: channel name primary match
    normalized_ecm_channel = _normalize(ecm_channel_name or "")
    if normalized_ecm_channel:
        for session, normalized_item, _ch, suffix in prepared:
            # Right-hand side of "<number> | <name>" matches (the
            # primary live-TV path), OR the whole item_name matches
            # (some Emby installs have no "<number> | " prefix).
            if suffix and suffix == normalized_ecm_channel:
                _accept(session)
                continue
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

    Emby renders live-TV ``NowPlayingItem.Name`` as e.g. ``"408 | ESPN"``;
    the operator-visible channel name is the part after the pipe. When
    there's no pipe (VOD / movies / episodes), return the empty string
    so tier-1 cannot accidentally match the whole item_name through
    this path (the caller still considers whole-string equality
    separately).

    Input is assumed already normalized (NFC + lowercase + strip) — the
    caller pre-normalizes for the hot-path performance reason
    documented in :func:`_find_matching_sessions`.
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
    # token_set_ratio returns 0–100; normalize to 0–1 to match the
    # threshold semantics in the bead spec.
    score = fuzz.token_set_ratio(normalized_stream, normalized_candidate) / 100.0
    return score >= FUZZY_MATCH_THRESHOLD


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
