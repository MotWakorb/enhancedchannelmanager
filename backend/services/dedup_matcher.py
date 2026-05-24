"""
Dedup matcher service — interactive stream-to-channel deduplication scorer.

Encodes ADR-008 §D2 (the hard confidence floor as a defense-in-depth
integrity constraint) for the v0.17.1 deduplication epic (bd-1v4ht). Every
confidence score the operator sees in the Pending Merges UI or via MCP
flows through ``find_candidate`` here.

ADR-008 §D2 contract (paraphrased):

    The matcher refuses to emit a candidate below ``CONFIDENCE_FLOOR``,
    regardless of the operator-configured threshold. The operator-facing
    threshold (default 80%, configurable 60-100 in Settings → Channel
    Defaults — BD-B / BD-K) can be set as low as the floor but no lower.
    Below the floor, the matcher returns "no candidate" — silent refusal,
    not an offer-merge prompt.

The floor is the load-bearing enforcement. The settings-side validator
(BD-B) is the early-rejection courtesy. Both layers import
``CONFIDENCE_FLOOR`` from this module so the two cannot drift.

Normalization policy (matcher-side fallback). The matcher applies a
universal NFC + lowercase + whitespace-strip normalization to *both*
``stream_name`` and every ``candidate_name`` before scoring. The richer
per-group normalization rule (epic ratification) is BD-D's caller-side
responsibility — BD-D pre-normalizes inputs using the group's configured
rule and the matcher's universal fallback is then a no-op on
already-cased / already-stripped strings. This keeps the matcher
deterministic and self-contained: passing raw operator inputs is always
safe, the floor still holds, and callers that need richer normalization
own that step explicitly.

Scoring:

* Exact match after normalization → confidence = 1.00.
* Otherwise → RapidFuzz ``fuzz.token_set_ratio(...) / 100.0``.
* Tie-break: lower ``channel_id`` wins (lexicographic on the string UUID
  — ASCII order is deterministic for Dispatcharr's UUID format).

Returns ``None`` when the candidates list is empty, when the top-1 score
falls below ``max(threshold, CONFIDENCE_FLOOR)``, or when both inputs
normalize to empty strings (defensive — RapidFuzz returns 0.0 for empty
inputs, but we short-circuit so the contract is explicit in code).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from rapidfuzz import fuzz

# Reuse the DB-free normalization helpers rather than re-implementing them
# (engineering-discipline: reuse before creating). ``convert_superscripts``
# (ᴿᴬᵂ→RAW) and ``extract_call_sign`` (WBAY/WGBA) are both verified DB-free
# in normalization_engine.py — the module-level function and the @staticmethod
# respectively. The LOCALS cleaner and the M1 callsign gate are built on them.
from normalization_engine import NormalizationEngine, convert_superscripts

logger = logging.getLogger(__name__)

# Leading channel-number prefix of the form ``<digits> | `` (e.g.
# ``5 | US : ESPN 2``). Dispatcharr renders channel numbers as a ``N | ``
# prefix on the channel name; stripping it before scoring lets a real
# prefixed channel name match an unprefixed incoming stream name (lq38l.7).
# Anchored at the start and requires the number+pipe so a legitimate
# interior ``|`` (e.g. ``HBO | East``) is never touched.
_CHANNEL_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+\s*\|\s*")

# Hard confidence floor — defense-in-depth integrity constraint per ADR-008
# §D2. Module-level constant so the settings validator (BD-B) can import the
# same value and the two layers cannot drift. Changing this is an ADR
# addendum, not a runtime config change — see ADR-008 §D2 final paragraph.
CONFIDENCE_FLOOR: float = 0.60

# Score a no-callsign fuzzy match must reach before it can be admitted via the
# explicit opt-in flag (enhancedchannelmanager-jnzst Q1). The default policy is
# to REQUIRE a parseable callsign on both sides; admitting a no-callsign pair is
# strictly riskier (the WGBA negative scored 0.889 against the WBAY channel in
# the spike) so it is gated behind this high bar.
NO_CALLSIGN_FLOOR: float = 0.90


class NameCleanMode(str, Enum):
    """Selects how aggressively a name is cleaned before scoring.

    A single shared cleaner (``_normalize``) is parameterized by this mode so
    there is exactly ONE cleaner in the system — the 1,341-merge incident fix
    (bd-0emgo.1) pinned the strict path, and every dedup / m3u_dedup_hook /
    exact caller stays byte-for-byte unchanged on ``CONSERVATIVE``.

    CONSERVATIVE
        Today's behavior exactly: NFC → strip ``<digits> | `` prefix →
        lowercase → strip. This is the load-bearing default.
    LOCALS
        CONSERVATIVE plus the OTA/affiliate-specific steps (dotted sub-channel
        prefixes, source/market prefixes, superscript conversion, quality-tag
        stripping, run-together token splitting). Used ONLY by the new scored
        fuzzy path (rule loose_name_match + min_score, preview, MCP).
    """

    CONSERVATIVE = "conservative"
    LOCALS = "locals"


# ---------------------------------------------------------------------------
# LOCALS cleaner support tables (enhancedchannelmanager-jnzst Component 1).
#
# These run ONLY under NameCleanMode.LOCALS. Every pattern is anchored or
# bounded so it cannot catastrophically backtrack (ReDoS guard, .semgrep.yml).
# ---------------------------------------------------------------------------

# (a) Dotted / sub-channel number prefix: ``2.1 | ``, ``2 | `` (the dotted form
# the CONSERVATIVE prefix regex does not cover). Anchored, single quantifier.
_LOCALS_DOTTED_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)?\s*\|\s*")

# (b) Leading source / market prefixes. The alternation is an EXPLICIT closed
# set of known source/market tokens — US state codes, a short list of country
# codes, and ``CITY`` — each followed by a ``:`` or ``|`` delimiter, plus the
# ``DIREC TV`` market tag. Constraining to an explicit set (rather than a
# generic ``[A-Z]{2,4}`` shape) prevents stripping network/premium call letters
# like ``ABC:``, ``NBC|``, ``HBO |`` that are part of the real channel name
# (enhancedchannelmanager-jnzst FIX 3). ``IN``/``OR``/``OK`` etc. are English
# words but only stripped when immediately followed by a delimiter, which a
# network name never is.
#
# ReDoS note (FIX 1): the pattern assumes whitespace has ALREADY been collapsed
# to single spaces by the caller (``_normalize`` does this before invoking the
# prefix loop). With at-most-one space between token and delimiter there is no
# overlapping ``\s*`` to backtrack on, so the match is linear in input length.
# The old pattern (``^\s*(?:\|?\s*[A-Z]{2,4}\s*[:|]|…)\s*``) had three
# overlapping ``\s*`` groups and backtracked O(n^2) on whitespace-padded input
# (~1.5 s on 16k chars) — it ran through stdlib ``re`` (no safe_regex timeout)
# on attacker-controllable M3U names inline on the event loop.
_LOCALS_SOURCE_TOKENS = (
    # US state codes (USPS two-letter).
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
    # Country / source tags.
    "USA", "US", "UK", "AU", "NZ", "IE", "CITY",
)
# Longest-first so the alternation prefers ``USA`` over ``US`` (and never leaves
# a dangling ``A``). Assembled once at import from the hard-coded token tuple
# above — never attacker-supplied — so the interpolated value is effectively a
# literal and the ReDoS rule does not apply.
_LOCALS_SOURCE_ALT = "|".join(
    sorted(_LOCALS_SOURCE_TOKENS, key=lambda t: (-len(t), t))
)
# nosemgrep: no-bare-re-on-dynamic-pattern -- _LOCALS_SOURCE_ALT is built from the hard-coded _LOCALS_SOURCE_TOKENS literal tuple, not runtime input; whitespace is pre-collapsed by _normalize so this is linear-time.
_LOCALS_SOURCE_PREFIX_RE = re.compile(  # nosemgrep: no-bare-re-on-dynamic-pattern -- interpolated value _LOCALS_SOURCE_ALT is a hard-coded literal alternation, not attacker input
    rf"^\|? ?(?:(?:{_LOCALS_SOURCE_ALT}) ?[:|]|DIREC TV\b) ?",
    re.IGNORECASE,
)

# Defensive length cap on a name entering ``_normalize``. Real channel/stream
# names are short (Dispatcharr UI columns are ~80 chars); a name longer than
# this is either junk or an amplification attempt. We truncate before any regex
# work so even a non-linear future edit cannot be driven superlinear by raw
# input length. 512 is generous headroom over the longest realistic name while
# still bounding per-call regex work. (Note: the LOCALS prefix regex is already
# linear; this cap is belt-and-suspenders for every step in the cleaner.)
_NORMALIZE_MAX_LEN: int = 512

# (d) Quality / event tags stripped as whole words anywhere in the string.
# Bounded alternation of fixed tokens — no nested quantifiers.
_LOCALS_QUALITY_TAG_RE = re.compile(
    r"\b(?:RAW|UHD|FHD|HD|SD|60FPS)\b|\[EVENT\]",
    re.IGNORECASE,
)

# (e) Run-together market tokens. A small explicit map keeps this deterministic
# and free of heuristic word-splitting (which would mis-split real names). The
# brief's only fixture is GREENBAY→GREEN BAY; extend the map as new fixtures
# appear rather than guessing splits.
_LOCALS_RUN_TOGETHER = {
    "greenbay": "green bay",
}

# Collapse residual delimiters / whitespace produced by the strips above.
_LOCALS_DELIM_RE = re.compile(r"[|:]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MatchResult:
    """Top-1 candidate for an incoming stream against the target group.

    Attributes
    ----------
    candidate_channel_id:
        Dispatcharr channel UUID (TEXT in ECM's schema — no local FK per
        ADR-008 §D4). The accept/dismiss routes (BD-E) take this verbatim.
    candidate_name:
        Human-readable channel name for the modal / MCP response payload.
        This is the *raw* name from the input tuple, not the normalized
        form — operators should see what is actually stored upstream.
    confidence:
        Normalized score in [0.0, 1.0]. Always ≥ ``CONFIDENCE_FLOOR``
        when this object is returned — the matcher refuses to emit
        anything below the floor (ADR-008 §D2).
    """

    candidate_channel_id: str
    candidate_name: str
    confidence: float


# Signal labels recorded in the journal so an operator can audit WHY a merge
# fired. These are the four signals the precedence ladder can emit.
SIGNAL_CALLSIGN_EXACT = "callsign-exact"
SIGNAL_TVG_ID_OVERRIDE = "tvg_id-override"
SIGNAL_FUZZY_WITH_CALLSIGN = "fuzzy-with-callsign"
SIGNAL_FUZZY_NO_CALLSIGN = "fuzzy-no-callsign-floor"
SIGNAL_CALLSIGN_CONFLICT = "callsign-conflict"


@dataclass(frozen=True)
class ScoredMatch:
    """One (stream, candidate) score from the shared core (score_one).

    The unified scorer returns this for EVERY candidate (not just top-1) so
    the rule path, the preview endpoint, and the MCP dry-run can all rank and
    threshold the same way.

    Attributes
    ----------
    score:
        Normalized score in [0.0, 1.0]. ``score_one`` does NOT clamp to the
        floor — the floor clamp is the DEDUP consumer's concern and lives in
        the ``find_candidate`` wrapper. The preview endpoint deliberately
        surfaces sub-floor scores for inspection.
    callsign_verdict:
        ``"match"``   — both sides parsed a call sign and they are equal.
        ``"conflict"``— both sides parsed a call sign and they DIFFER (M1
                        hard-reject fired: score is 0.0, candidate dropped).
        ``"absent"``  — at least one side had no parseable call sign.
    tvg_id_override:
        True when the tvg_id-callsign equality override set the score to 1.0.
        Fails closed: if the name-callsigns conflict (M1), M1 wins and this
        stays False.
    signal:
        Which rung of the precedence ladder produced the score (one of the
        ``SIGNAL_*`` constants). Recorded in the journal for provenance.
    stream_callsign / candidate_callsign:
        The parsed call signs (or None) — journaled so a bad merge is
        auditable without re-parsing.
    """

    score: float
    callsign_verdict: str
    tvg_id_override: bool
    signal: str
    stream_callsign: str | None = None
    candidate_callsign: str | None = None


# ---------------------------------------------------------------------------
# SHARED CORE SCORER (enhancedchannelmanager-jnzst Component 2).
#
# score_one is the single scoring function. Precedence is a HARD order:
#
#   (1) M1 CALLSIGN HARD-REJECT — if both name-callsigns parse and DIFFER
#       (case-insensitive exact), the pair is rejected unconditionally:
#       score 0.0, verdict "conflict". This fires BEFORE any threshold and
#       wins over the tvg_id override and any min_score. It is what cleanly
#       separates the WBAY positives from the WGBA negative in the spike —
#       the fuzzy score alone could not (WGBA scored 0.889 against WBAY).
#
#   (2) tvg_id-callsign equality OVERRIDE — if a stream tvg_id parses a call
#       sign equal to the candidate's call sign, score 1.0. FAILS CLOSED: if
#       (1) fired, M1 wins and the override never runs.
#
#   (3) FUZZY — token_set_ratio on LOCALS-cleaned strings, in [0.0, 1.0].
#
# Thresholds are tunable by the caller (min_score) but the DEDUP wrapper
# (find_candidate) keeps the 0.60 floor clamp + exact short-circuit.
# ---------------------------------------------------------------------------


def score_one(
    stream_name: str,
    candidate_name: str,
    stream_tvg_id: str | None = None,
    candidate_tvg_id: str | None = None,
    mode: NameCleanMode = NameCleanMode.LOCALS,
) -> ScoredMatch:
    """Score one (stream, candidate) pair through the precedence ladder.

    Parameters
    ----------
    stream_name, candidate_name:
        Raw names; cleaned internally with ``mode`` (defaults to LOCALS, the
        scored-fuzzy path).
    stream_tvg_id, candidate_tvg_id:
        Optional tvg_id strings used for the call-sign equality override
        (rung 2). The candidate's call sign is sourced from its tvg_id when
        present (the channel fixture carries ``WBAY-DT(ABC)(WBAYDT).us``),
        falling back to its name.

    Returns
    -------
    ScoredMatch
        Always returned — even for a hard-reject (score 0.0, verdict
        "conflict") so callers can journal the rejection.
    """
    # Parse the stream-side call signs and clean the stream name ONCE, then
    # delegate to the prepared scorer. ``score_all`` hoists these same three
    # computations out of its per-candidate loop (FIX 6) — the prepared scorer
    # is the shared body so single-pair and bulk paths produce identical
    # results.
    stream_cs = extract_callsign(stream_name)
    stream_tvg_cs = extract_callsign(stream_tvg_id) if stream_tvg_id else None
    norm_stream = _normalize(stream_name, mode=mode)
    return _score_one_prepared(
        stream_cs=stream_cs,
        stream_tvg_cs=stream_tvg_cs,
        norm_stream=norm_stream,
        candidate_name=candidate_name,
        candidate_tvg_id=candidate_tvg_id,
        mode=mode,
    )


def _score_one_prepared(
    *,
    stream_cs: str | None,
    stream_tvg_cs: str | None,
    norm_stream: str,
    candidate_name: str,
    candidate_tvg_id: str | None,
    mode: NameCleanMode,
) -> ScoredMatch:
    """Score a candidate against PRE-COMPUTED stream-side values.

    The stream-side callsign (``stream_cs``), tvg_id callsign (``stream_tvg_cs``)
    and cleaned name (``norm_stream``) are computed once by the caller. This is
    the shared body of ``score_one`` (single pair) and ``score_all`` (bulk) —
    hoisting these three out of ``score_all``'s loop is pure perf and cannot
    change results, since every candidate sees the identical stream-side inputs
    ``score_one`` would have recomputed.
    """
    cand_cs_name = extract_callsign(candidate_name)

    # (1) M1 CALLSIGN HARD-REJECT — unconditional, first, wins over everything.
    if stream_cs is not None and cand_cs_name is not None \
            and stream_cs.upper() != cand_cs_name.upper():
        return ScoredMatch(
            score=0.0,
            callsign_verdict="conflict",
            tvg_id_override=False,
            signal=SIGNAL_CALLSIGN_CONFLICT,
            stream_callsign=stream_cs,
            candidate_callsign=cand_cs_name,
        )

    # (2) tvg_id-callsign equality OVERRIDE → 1.0. Fails closed: only reached
    # because M1 did not fire above. The candidate call sign for the override
    # prefers its tvg_id (richer source) then its name. ``stream_tvg_cs`` is
    # pre-computed by the caller (hoisted out of score_all's loop, FIX 6).
    cand_tvg_cs = extract_callsign(candidate_tvg_id) if candidate_tvg_id else None
    cand_cs_effective = cand_cs_name or cand_tvg_cs
    override_stream_cs = stream_cs or stream_tvg_cs
    if (
        override_stream_cs is not None
        and cand_cs_effective is not None
        and override_stream_cs.upper() == cand_cs_effective.upper()
    ):
        return ScoredMatch(
            score=1.0,
            callsign_verdict="match",
            tvg_id_override=True,
            signal=SIGNAL_TVG_ID_OVERRIDE,
            stream_callsign=override_stream_cs,
            candidate_callsign=cand_cs_effective,
        )

    # (3) FUZZY on LOCALS-cleaned strings. ``norm_stream`` is pre-computed by
    # the caller (hoisted out of score_all's loop, FIX 6).
    norm_cand = _normalize(candidate_name, mode=mode)
    if not norm_stream or not norm_cand:
        score = 0.0
    elif norm_stream == norm_cand:
        score = 1.0
    else:
        score = fuzz.token_set_ratio(norm_stream, norm_cand) / 100.0

    # Verdict + signal reflect whether a callsign was available on both sides.
    if stream_cs is not None and cand_cs_name is not None:
        # Both parsed and (since M1 did not fire) they are equal.
        verdict = "match"
        signal = SIGNAL_FUZZY_WITH_CALLSIGN
    else:
        verdict = "absent"
        signal = SIGNAL_FUZZY_NO_CALLSIGN

    return ScoredMatch(
        score=score,
        callsign_verdict=verdict,
        tvg_id_override=False,
        signal=signal,
        stream_callsign=stream_cs,
        candidate_callsign=cand_cs_name,
    )


def score_all(
    stream_name: str,
    candidates: list[tuple[str, str]],
    stream_tvg_id: str | None = None,
    candidate_tvg_ids: dict[str, str] | None = None,
    mode: NameCleanMode = NameCleanMode.LOCALS,
):
    """Score ``stream_name`` against EVERY candidate (not top-1).

    Yields ``(channel_id, channel_name, ScoredMatch)`` triples in input order.
    The preview endpoint and MCP dry-run consume this; the dedup wrapper keeps
    its own top-1 logic for the floor/exact contract.

    ``candidate_tvg_ids`` maps ``channel_id`` → tvg_id so the tvg_id override
    can run per candidate without changing the ``(id, name)`` tuple shape the
    rest of the system passes around.

    Perf (FIX 6): the stream-side callsign, tvg_id callsign and cleaned name are
    computed ONCE here and reused for every candidate, instead of re-cleaning
    the stream name per candidate inside ``score_one``. Results are identical —
    the stream-side inputs are constant across the loop.
    """
    tvg_map = candidate_tvg_ids or {}
    stream_cs = extract_callsign(stream_name)
    stream_tvg_cs = extract_callsign(stream_tvg_id) if stream_tvg_id else None
    norm_stream = _normalize(stream_name, mode=mode)
    for channel_id, channel_name in candidates:
        yield channel_id, channel_name, _score_one_prepared(
            stream_cs=stream_cs,
            stream_tvg_cs=stream_tvg_cs,
            norm_stream=norm_stream,
            candidate_name=channel_name,
            candidate_tvg_id=tvg_map.get(channel_id),
            mode=mode,
        )


def is_admissible(
    scored_match: ScoredMatch,
    *,
    min_score: float,
    allow_no_callsign: bool,
) -> bool:
    """The ONE admission policy shared by every write/assign consumer (FIX 2).

    This is the single source of truth for "may this scored pair be admitted
    as a match?" — distinct from ``score_one``, which only computes the score
    and verdict. The rule executor, the preview endpoint, and (transitively,
    via the preview endpoint) both MCP write tools all funnel through here so
    no consumer can apply its own score-only policy and re-admit an incident-
    class false positive (an M1 ``conflict`` or a no-callsign ``absent`` pair).

    Policy (precedence already enforced inside ``score_one``):

    * ``conflict`` verdict (M1 callsign hard-reject fired) → NEVER admissible,
      regardless of ``min_score`` (even at ``min_score == 0``).
    * ``absent`` verdict (a callsign is missing on at least one side) →
      admissible ONLY when ``allow_no_callsign`` is True AND the score reaches
      ``NO_CALLSIGN_FLOOR`` (0.90). The default policy REQUIRES a parseable
      callsign on both sides (Q1).
    * ``match`` verdict (both callsigns parsed and agree, OR a tvg_id override
      set 1.0) → admissible when ``score >= min_score``.

    Parameters
    ----------
    scored_match:
        The ``ScoredMatch`` from ``score_one`` / ``score_all``.
    min_score:
        Minimum score for a ``match``-verdict pair. The caller is responsible
        for any floor clamp (the schema floors the rule path at
        ``CONFIDENCE_FLOOR``; the preview deliberately allows sub-floor
        ``min_score`` so it can SURFACE low scores — but a ``conflict`` is
        still never admissible there).
    allow_no_callsign:
        Q1 opt-in. When True, an ``absent`` pair at ``score >=
        NO_CALLSIGN_FLOOR`` becomes admissible. Default callers pass False.

    Returns
    -------
    bool
        True only when the pair clears the policy above.
    """
    verdict = scored_match.callsign_verdict
    if verdict == "conflict":
        return False
    if verdict == "absent":
        return allow_no_callsign and scored_match.score >= NO_CALLSIGN_FLOOR
    # "match" verdict.
    return scored_match.score >= min_score


def _normalize(value: str, mode: NameCleanMode = NameCleanMode.CONSERVATIVE) -> str:
    """The ONE shared name cleaner, parameterized by ``mode``.

    There is exactly one cleaner in the system; ``mode`` selects how
    aggressive it is. ``CONSERVATIVE`` (the default) is byte-for-byte the
    historical behavior every dedup / m3u_dedup_hook / exact caller relies
    on — the 1,341-merge incident fix pinned this and a regression test
    asserts ``_normalize(x) == _normalize(x, CONSERVATIVE)`` for the incident
    fixtures.

    CONSERVATIVE
        NFC unicode normalization → strip a leading ``N | `` channel-number
        prefix → lowercase → strip leading/trailing whitespace. The
        matcher's safety net so callers passing raw M3U-derived names still
        get a sensible score.

        The channel-number-prefix strip (lq38l.7) removes a leading
        ``<digits> | `` only — Dispatcharr prepends the channel number to the
        name (e.g. ``5 | US : ESPN 2``). The regex is anchored at the start,
        so an interior ``|`` (``HBO | East``) is preserved.

    LOCALS
        CONSERVATIVE plus the OTA/affiliate-specific steps required for the
        scored fuzzy path (enhancedchannelmanager-jnzst): (a) dotted
        sub-channel number prefix; (b) leading source/market prefixes — an
        EXPLICIT closed token set (US state codes, country codes, ``CITY``,
        ``DIREC TV``) followed by a delimiter, so network call letters like
        ``ABC:`` / ``HBO |`` are preserved (FIX 3); (c) superscript conversion
        via the reused ``convert_superscripts`` (ᴿᴬᵂ→RAW); (d) quality-tag
        stripping (RAW/UHD/HD/FHD/SD/60fps/[EVENT]); (e) run-together token
        splitting (GREENBAY→GREEN BAY). The result is lowercased, de-delimited
        and whitespace-collapsed.

    ReDoS hardening (FIX 1). The input is truncated to ``_NORMALIZE_MAX_LEN``
    before any regex runs, and LOCALS collapses whitespace to single spaces
    BEFORE the source-prefix loop so the (formerly O(n^2)) prefix regex matches
    in linear time regardless of how much whitespace an attacker pads a name
    with.
    """
    # Defensive length cap: truncate before ANY regex work so raw input length
    # alone can never drive superlinear runtime (FIX 1). Applied to both modes.
    if len(value) > _NORMALIZE_MAX_LEN:
        value = value[:_NORMALIZE_MAX_LEN]

    normalized = unicodedata.normalize("NFC", value)

    if mode is NameCleanMode.CONSERVATIVE:
        # Strip the leading ``N | `` prefix only when a non-blank name remains
        # after it. A degenerate name that is *only* a channel-number prefix
        # (e.g. ``5 | ``) keeps its original form rather than collapsing to an
        # empty, un-matchable string — preserving the invariant that a
        # non-blank input never normalizes to empty.
        stripped = _CHANNEL_NUMBER_PREFIX_RE.sub("", normalized)
        if stripped.strip():
            normalized = stripped
        return normalized.lower().strip()

    # ---- LOCALS mode ----
    # (c) Superscript conversion first so quality tags written as superscripts
    # (ᴿᴬᵂ) become strippable ASCII before step (d). convert_superscripts
    # re-applies NFC internally — harmless, idempotent.
    work = convert_superscripts(normalized)

    # FIX 1 (ReDoS): collapse all whitespace runs to single spaces ONCE, up
    # front, before any prefix regex runs. The source-prefix regex (step b)
    # then only ever sees single spaces between token and delimiter, so it
    # cannot backtrack on overlapping ``\s*`` — the match is linear in input
    # length. (The old pattern with three ``\s*`` groups went O(n^2) on
    # whitespace-padded input.) Strip leading/trailing so the anchored prefix
    # regexes see a clean start.
    work = _WHITESPACE_RE.sub(" ", work).strip()

    # (a) Strip a leading dotted/plain sub-channel number prefix (``2.1 | ``).
    # Guarded the same way as CONSERVATIVE: never collapse to empty.
    stripped = _LOCALS_DOTTED_PREFIX_RE.sub("", work)
    if stripped.strip():
        work = stripped

    # (b) Strip leading source/market prefixes, repeatedly for stacked tags
    # (``US| CITY: …``). Bounded loop so a pathological input cannot spin.
    for _ in range(4):
        stripped = _LOCALS_SOURCE_PREFIX_RE.sub("", work, count=1)
        if stripped == work or not stripped.strip():
            break
        work = stripped

    work = work.lower()

    # (e) Split known run-together market tokens before delimiter collapse so
    # the replacement words survive whitespace normalization.
    for joined, split in _LOCALS_RUN_TOGETHER.items():
        if joined in work:
            work = work.replace(joined, split)

    # (d) Strip quality / event tags as whole words.
    work = _LOCALS_QUALITY_TAG_RE.sub(" ", work)

    # Collapse residual delimiters and whitespace.
    work = _LOCALS_DELIM_RE.sub(" ", work)
    work = _WHITESPACE_RE.sub(" ", work).strip()
    return work


def extract_callsign(name: str) -> str | None:
    """Thin re-export of the DB-free FCC call-sign extractor.

    The core's M1 callsign hard-reject (enhancedchannelmanager-jnzst) uses
    ``NormalizationEngine.extract_call_sign`` — a verified DB-free
    @staticmethod that returns WBAY/WGBA correctly from both names and tvg_ids
    (e.g. ``WBAY-DT(ABC)(WBAYDT).us`` → ``WBAY``). Re-exported here so the
    core, the rule executor, the preview endpoint, and tests share ONE
    callsign parser. Returns ``None`` when no call sign is found.
    """
    if not name:
        return None
    return NormalizationEngine.extract_call_sign(name)


def find_candidate(
    stream_name: str,
    candidates: list[tuple[str, str]],
    threshold: float,
) -> MatchResult | None:
    """Score ``stream_name`` against ``candidates`` and return the top-1 hit.

    Parameters
    ----------
    stream_name:
        Raw incoming stream name (e.g. from an M3U entry or a drag-drop
        payload). Will be normalized internally; pass as-stored.
    candidates:
        List of ``(channel_id, channel_name)`` tuples representing the
        existing channels in the target group's scope. Channel IDs are
        Dispatcharr UUIDs (strings).
    threshold:
        Operator-configured minimum confidence in [0.0, 1.0]. **Clamped
        to ``CONFIDENCE_FLOOR``** before any RapidFuzz call — a request
        for a 0.30 threshold gets 0.60 behavior. The clamp is the
        load-bearing enforcement of ADR-008 §D2; the settings-side
        validator (BD-B) is the early-rejection courtesy.

    Returns
    -------
    MatchResult | None
        ``None`` when ``candidates`` is empty, when both inputs normalize
        to empty strings, or when the top-1 score falls below the
        clamped threshold. Otherwise a ``MatchResult`` whose
        ``confidence`` is guaranteed to be ≥ ``CONFIDENCE_FLOOR``.

    Notes
    -----
    Exact-match short-circuit. An exact case-insensitive match after
    normalization returns ``confidence = 1.00`` without running
    RapidFuzz. The exact-match path is unconditional — it does not
    inspect ``threshold`` — because an exact match is the strongest
    possible signal and any operator-configured threshold is, by
    definition, below 1.00.

    Tie-break. When two candidates produce the same score, the lower
    lexicographic ``channel_id`` wins. UUIDs are ASCII-only so plain
    string comparison is deterministic.
    """
    if not candidates:
        return None

    # Clamp threshold to the hard floor. ADR-008 §D2: "BD-A's
    # find_candidate(...) clamps threshold = max(threshold, HARD_FLOOR)
    # before any RapidFuzz call." Logged at DEBUG so the operator's
    # intent is visible in postmortem traces but the action is safe.
    effective_threshold = max(threshold, CONFIDENCE_FLOOR)
    if effective_threshold != threshold:
        logger.debug(
            "[DEDUP] threshold=%.2f clamped to floor=%.2f", threshold, CONFIDENCE_FLOOR
        )

    normalized_stream = _normalize(stream_name)
    if not normalized_stream:
        # Defensive: an empty stream name can only score 0.0 against any
        # candidate (RapidFuzz returns 0.0 for empty inputs). Short-circuit
        # so the contract is explicit and no fuzzy work is done.
        return None

    # Track best score and the candidate that produced it. Iterate in
    # input order so tie-break is decided explicitly below, not by the
    # incidental order RapidFuzz happens to surface.
    best: MatchResult | None = None

    for channel_id, channel_name in candidates:
        normalized_candidate = _normalize(channel_name)
        if not normalized_candidate:
            # Skip candidates that normalize to empty — they can only
            # match an empty stream_name (already short-circuited above).
            continue

        # Exact match wins unconditionally with confidence = 1.00. The
        # exact-match path predates the floor and does not require the
        # threshold check — an exact match is the strongest possible
        # signal, and any operator-configured threshold ≤ 1.00 admits it.
        if normalized_stream == normalized_candidate:
            confidence = 1.0
        else:
            # token_set_ratio returns 0.0-100.0; normalize to 0.0-1.0 to
            # match the schema's REAL column convention (ADR-008 §D8).
            confidence = fuzz.token_set_ratio(
                normalized_stream, normalized_candidate
            ) / 100.0

        if confidence < effective_threshold:
            continue

        if best is None:
            best = MatchResult(
                candidate_channel_id=channel_id,
                candidate_name=channel_name,
                confidence=confidence,
            )
            continue

        if confidence > best.confidence:
            best = MatchResult(
                candidate_channel_id=channel_id,
                candidate_name=channel_name,
                confidence=confidence,
            )
        elif confidence == best.confidence and channel_id < best.candidate_channel_id:
            # Tie-break: lower lexicographic channel_id wins. Deterministic
            # so the same input always produces the same output, regardless
            # of the order the caller assembled the candidates list.
            best = MatchResult(
                candidate_channel_id=channel_id,
                candidate_name=channel_name,
                confidence=confidence,
            )

    return best
