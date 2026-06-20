"""4-tier stream matcher for DBAS Phase-2 channel restore (PURE function).

This module implements the stream-matching fallback ladder that the channels
importer (bead ``enhancedchannelmanager-0i2vt.14``, integration umbrella) runs
when re-attaching an archived channel's embedded streams to the streams that
already exist on the *destination* Dispatcharr instance.

Bead: ``enhancedchannelmanager-al6e3`` (split 3/4 of ``0i2vt.14``).

WHY a pure function. The matcher is the hardest, most correctness-sensitive
piece of the channels importer, and the epic success signal is a ">99% match
rate" assertion over a production-shaped seed snapshot (bead
``enhancedchannelmanager-zqtjj``). To unit-test the matching *logic* exhaustively
without a live Dispatcharr instance, the matcher does ZERO I/O: the caller
fetches the destination instance's streams (``get_streams``) and passes them in
as ``candidates``. Same inputs → same output, deterministic, no global state, no
network, no DB. The live ">99%" signal is a SEPARATE integration test that
depends on the seeded test instance (``zqtjj``) — see the module-level NOTE.

----------------------------------------------------------------------------
THE 4-TIER LADDER (strongest signal first; first tier that hits wins)
----------------------------------------------------------------------------

A Dispatcharr stream's *identity* is its upstream URL — a stream literally IS a
playable URL with a display name, ingested from an M3U provider
(``m3u_account``). This is the key difference from the ADR-008 *channel* dedup
matcher (``backend/services/dedup_matcher.py``), which is name-first because a
*channel* is a human-named container. A *stream* is URL-first. The ladder is
therefore ordered by how strongly each signal proves "this destination stream
is the same upstream stream the archive recorded":

* **Tier 1 — EXACT URL.** The archived stream's ``url`` equals a destination
  stream's ``url`` (case-sensitive; a URL path/query is case-significant).
  This is the canonical stream identity — the same provider serving the same
  endpoint. Strongest possible signal; never a false positive.

* **Tier 2 — EXACT NAME + SAME PROVIDER.** Same display name (after the shared
  conservative normalization) AND same ``m3u_account`` provider id. Covers the
  common case where a provider rotated its stream URLs (token refresh, CDN
  hostname change) but kept the channel line-up stable: the URL no longer
  matches (Tier 1 missed) but "same name from the same provider" is a high-
  confidence same-stream signal.

* **Tier 3 — EXACT NORMALIZED NAME (any provider).** Same normalized display
  name, regardless of provider. Covers a cross-provider migration (the operator
  restored onto an instance whose M3U accounts are different ids / different
  providers, but carry an equivalently-named stream). Looser than Tier 2 — the
  provider is not pinned — so it sits below it.

* **Tier 4 — FUZZY NORMALIZED NAME.** ``token_set_ratio`` of the normalized
  names ≥ :data:`STREAM_FUZZY_FLOOR`. Last resort before giving up: catches
  minor name drift (quality-tag reorder, punctuation, ``HD`` vs ``FHD``) that
  exact-normalized comparison misses. Reuses the exact RapidFuzz scorer and the
  ReDoS-hardened LOCALS normalizer the ADR-008 dedup matcher already ships, so
  the two matchers cannot drift on normalization.

* **MISS (Tier 0).** No tier hit. The matcher returns ``(MatchTier.MISS, None)``.
  The caller's custom-stream fallback (bead ``enhancedchannelmanager-ahygg``)
  takes over: it synthesizes a custom-stream M3U account for the orphan and
  logs a WARN so operators see when the heuristic fires. That synthesis is NOT
  this bead — this bead only *reports* the miss.

SOURCING NOTE (read the report / bead comment for the full provenance trail):
the repo has **no pre-written 4-tier *stream* ladder** to copy — ``0i2vt.14``
refers to "sub-spike output" that was never committed as a doc or bead, and the
DBAS threat model / restore-contracts docs are silent on matching mechanics.
This ladder is therefore *derived*, not invented blindly: every primitive it
uses (the conservative + LOCALS normalizers, the ``token_set_ratio`` scorer,
the lowest-id tie-break, the ReDoS posture) is reused verbatim from the shipped,
reviewed ADR-008 dedup matcher. The tier *ordering* (URL > name+provider >
name > fuzzy > miss) is the defensible choice for stream identity and is flagged
as a confirm-me item for the code reviewer / PO in the bead report.

----------------------------------------------------------------------------
DETERMINISM CONTRACT
----------------------------------------------------------------------------

* PURE: no I/O, no DB, no network, no global mutable state.
* NO INPUT MUTATION: neither ``stream`` nor any ``candidates`` element is
  modified; the function only reads.
* DETERMINISTIC TIE-BREAK: within the winning tier, if several candidates tie,
  the candidate with the **lowest integer ``id``** wins. Mirrors the ADR-008
  dedup matcher's lowest-id rule (there the id is a UUID string; here a
  Dispatcharr stream id is an int, so it is a numeric min). Same inputs always
  produce the same ``(tier, id)`` regardless of candidate list order.

Conventions (``docs/style_guide.md``): ``int, Enum`` with self-describing
values; ``snake_case``; Google-style docstrings; lazy ``%`` logging; no bare
``re.*`` on dynamically-built patterns (this module builds no regex — it reuses
the dedup matcher's pre-compiled, ``nosemgrep``-annotated patterns).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from enum import IntEnum

# Reuse the shipped ADR-008 matcher primitives rather than re-implementing them
# (engineering-discipline: reuse before creating). ``_normalize`` is the ONE
# shared, ReDoS-hardened name cleaner; ``NameCleanMode`` selects its
# aggressiveness; ``fuzz.token_set_ratio`` is the exact scorer the dedup path
# uses. Importing these guarantees the stream matcher and the channel dedup
# matcher cannot drift on normalization or scoring.
from rapidfuzz import fuzz

from services.dedup_matcher import NameCleanMode, _normalize

logger = logging.getLogger(__name__)


class MatchTier(IntEnum):
    """Which rung of the 4-tier ladder produced a match (or MISS).

    Self-describing values usable directly as the public ``tier`` return: a
    caller / log line / test reads ``MatchTier.EXACT_URL`` without needing extra
    context. Ordered by signal strength — the ``IntEnum`` integer value IS the
    tier number the bead's contract speaks of (``match_stream -> (tier:int, …)``)
    so ``int(MatchTier.EXACT_URL) == 1``. ``MISS`` is ``0``: "no tier hit", a
    falsy sentinel that reads naturally as "below tier 1".
    """

    MISS = 0
    EXACT_URL = 1
    EXACT_NAME_SAME_PROVIDER = 2
    EXACT_NORMALIZED_NAME = 3
    FUZZY_NORMALIZED_NAME = 4


# Minimum ``token_set_ratio`` (normalized to [0.0, 1.0]) a Tier-4 fuzzy pair
# must reach to be admitted as a match. Set to the same value as the ADR-008
# dedup ``CONFIDENCE_FLOOR`` (0.60) deliberately: it is the project's vetted,
# defense-in-depth floor for "this fuzzy name pair is the same thing", so the
# stream matcher does not introduce a second, divergent fuzzy threshold. A
# stream restore is operator-trusted input (ADR-012 D11) and a Tier-4 miss is
# *safe* — it falls through to the custom-stream account, not to a wrong
# attachment — so the floor need not be stricter than the dedup floor.
#
# Defined locally (not imported from ``confidence_constants``) so the two floors
# can diverge later by an explicit edit + test change rather than silently
# coupling the stream-restore policy to the live-dedup policy. If they should be
# unified, that is an ADR addendum, not a hidden import.
STREAM_FUZZY_FLOOR: float = 0.60


def _stream_id(candidate: Mapping) -> int | None:
    """Return a candidate stream's integer ``id``, or ``None`` if unusable.

    A candidate with no ``id`` (or a non-int id) cannot be returned as a match
    target — the caller needs a concrete destination id to attach — so such a
    candidate is silently skipped by every tier. Defensive against a malformed
    archive/candidate; never raises.
    """
    raw = candidate.get("id")
    if isinstance(raw, bool):  # bool is an int subclass — reject explicitly.
        return None
    if isinstance(raw, int):
        return raw
    return None


def _normalized_name(record: Mapping) -> str:
    """Conservatively-normalized display name of a stream record.

    Reuses the ADR-008 ``_normalize`` (CONSERVATIVE mode: NFC → strip a leading
    ``N | `` channel-number prefix → lowercase → strip) so exact-name tiers
    compare on the same canonical form the rest of the system uses. Returns
    ``""`` for a missing/blank name (an un-matchable empty string).
    """
    name = record.get("name")
    if not isinstance(name, str) or not name:
        return ""
    return _normalize(name, mode=NameCleanMode.CONSERVATIVE)


def _provider_id(record: Mapping) -> object:
    """Return a stream record's provider (``m3u_account``) id, or ``None``.

    Used only for the Tier-2 same-provider equality. Returned as-is (int id on
    real data) so two records match iff their providers are equal AND present;
    a ``None`` provider never equals another ``None`` for matching purposes —
    the Tier-2 check guards against that explicitly.
    """
    return record.get("m3u_account")


def match_stream(
    stream: Mapping,
    candidates: Sequence[Mapping],
    *,
    allow_fuzzy: bool = True,
) -> tuple[int, int | None]:
    """Match one archived ``stream`` against destination ``candidates``.

    PURE. Runs the 4-tier ladder (see the module docstring) and returns the
    first tier that hits, with the destination stream id it matched. Performs
    no I/O — ``candidates`` are the destination instance's existing streams,
    already fetched by the caller.

    Args:
        stream: The archived stream record from the backup artifact. Read for
            ``url`` (Tier 1), ``name`` (Tiers 2–4), and ``m3u_account``
            (Tier 2). A Mapping (dict) — never mutated.
        candidates: The destination instance's existing streams, each a Mapping
            carrying at least ``id`` plus the same fields. Never mutated. May be
            empty (→ MISS). Order does not affect the result (deterministic
            tie-break).
        allow_fuzzy: Whether Tier-4 fuzzy (``token_set_ratio``) matching is
            permitted. ``True`` (default) keeps the full 4-tier ladder — the
            DBAS one-shot archive restore behaviour. ``False`` FLOORS the ladder
            at Tier-3 exact-normalized: a Tier-4 fuzzy candidate is NOT admitted
            and the call returns ``MISS`` instead. The cross-instance sync path
            (epic ``i39wu``, spike ``xp6mp`` ruling 1b) passes ``allow_fuzzy``
            from the per-``SyncTarget`` ``fuzzy_stream_matching`` flag (default
            off) so a continuous sync does not let a fuzzy name guess silently
            shadow a real stream on every cycle.

    Returns:
        ``(tier, match_id)`` where ``tier`` is the :class:`MatchTier` integer of
        the winning rung (``1``–``4``) and ``match_id`` is the destination
        stream id, OR ``(MatchTier.MISS, None)`` == ``(0, None)`` when no tier
        hits (empty candidates, or every tier missed). ``MatchTier`` is an
        ``IntEnum`` so the returned value satisfies the ``tier: int`` contract
        directly.

    Tie-break:
        Within the winning tier, the candidate with the lowest integer ``id``
        wins. Deterministic regardless of ``candidates`` order.
    """
    if not candidates:
        return (MatchTier.MISS, None)

    # ---- Tier 1: EXACT URL (case-sensitive — a URL is case-significant). ----
    stream_url = stream.get("url")
    if isinstance(stream_url, str) and stream_url:
        match_id = _lowest_id_where(
            candidates,
            lambda c: c.get("url") == stream_url,
        )
        if match_id is not None:
            return (MatchTier.EXACT_URL, match_id)

    # Normalize the archived stream's name ONCE; reused by Tiers 2–4.
    norm_stream_name = _normalized_name(stream)

    # A stream with no usable name can only match on URL (Tier 1, already
    # tried). Skip the name-based tiers — they would compare against "".
    if not norm_stream_name:
        return (MatchTier.MISS, None)

    # ---- Tier 2: EXACT NORMALIZED NAME + SAME PROVIDER. ----
    stream_provider = _provider_id(stream)
    if stream_provider is not None:
        match_id = _lowest_id_where(
            candidates,
            lambda c: (
                _provider_id(c) == stream_provider
                and _normalized_name(c) == norm_stream_name
            ),
        )
        if match_id is not None:
            return (MatchTier.EXACT_NAME_SAME_PROVIDER, match_id)

    # ---- Tier 3: EXACT NORMALIZED NAME (any provider). ----
    match_id = _lowest_id_where(
        candidates,
        lambda c: _normalized_name(c) == norm_stream_name,
    )
    if match_id is not None:
        return (MatchTier.EXACT_NORMALIZED_NAME, match_id)

    # ---- Tier 4: FUZZY NORMALIZED NAME (>= STREAM_FUZZY_FLOOR). ----
    # Floored off for the sync path (allow_fuzzy=False, ruling 1b): a fuzzy name
    # guess is not admitted; the call returns MISS so the orphan falls through to
    # the custom-stream fallback rather than silently attaching a wrong stream.
    if not allow_fuzzy:
        return (MatchTier.MISS, None)

    # Iterate explicitly so we can apply the lowest-id tie-break across the BEST
    # fuzzy score: pick the highest score, breaking ties on lowest id. This is
    # the only tier where candidates can score *differently*, so the helper
    # (which only filters) is not enough — we track best (score, id) here.
    best_score = STREAM_FUZZY_FLOOR
    best_id: int | None = None
    for candidate in candidates:
        cand_id = _stream_id(candidate)
        if cand_id is None:
            continue
        norm_cand_name = _normalized_name(candidate)
        if not norm_cand_name:
            continue
        score = fuzz.token_set_ratio(norm_stream_name, norm_cand_name) / 100.0
        if score < STREAM_FUZZY_FLOOR:
            continue
        if best_id is None or score > best_score or (
            score == best_score and cand_id < best_id
        ):
            best_score = score
            best_id = cand_id
    if best_id is not None:
        return (MatchTier.FUZZY_NORMALIZED_NAME, best_id)

    return (MatchTier.MISS, None)


def _lowest_id_where(
    candidates: Sequence[Mapping],
    predicate,
) -> int | None:
    """Lowest integer ``id`` among candidates satisfying ``predicate``.

    The deterministic tie-break for the exact tiers (1–3): when several
    candidates match, the lowest stream id wins, independent of list order.
    Candidates with no usable integer id are skipped. Returns ``None`` when no
    candidate matches.

    Args:
        candidates: The destination streams to scan (never mutated).
        predicate: A callable ``Mapping -> bool`` selecting matching candidates.

    Returns:
        The minimum matching ``id``, or ``None`` if none match.
    """
    best: int | None = None
    for candidate in candidates:
        cand_id = _stream_id(candidate)
        if cand_id is None:
            continue
        if predicate(candidate) and (best is None or cand_id < best):
            best = cand_id
    return best
