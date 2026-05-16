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
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Hard confidence floor — defense-in-depth integrity constraint per ADR-008
# §D2. Module-level constant so the settings validator (BD-B) can import the
# same value and the two layers cannot drift. Changing this is an ADR
# addendum, not a runtime config change — see ADR-008 §D2 final paragraph.
CONFIDENCE_FLOOR: float = 0.60


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


def _normalize(value: str) -> str:
    """Universal-fallback normalization (ADR-008 §D2-adjacent).

    NFC unicode normalization → lowercase → strip leading/trailing
    whitespace. This is the matcher's safety net so callers passing raw
    M3U-derived names still get a sensible score; BD-D's per-group rule
    runs *before* this for the richer path.
    """
    return unicodedata.normalize("NFC", value).lower().strip()


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
