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
    _normalize,
    airing_key,
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
# _normalize — channel-number prefix strip (lq38l.7)
# ---------------------------------------------------------------------------


class TestNormalizeChannelNumberPrefix:
    """``_normalize`` strips a leading ``N | `` channel-number prefix.

    Dispatcharr renders the channel number as a ``N | `` prefix on the name
    (e.g. ``5 | US : ESPN 2``). Stripping it before scoring lets a real
    prefixed channel name match an unprefixed incoming stream name (lq38l.7).
    """

    def test_strips_leading_number_pipe_prefix(self):
        assert _normalize("5 | US : ESPN 2") == "us : espn 2"

    def test_strips_multi_digit_prefix(self):
        assert _normalize("1351 | FOX Network") == "fox network"

    def test_strips_prefix_without_spaces_around_pipe(self):
        assert _normalize("7|HBO") == "hbo"

    def test_preserves_interior_pipe(self):
        # A legitimate interior pipe is NOT a channel-number prefix and must
        # survive — only a leading ``<digits> | `` is stripped.
        assert _normalize("HBO | East") == "hbo | east"

    def test_does_not_strip_leading_number_without_pipe(self):
        # '2' here is part of the name, not a channel-number prefix.
        assert _normalize("2 Broke Girls") == "2 broke girls"

    def test_prefix_only_name_is_not_collapsed_to_empty(self):
        # A degenerate name that is *only* a channel-number prefix must not
        # normalize to empty (which would make it un-matchable and could
        # violate the identical-input invariant). The strip is skipped when
        # nothing non-blank remains after it.
        assert _normalize("5 | ") != ""
        assert _normalize("7|") != ""

    def test_prefixed_name_matches_unprefixed_stream_exactly(self):
        # End-to-end: a prefixed channel name scores as an exact (1.0) match
        # against the equivalent unprefixed incoming stream name.
        result = find_candidate(
            stream_name="US : ESPN 2",
            candidates=[("11874", "5 | US : ESPN 2")],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0
        assert result.candidate_channel_id == "11874"


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
# Airing gate — schedule-driven slot names (GH #1015)
# ---------------------------------------------------------------------------
#
# A provider hands out a fixed pool of slot names and rolls the fixture and
# the airing over daily. The slot prefix, tournament and round are byte-
# identical day to day, so token_set_ratio scores a next-day fixture against
# yesterday's at 0.86-0.99 — above the operator default of 0.80. The gate
# drops a pair whose two stated airings disagree, before any scoring.
#
# The scorer alone cannot fix this: REMOVING the date/time run leaves the two
# names character-identical and they score 1.00, i.e. still queued. The run is
# the only part of the name that says which airing it is, so a disagreement
# between two present runs is the signal, not noise.


class TestAiringKey:
    """``airing_key`` extracts the date/time run, or None when absent."""

    def test_extracts_provider_shape(self):
        assert airing_key("Slot 04: Final @ 18 Sep 09:30 AM GMT-1") == (
            "18 sep 09:30 am gmt-1"
        )

    def test_none_when_name_states_no_airing(self):
        assert airing_key("ESPN HD") is None
        assert airing_key("Channel 5 | US : ESPN 2") is None

    def test_none_for_bare_day_and_month(self):
        # No clock time → not an airing. A false airing would veto a
        # legitimate duplicate, so the shape is kept narrow.
        assert airing_key("Sports 24/7 Sep") is None
        assert airing_key("18 Sep") is None

    def test_spelling_noise_is_ignored(self):
        noisy = airing_key("X @ 18 sep 09:30 a.m. gmt-1")
        clean = airing_key("X @ 18 Sep 09:30 AM GMT-1")
        assert noisy == clean

    def test_year_is_not_part_of_the_key(self):
        # One provider spelling the year out must not read as a
        # different airing from another that omits it.
        assert airing_key("X 18 Sep 2026 09:30 GMT-1") == airing_key(
            "X 18 Sep 09:30 GMT-1"
        )

    def test_leading_zeros_do_not_split_an_airing(self):
        # A genuine duplicate that one provider zero-pads and another does
        # not must still compare equal, or the gate would veto a real pair.
        assert airing_key("X @ 8 Sep 9:05 AM GMT-1") == airing_key(
            "X @ 08 Sep 09:05 am gmt-1"
        )

    def test_accepts_the_no_marker_shape(self):
        assert airing_key("X 18 Sep 09:30 GMT-1") is not None

    def test_does_not_truncate_into_a_slot_number(self):
        # "04:" is a slot separator, not a clock, and "250" is not a day.
        assert airing_key("SLOT 04: Championship") is None
        assert airing_key("Channel Name Number 250") is None


class TestAiringGate:
    """A next-day fixture in the same slot is a rollover, not a duplicate."""

    SLOT = "EVENT SLOT 04: Championship | Qualifying: Player One - Player Two @ 18 Sep 09:30 AM GMT-1"
    YESTERDAY = "EVENT SLOT 04: Championship | Qualifying: Player Three - Player Four @ 17 Sep 01:00 PM GMT-1"

    def test_same_slot_different_fixture_and_day_is_not_queued(self):
        # The acceptance case: yesterday's channel is still in the group
        # and today's slot has rolled over.
        result = find_candidate(
            stream_name=self.SLOT,
            candidates=[("uuid-yesterday", self.YESTERDAY)],
            threshold=0.80,
        )
        assert result is None

    def test_the_score_alone_would_have_queued_it(self):
        # Guard against the gate being quietly dropped: the same pair
        # scores above the operator default when scored directly, so
        # ``find_candidate`` returning None is the gate, not the threshold.
        from rapidfuzz import fuzz

        score = (
            fuzz.token_set_ratio(_normalize(self.SLOT), _normalize(self.YESTERDAY))
            / 100.0
        )
        assert score >= 0.80

    def test_next_day_with_identical_fixture_is_not_queued(self):
        # A slot that carries the same fixture into the next day is still
        # a different airing: the group is on a daily rollover, so the
        # previous day's channel is not this stream's duplicate.
        next_day = self.SLOT.replace("@ 18 Sep", "@ 19 Sep")
        result = find_candidate(
            stream_name=self.SLOT,
            candidates=[("uuid-next-day", next_day)],
            threshold=0.80,
        )
        assert result is None

    def test_identical_names_still_queue(self):
        # The other half of the acceptance criterion: the gate must not
        # suppress genuine duplicates.
        result = find_candidate(
            stream_name=self.SLOT,
            candidates=[("uuid-a", self.SLOT)],
            threshold=0.80,
        )
        assert result is not None
        assert result.confidence == 1.0

    def test_same_airing_still_scores_fuzzily(self):
        # Same airing, GENUINELY different normalized names → still a
        # candidate, still scored by the fuzzy path (not the exact path).
        # The gate only ever drops a MISMATCH. (PR #1016 review: the earlier
        # version of this test reconstructed the identical string.)
        different = (
            "EVENT SLOT 04: Championship | Qualifying: Player One v Player Two [HD] "
            "@ 18 Sep 09:30 AM GMT-1"
        )
        assert _normalize(different) != _normalize(self.SLOT)
        result = find_candidate(
            stream_name=self.SLOT,
            candidates=[("uuid-a", different)],
            threshold=0.80,
        )
        assert result is not None
        assert 0.80 <= result.confidence < 1.0

    def test_gate_keeps_a_genuine_candidate_in_the_same_call(self):
        # The rollover candidate is dropped; a real duplicate elsewhere in
        # the same candidate list must still be returned.
        result = find_candidate(
            stream_name=self.SLOT,
            candidates=[
                ("uuid-yesterday", self.YESTERDAY),
                ("uuid-duplicate", self.SLOT),
            ],
            threshold=0.80,
        )
        assert result is not None
        assert result.candidate_channel_id == "uuid-duplicate"

    def test_gate_does_not_fire_without_an_airing_on_both_sides(self):
        # Names that state no airing are scored exactly as before — the
        # common case, and the reason the gate cannot regress it.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN SD")],
            threshold=0.80,
        )
        assert result is not None  # token_set_ratio("espn hd","espn sd") = 0.833

    def test_gate_does_not_fire_when_only_the_stream_has_an_airing(self):
        # Documented contract: both sides must state an airing. A name
        # with no run is never vetoed, so the gate can only change the
        # verdict on pairs that both date themselves.
        result = find_candidate(
            stream_name="ESPN HD",
            candidates=[("uuid-a", "ESPN HD @ 18 Sep 09:30 AM GMT-1")],
            threshold=0.80,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Microbench — informational, flag if > 5ms per call against 500 candidates.
# Per task spec: "Per-call latency on find_candidate for 100 candidates
# (rough microbench in test) — flag if > 5ms". We run 500 because that's
# the bulk-M3U dimension in the ADR/epic (200×500 candidates inline) and
# 5ms / 500 is a stricter bar than 5ms / 100.
# ---------------------------------------------------------------------------


class TestPerformanceMicrobench:
    """Documented per-call latency. Not a hard gate — informational.

    Marked ``slow`` so the wall-clock microbenchmark is excluded from the
    default CI gate (``pytest -m "not slow"``), where host contention could
    false-fail the 5ms soft cap. It still runs on an explicit
    ``pytest -m slow`` / full-suite invocation, preserving the latency check.
    """

    @pytest.mark.slow
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



class TestAiringKeyValidation:
    """PR #1016 review items 1 and 2: only VALID airing tokens form a key,
    a false prefix cannot hide a real trailing airing, bare zone labels are
    kept and non-zone suffixes are ignored."""

    @pytest.mark.parametrize("name", [
        "Channel 250 News 09:30",          # "50" is inside "250": not a day
        "Sports 24 Hour 09:30",            # "Hour" is not a month
        "Sky Sports F1 Live 20:00",        # no day/month at all
        "Sky Sports F1 UHD 20:00",
        "X 118 Sep 09:30",                 # day token incomplete / out of range
        "X 18 Sep 09:300",                 # clock continues into more digits
        "X 18 Sep 25:30",                  # no such hour
        "X 18 Sep 09:61",                  # no such minute
        "X 32 Sep 09:30",                  # no such day
        "X 18 Sep 13:30 PM",               # 12-hour clock with meridiem past 12
        "X 18 Foo 09:30",                  # not a month
    ])
    def test_invalid_runs_state_no_airing(self, name):
        assert airing_key(name) is None

    def test_a_false_prefix_does_not_hide_a_real_trailing_airing(self):
        assert airing_key("SPORT 01 LIVE 09:30 Final @ 18 Sep 09:30 AM GMT-1") == (
            "18 sep 09:30 am gmt-1"
        )

    def test_full_month_names_and_abbreviations_agree(self):
        assert airing_key("X 18 September 09:30") == airing_key("X 18 Sep 09:30")
        assert airing_key("X 18 Sept. 09:30") == airing_key("X 18 sep 09:30")

    def test_bare_zone_labels_are_part_of_the_key(self):
        assert airing_key("X @ 18 Sep 09:30 GMT") == "18 sep 09:30 gmt"
        assert airing_key("X @ 18 Sep 09:30 IST") == "18 sep 09:30 ist"
        assert airing_key("X @ 18 Sep 09:30 GMT") != airing_key("X @ 18 Sep 09:30 IST")

    def test_numeric_offsets_are_part_of_the_key(self):
        assert airing_key("X @ 18 Sep 09:30 UTC+05:30") == "18 sep 09:30 utc+05:30"
        assert airing_key("X @ 18 Sep 09:30 GMT-1") != airing_key("X @ 18 Sep 09:30 GMT+1")

    @pytest.mark.parametrize("suffix", ["HD2", "UHD", "4K", "RAW", "FHD", "backup"])
    def test_non_zone_suffixes_do_not_become_zones(self, suffix):
        assert airing_key(f"X @ 18 Sep 09:30 {suffix}") == airing_key("X @ 18 Sep 09:30")

    def test_regex_has_no_unbounded_whitespace_scan(self):
        # PR #1016 review item 6: the earlier shape led with an unanchored
        # ``\s*`` and retried across every internal whitespace run.
        from services.dedup_matcher import _AIRING_RE
        assert "\\s*" not in _AIRING_RE.pattern
        assert "\\s+" not in _AIRING_RE.pattern

    def test_padded_non_matching_input_stays_cheap(self):
        # Bounded operation check through the production wrapper: 500
        # distinct whitespace-padded names near the 512-char cap.
        import time
        names = [
            (f"CHANNEL {i}" + " " * 200 + "NEWS 24" + " " * 200 + f"{i % 10}:30")[:512]
            for i in range(500)
        ]
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            for name in names:
                assert airing_key(_normalize(name)) is None
            best = min(best, time.perf_counter() - started)
        assert best < 0.5, f"airing gate took {best:.3f}s for 500 padded names"


class TestAiringGateCounterexamples:
    """The reviewer's false-veto cases through ``find_candidate``: with no
    valid airing on one side the gate must not fire, and a suffix must not
    invent a zone."""

    def test_hour_is_not_a_month(self):
        result = find_candidate(
            stream_name="Sports 24 Hour 09:30",
            candidates=[("uuid-a", "Sports 24 Hour 10:30")],
            threshold=0.80,
        )
        assert result is not None and result.candidate_channel_id == "uuid-a"

    def test_quality_variants_without_an_airing_still_match(self):
        result = find_candidate(
            stream_name="Sky Sports F1 Live 20:00",
            candidates=[("uuid-a", "Sky Sports F1 UHD 20:00")],
            threshold=0.80,
        )
        assert result is not None

    def test_channel_number_is_not_a_day(self):
        result = find_candidate(
            stream_name="Channel 250 News 09:30",
            candidates=[("uuid-a", "Channel 250 News 09:30 HD")],
            threshold=0.80,
        )
        assert result is not None

    def test_a_false_prefix_still_lets_the_trailing_airing_gate(self):
        today = "SPORT 01 LIVE 09:30 Final: A - B @ 18 Sep 09:30 AM GMT-1"
        yesterday = "SPORT 01 LIVE 09:30 Final: C - D @ 17 Sep 09:30 AM GMT-1"
        assert find_candidate(today, [("uuid-y", yesterday)], threshold=0.80) is None
        same_day = "SPORT 01 LIVE 09:30 Final: A - B @ 18 Sep 09:30 AM GMT-1"
        assert find_candidate(today, [("uuid-s", same_day)], threshold=0.80) is not None

    def test_differing_bare_zones_are_different_airings(self):
        assert find_candidate(
            "X @ 18 Sep 09:30 GMT", [("uuid-a", "X @ 18 Sep 09:30 IST")], threshold=0.80,
        ) is None
        equal = find_candidate(
            "X @ 18 Sep 09:30 GMT", [("uuid-a", "X @ 18 Sep 09:30 GMT")], threshold=0.80,
        )
        assert equal is not None and equal.confidence == 1.0

    def test_a_non_zone_suffix_does_not_veto_a_duplicate(self):
        result = find_candidate(
            "X @ 18 Sep 09:30", [("uuid-a", "X @ 18 Sep 09:30 HD2")], threshold=0.80,
        )
        assert result is not None
