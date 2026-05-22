"""
Example-based regression tests for the dedup matcher service (bd-7xo8e / BD-A).

Covers normalization (NFC, whitespace, case), tie-break,
exact-match-beats-threshold, edge cases (empty stream, unicode, very long
strings), and a rough microbench documenting per-call latency on a
500-candidate list (informational — flag if > 5ms).

Property-based invariants (ADR-008 §D2 floor contract) live in
``test_dedup_matcher_properties.py``.
"""
from __future__ import annotations

import time

import pytest

from services.dedup_matcher import (
    CONFIDENCE_FLOOR,
    MatchResult,
    find_candidate,
)


# ---------------------------------------------------------------------------
# Constant / contract sanity
# ---------------------------------------------------------------------------


class TestConfidenceFloorConstant:
    """The floor is a single source-of-truth import target (ADR-008 §D2)."""

    def test_floor_is_0_60(self):
        assert CONFIDENCE_FLOOR == 0.60

    def test_floor_is_a_float(self):
        # Schema stores REAL (0.0-1.0). A stray int 60 would silently
        # break the threshold comparisons in find_candidate.
        assert isinstance(CONFIDENCE_FLOOR, float)


# ---------------------------------------------------------------------------
# Exact / normalized-equality path
# ---------------------------------------------------------------------------


class TestExactMatch:
    """Exact match after normalization → confidence = 1.00."""

    def test_returns_confidence_1_on_exact_match(self):
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0
        assert result.candidate_channel_id == "uuid-a"
        assert result.candidate_name == "ESPN HD"

    def test_case_insensitive_after_normalization(self):
        # Universal-fallback normalization lowercases both sides; raw
        # token_set_ratio is case-sensitive, so this is the matcher's
        # safety net — not RapidFuzz's behavior.
        result = find_candidate(
            stream_name="espn hd",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_strips_leading_trailing_whitespace(self):
        result = find_candidate(
            stream_name="   ESPN HD   ",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_nfc_normalizes_decomposed_forms(self):
        # 'é' (NFC, single codepoint U+00E9) vs 'e' + combining acute
        # (NFD, two codepoints). NFC normalization on both sides
        # collapses them to the same canonical form.
        nfc_form = "Café HD"
        nfd_form = "Café HD"
        result = find_candidate(
            stream_name=nfc_form,
            candidates=[("uuid-a", nfd_form)],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Fuzzy path + threshold semantics
# ---------------------------------------------------------------------------


class TestFuzzyMatchAboveThreshold:
    """RapidFuzz score / 100.0 ≥ threshold → MatchResult emitted."""

    def test_subset_tokens_score_high(self):
        # 'ESPN' is a subset of 'ESPN HD' tokens; token_set_ratio gives
        # 100% on subset-equal token sets.
        result = find_candidate(
            stream_name="ESPN",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_one_overlap_token_below_default_threshold(self):
        # 'ESPN HD' vs 'ESPN SD' → ~0.857 — above the floor and above
        # the operator-default 0.80.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN SD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence >= 0.80
        assert result.confidence < 1.0  # not an exact match


class TestFuzzyMatchBelowThreshold:
    """Below threshold → None."""

    def test_returns_none_when_score_below_operator_threshold(self):
        # Strings with no shared tokens score below the floor, and the
        # operator-set 0.95 threshold is also above the actual score.
        result = find_candidate(
            stream_name="alpha beta gamma",
            candidates=[("uuid-a", "delta epsilon zeta")],
            threshold=0.95,
        )
        assert result is None

    def test_returns_none_when_score_below_floor(self):
        # Operator asks for 0.30 (below the floor) but the matcher
        # clamps to 0.60. The candidate scores ~0.35 — below the clamped
        # threshold, so None is returned even though the operator's
        # nominal threshold (0.30) would have admitted it.
        result = find_candidate(
            stream_name="alpha beta gamma",
            candidates=[("uuid-a", "delta epsilon zeta")],
            threshold=0.30,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tie-break (lower channel_id wins)
# ---------------------------------------------------------------------------


class TestTieBreak:
    """When two candidates score identically, lower channel_id wins."""

    def test_lower_channel_id_wins_on_equal_score(self):
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[
                ("uuid-zzz", "ESPN HD"),
                ("uuid-aaa", "ESPN HD"),
            ],
            threshold=0.80,
        )
        assert result is not None
        assert result.candidate_channel_id == "uuid-aaa"

    def test_tie_break_independent_of_input_order(self):
        # Reverse the input order — result must be the same.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[
                ("uuid-aaa", "ESPN HD"),
                ("uuid-zzz", "ESPN HD"),
            ],
            threshold=0.80,
        )
        assert result is not None
        assert result.candidate_channel_id == "uuid-aaa"

    def test_higher_score_beats_lower_id(self):
        # Tie-break only applies when scores are equal. A higher-scoring
        # candidate with a lexically larger UUID still wins.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[
                ("uuid-aaa", "Different Name Entirely"),
                ("uuid-zzz", "ESPN HD"),
            ],
            threshold=0.80,
        )
        assert result is not None
        assert result.candidate_channel_id == "uuid-zzz"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Empty inputs, unicode, very long strings."""

    def test_empty_candidates_returns_none(self):
        assert find_candidate("ESPN HD", [], threshold=0.80) is None

    def test_empty_stream_name_returns_none(self):
        # Empty stream can only score 0.0 against any candidate.
        # Short-circuited explicitly.
        result = find_candidate(
            stream_name="",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is None

    def test_whitespace_only_stream_name_returns_none(self):
        # After strip() the normalized form is empty, same short-circuit.
        result = find_candidate(
            stream_name="   \t  \n  ",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert result is None

    def test_empty_candidate_name_is_skipped(self):
        # A candidate whose name normalizes to empty cannot match
        # anything; it's skipped, not crashed.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "   "), ("uuid-b", "ESPN HD")],
            threshold=0.80,
        )
        assert result is not None
        assert result.candidate_channel_id == "uuid-b"
        assert result.confidence == 1.0

    def test_unicode_match(self):
        # Cyrillic name should round-trip through NFC + lowercase cleanly.
        result = find_candidate(
            stream_name="Россия 1",
            candidates=[("uuid-a", "РОССИЯ 1")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_very_long_strings_do_not_crash(self):
        # Defensive: 10 KB stream name and candidate. The fuzzy path
        # must complete in reasonable time and return a deterministic
        # answer (exact match → 1.0).
        long_name = "Channel " + "x" * 10_000
        result = find_candidate(
            stream_name=long_name,
            candidates=[("uuid-a", long_name)],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_threshold_at_floor_admits_exact_match(self):
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=CONFIDENCE_FLOOR,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_threshold_above_one_returns_none_for_non_exact(self):
        # An operator setting threshold = 1.5 (nonsense) gets nothing
        # but exact matches; the clamp goes upward as well as downward
        # in spirit because no fuzzy score exceeds 1.0.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN SD")],
            threshold=1.5,
        )
        assert result is None

    def test_returns_matchresult_dataclass(self):
        # Frozen dataclass — operators downstream rely on the shape.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN HD")],
            threshold=0.80,
        )
        assert isinstance(result, MatchResult)
        # Immutable.
        with pytest.raises((AttributeError, Exception)):
            result.confidence = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Microbench — informational, flag if > 5ms per call against 500 candidates.
# Per task spec: "Per-call latency on find_candidate for 100 candidates
# (rough microbench in test) — flag if > 5ms". We run 500 because that's
# the bulk-M3U dimension in the ADR/epic (200×500 candidates inline) and
# 5ms / 500 is a stricter bar than 5ms / 100.
# ---------------------------------------------------------------------------


class TestPerformanceMicrobench:
    """Documented per-call latency. Not a hard gate — informational."""

    def test_find_candidate_under_5ms_per_call_for_500_candidates(self):
        candidates = [
            (f"uuid-{i:04d}", f"Channel Name Number {i}") for i in range(500)
        ]

        # Warm-up — first call may include lazy imports / module load.
        find_candidate("Channel Name Number 250", candidates, threshold=0.80)

        n_iterations = 50
        start = time.perf_counter()
        for _ in range(n_iterations):
            find_candidate(
                "Channel Name Number 250", candidates, threshold=0.80
            )
        elapsed_seconds = time.perf_counter() - start
        per_call_ms = (elapsed_seconds / n_iterations) * 1000

        # Soft cap at 5ms per call. RapidFuzz against 500 short strings
        # is typically well under this on CI hardware.
        assert per_call_ms < 5.0, (
            f"find_candidate took {per_call_ms:.2f}ms per call against 500 "
            f"candidates — exceeds 5ms soft cap (informational)."
        )
