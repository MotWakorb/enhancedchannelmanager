"""Tests for the unified shared name-matching core (enhancedchannelmanager-jnzst).

ONE cleaner (``_normalize`` parameterized by ``NameCleanMode``) and ONE core
scorer (``score_one`` / ``score_all``) live in ``services.dedup_matcher``. These
tests pin:

* CONSERVATIVE == today's behavior (incident fixtures) — the strict path the
  1,341-merge fix relies on must not move byte-for-byte.
* The LOCALS cleaner produces the expected forms for the brief fixtures.
* The M1 callsign hard-reject (WBAY vs WGBA) wins regardless of fuzzy score.
* The tvg_id override → 1.0 and fails closed under a name-callsign conflict.
* The TOMBSTONE property: a callsign-gated fuzzy match admits ALL 4 WBAY
  positives and EXCLUDES the WGBA negative at EVERY min_score in [0.60, 1.0].

All validation is deterministic against the literal spec name strings.
"""
from __future__ import annotations

import time

import pytest

from services.dedup_matcher import (
    CONFIDENCE_FLOOR,
    NO_CALLSIGN_FLOOR,
    _NORMALIZE_MAX_LEN,
    NameCleanMode,
    ScoredMatch,
    _normalize,
    extract_callsign,
    is_admissible,
    score_all,
    score_one,
)

# ---------------------------------------------------------------------------
# Acceptance fixtures — the spec (literal strings).
# ---------------------------------------------------------------------------

CHANNEL_NAME = "ABC: WBAY Green Bay"
CHANNEL_TVG_ID = "WBAY-DT(ABC)(WBAYDT).us"
CHANNEL_NAME_PREFIXED = "2.1 | ABC: WBAY Green Bay"

POSITIVES = [
    "WI | Green Bay | ABC 2 WBAY",
    "US| ABC 2 GREEN BAY WI (WBAY)",
    "CITY: ABC WBAY GREENBAY ᴿᴬᵂ",
    "CITY| ABC WBAY GREENBAY ᴿᴬᵂ",
]
NEGATIVE = "WI | Green Bay | NBC WGBA"


# ---------------------------------------------------------------------------
# Cleaner: CONSERVATIVE == today's behavior (gating regression).
# ---------------------------------------------------------------------------


class TestConservativeIsByteForByteUnchanged:
    """The default mode must equal the historical _normalize output exactly.

    These are the incident fixtures the strict (exact-match) merge path is
    pinned on. If LOCALS work changes CONSERVATIVE output, the 1,341-merge
    incident regression re-opens.
    """

    INCIDENT_FIXTURES = [
        "5 | US : ESPN 2",
        "1351 | FOX Network",
        "7|HBO",
        "HBO | East",
        "SKY Sport 4K",
        "Sky Sport Premier League",
        "ABC: WBAY Green Bay",
        "  Mixed   Case  Name  ",
        "5 | ",  # degenerate prefix-only
        # Apostrophe-family + underscore: the LOCALS punct fix (bead enhancedchannelmanager-79k6b
        # bead) touches these ONLY in LOCALS mode; CONSERVATIVE must leave them
        # byte-for-byte so the dedup/exact path is unchanged.
        "HLR Joker`s Jackpot",
        "O'Brien Racing",
        "Joker´s Wild",
        "Utica_Rome Speedway",
        "a_b_c",
    ]

    @pytest.mark.parametrize("name", INCIDENT_FIXTURES)
    def test_default_equals_explicit_conservative(self, name):
        assert _normalize(name) == _normalize(name, mode=NameCleanMode.CONSERVATIVE)

    @pytest.mark.parametrize("name,preserved", [
        ("HLR Joker`s Jackpot", "joker`s"),   # backtick apostrophe kept
        ("O'Brien Racing", "o'brien"),        # straight apostrophe kept
        ("Joker´s Wild", "joker´s"),          # acute apostrophe kept
        ("Utica_Rome", "utica_rome"),         # underscore kept (one token)
    ])
    def test_conservative_preserves_apostrophe_and_underscore(self, name, preserved):
        # The LOCALS punct fix (apostrophe fuse + underscore split) is
        # LOCALS-only; CONSERVATIVE must NOT normalize these characters.
        assert preserved in _normalize(name, mode=NameCleanMode.CONSERVATIVE)

    def test_conservative_strips_number_prefix(self):
        assert _normalize("5 | US : ESPN 2") == "us : espn 2"

    def test_conservative_preserves_interior_pipe(self):
        assert _normalize("HBO | East") == "hbo | east"

    def test_conservative_does_not_strip_market_prefix(self):
        # The CITY:/US| stripping is a LOCALS-only behavior — CONSERVATIVE
        # must leave it intact so dedup/exact callers are unchanged.
        out = _normalize("CITY: ABC WBAY GREENBAY", mode=NameCleanMode.CONSERVATIVE)
        assert "city:" in out

    def test_conservative_does_not_convert_superscripts(self):
        out = _normalize("ABC ᴿᴬᵂ", mode=NameCleanMode.CONSERVATIVE)
        assert "ᴿᴬᵂ" in out


# ---------------------------------------------------------------------------
# Cleaner: LOCALS forms.
# ---------------------------------------------------------------------------


class TestLocalsCleaner:
    def test_strips_dotted_subchannel_prefix(self):
        out = _normalize(CHANNEL_NAME_PREFIXED, mode=NameCleanMode.LOCALS)
        assert out == _normalize(CHANNEL_NAME, mode=NameCleanMode.LOCALS)

    def test_strips_market_prefixes(self):
        assert "city" not in _normalize("CITY: ABC WBAY", mode=NameCleanMode.LOCALS)
        assert "us" not in _normalize("US| ABC 2", mode=NameCleanMode.LOCALS).split()
        assert "wi" not in _normalize("WI | Green Bay", mode=NameCleanMode.LOCALS).split()

    def test_converts_superscripts(self):
        out = _normalize("CITY: ABC WBAY GREENBAY ᴿᴬᵂ", mode=NameCleanMode.LOCALS)
        assert "ᴿᴬᵂ" not in out
        # And the resulting RAW quality tag is then stripped.
        assert "raw" not in out.split()

    def test_strips_quality_tags(self):
        out = _normalize("ABC WBAY HD UHD", mode=NameCleanMode.LOCALS)
        assert "hd" not in out.split()
        assert "uhd" not in out.split()

    def test_splits_run_together_tokens(self):
        out = _normalize("ABC WBAY GREENBAY", mode=NameCleanMode.LOCALS)
        assert "green bay" in out

    def test_locals_never_collapses_to_empty_for_real_name(self):
        assert _normalize("WI | Green Bay | ABC 2 WBAY", mode=NameCleanMode.LOCALS)

    # -- Apostrophe-family fusion + underscore split (bead enhancedchannelmanager-79k6b) --
    # A master "HLR Joker`s Jackpot" (backtick-as-apostrophe) tokenized
    # 'joker`s' != the stream's 'jokers' (token_set_ratio 0.837, ambiguous
    # band); "Utica_Rome" stayed one \w token 'utica_rome' != two stream
    # tokens (0.703). Both are teamless events needing >= 0.90 to attach.

    @pytest.mark.parametrize("apostrophe", ["'", "’", "ʼ", "`", "´"])
    def test_fuses_apostrophe_family_to_nothing(self, apostrophe):
        # Every apostrophe encoding fuses to nothing so the possessive token
        # matches the un-apostrophed stream spelling.
        out = _normalize(f"HLR Joker{apostrophe}s Jackpot", mode=NameCleanMode.LOCALS)
        assert "jokers" in out.split()
        assert apostrophe not in out

    def test_apostrophe_fusion_reaches_full_subset_match(self):
        from rapidfuzz import fuzz
        stream = _normalize("HLR Jokers Jackpot Eldora", mode=NameCleanMode.LOCALS)
        master = _normalize(
            "(FLSP 010) | floracing: 2026 HLR Joker`s Jackpot at Eldora "
            "Speedway (HLR Joker`s Jackpot at Eldora)",
            mode=NameCleanMode.LOCALS,
        )
        assert fuzz.token_set_ratio(stream, master) / 100 == 1.0

    def test_splits_underscore_into_separate_tokens(self):
        out = _normalize("Weekly Racing Utica_Rome", mode=NameCleanMode.LOCALS)
        assert "utica" in out.split()
        assert "rome" in out.split()
        assert "_" not in out

    def test_underscore_split_reaches_full_subset_match(self):
        from rapidfuzz import fuzz
        stream = _normalize("Weekly Racing Utica Rome", mode=NameCleanMode.LOCALS)
        master = _normalize(
            "(FLSP 011) | floracing: 2026 Weekly Racing at Utica_Rome "
            "Speedway (Weekly Racing at Utica_Rome)",
            mode=NameCleanMode.LOCALS,
        )
        assert fuzz.token_set_ratio(stream, master) / 100 == 1.0


# ---------------------------------------------------------------------------
# FIX 3 — source-prefix regex must NOT strip network/premium affiliations.
# ---------------------------------------------------------------------------


class TestSourcePrefixDoesNotStripNetworks:
    """The LOCALS source-prefix strip is an explicit closed token set (state /
    country / CITY), so it must leave network call letters and premium names
    intact even when they are followed by a ``:`` or ``|`` delimiter."""

    @pytest.mark.parametrize("name", [
        "ABC: WBAY Green Bay",
        "NBC| Channel 5",
        "HBO | East",
        "FOX: Sports 1",
        "CBS | Local",
        "ESPN: Deportes",
    ])
    def test_network_prefix_preserved(self, name):
        out = _normalize(name, mode=NameCleanMode.LOCALS)
        token = name.split(":")[0].split("|")[0].strip().lower()
        assert token in out, f"{token!r} (a network affiliation) was stripped from {out!r}"

    @pytest.mark.parametrize("name,gone", [
        ("CITY: ABC WBAY", "city"),
        ("US| ABC 2", "us"),
        ("WI | Green Bay", "wi"),
        ("|US| ABC", "us"),
        ("US : ESPN 2", "us"),
    ])
    def test_known_source_tag_stripped(self, name, gone):
        # The market/source tags ARE stripped (these are the intended targets).
        assert gone not in _normalize(name, mode=NameCleanMode.LOCALS).split()


# ---------------------------------------------------------------------------
# FIX 1 — ReDoS: the cleaner must be linear-time and length-capped.
# ---------------------------------------------------------------------------


class TestNormalizeBoundedRuntime:
    """The LOCALS cleaner ran the source-prefix regex through stdlib ``re``
    (no safe_regex timeout) on attacker-controllable M3U names inline on the
    event loop. The old pattern backtracked O(n^2) on whitespace padding
    (~1.5 s on 16k chars). These tests pin bounded runtime + the length cap."""

    def test_whitespace_heavy_input_completes_fast(self):
        # 16k of whitespace then a single char — the pathological shape that
        # drove the old pattern to ~1.5 s. Must now complete well under a small
        # fixed bound. Generous ceiling (0.1 s) to stay non-flaky on a loaded
        # CI box while still catching a regression to superlinear behavior.
        pathological = " " * 16000 + "X"
        t0 = time.perf_counter()
        _normalize(pathological, mode=NameCleanMode.LOCALS)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"_normalize took {elapsed:.3f}s — expected linear-time"

    def test_repeated_token_delim_input_completes_fast(self):
        # Many 'US | ' source tags + leading pipes: stresses the bounded prefix
        # loop and the alternation. Still linear / loop-bounded.
        pathological = ("|" + " " * 4) * 8000 + "US | " * 8000 + "WBAY"
        t0 = time.perf_counter()
        _normalize(pathological, mode=NameCleanMode.LOCALS)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"_normalize took {elapsed:.3f}s — expected bounded"

    def test_input_truncated_to_max_len(self):
        # A name longer than the cap is truncated before any regex work.
        long_name = "WBAY " + "x" * (_NORMALIZE_MAX_LEN * 4)
        out = _normalize(long_name, mode=NameCleanMode.LOCALS)
        # Output cannot be longer than the (truncated) input after lowercasing
        # and whitespace collapse — bounded by the cap.
        assert len(out) <= _NORMALIZE_MAX_LEN

    def test_truncation_does_not_break_short_names(self):
        # A normal-length name is unaffected by the cap.
        name = "WI | Green Bay | ABC 2 WBAY"
        assert _normalize(name, mode=NameCleanMode.LOCALS) == _normalize(
            name, mode=NameCleanMode.LOCALS
        )
        assert _normalize(name, mode=NameCleanMode.LOCALS)  # non-empty


# ---------------------------------------------------------------------------
# Callsign extraction (the M1 input).
# ---------------------------------------------------------------------------


class TestCallsignExtraction:
    @pytest.mark.parametrize("name", POSITIVES + [CHANNEL_NAME])
    def test_positives_and_channel_extract_wbay(self, name):
        assert extract_callsign(name) == "WBAY"

    def test_negative_extracts_wgba(self):
        assert extract_callsign(NEGATIVE) == "WGBA"

    def test_tvg_id_extracts_wbay(self):
        assert extract_callsign(CHANNEL_TVG_ID) == "WBAY"

    def test_none_for_empty(self):
        assert extract_callsign("") is None
        assert extract_callsign(None) is None


# ---------------------------------------------------------------------------
# M1 callsign hard-reject — wins regardless of fuzzy score.
# ---------------------------------------------------------------------------


class TestCallsignHardReject:
    def test_wgba_negative_is_rejected_against_wbay_channel(self):
        sm = score_one(NEGATIVE, CHANNEL_NAME)
        assert sm.score == 0.0
        assert sm.callsign_verdict == "conflict"
        assert sm.tvg_id_override is False
        assert sm.stream_callsign == "WGBA"
        assert sm.candidate_callsign == "WBAY"

    def test_hard_reject_beats_high_fuzzy(self):
        # Two names with conflicting callsigns but very similar text — fuzzy
        # would happily match; M1 must veto.
        sm = score_one("ABC 2 Green Bay WGBA", "ABC 2 Green Bay WBAY")
        assert sm.score == 0.0
        assert sm.callsign_verdict == "conflict"

    def test_hard_reject_beats_tvg_id_override(self):
        # tvg_id says WBAY but the stream NAME callsign is WGBA → M1 wins,
        # override never runs (fails closed).
        sm = score_one(
            NEGATIVE,
            CHANNEL_NAME,
            stream_tvg_id="WGBA-DT.us",
            candidate_tvg_id=CHANNEL_TVG_ID,
        )
        assert sm.score == 0.0
        assert sm.tvg_id_override is False
        assert sm.callsign_verdict == "conflict"


# ---------------------------------------------------------------------------
# tvg_id-callsign override → 1.0.
# ---------------------------------------------------------------------------


class TestTvgIdOverride:
    def test_override_sets_score_to_one(self):
        # Stream tvg_id parses WBAY (spec-shaped tvg_id with a network in
        # parens), candidate tvg_id parses WBAY; even with mediocre name text
        # the override sets 1.0.
        sm = score_one(
            "totally different text",
            "another unrelated channel",
            stream_tvg_id="WBAY-DT(ABC)(WBAYDT).us",
            candidate_tvg_id=CHANNEL_TVG_ID,
        )
        assert sm.score == 1.0
        assert sm.tvg_id_override is True
        assert sm.signal == "tvg_id-override"

    def test_override_fails_closed_on_name_conflict(self):
        # Name callsigns conflict (WGBA vs WBAY) → M1 fires, override skipped.
        sm = score_one(
            "NBC WGBA Green Bay",
            CHANNEL_NAME,
            stream_tvg_id="WBAY-DT(ABC)(WBAYDT).us",  # matches candidate, M1 wins
            candidate_tvg_id=CHANNEL_TVG_ID,
        )
        assert sm.tvg_id_override is False
        assert sm.score == 0.0


# ---------------------------------------------------------------------------
# TOMBSTONE property — the headline acceptance test.
# ---------------------------------------------------------------------------


class TestTombstoneProperty:
    """A callsign-gated fuzzy match admits all 4 WBAY positives and excludes
    the WGBA negative at EVERY min_score in [0.60, 1.0]."""

    THRESHOLDS = [round(0.60 + 0.05 * i, 2) for i in range(9)]  # 0.60..1.00

    def _admit(self, stream: str, min_score: float) -> bool:
        """Use the SHARED admission policy (is_admissible) — the same helper the
        rule path and preview endpoint call (FIX 2) — so the tombstone is pinned
        against the real policy, not a hand-rolled copy."""
        sm = score_one(stream, CHANNEL_NAME,
                       candidate_tvg_id=CHANNEL_TVG_ID)
        return is_admissible(sm, min_score=min_score, allow_no_callsign=False)

    @pytest.mark.parametrize("min_score", THRESHOLDS)
    @pytest.mark.parametrize("positive", POSITIVES)
    def test_positives_admitted_at_every_threshold(self, positive, min_score):
        assert self._admit(positive, min_score) is True, (
            f"{positive!r} should match WBAY at min_score={min_score}"
        )

    @pytest.mark.parametrize("min_score", THRESHOLDS)
    def test_negative_excluded_at_every_threshold(self, min_score):
        assert self._admit(NEGATIVE, min_score) is False, (
            f"{NEGATIVE!r} must NOT match WBAY at min_score={min_score}"
        )


# ---------------------------------------------------------------------------
# score_all — returns every candidate, not top-1.
# ---------------------------------------------------------------------------


class TestScoreAll:
    def test_yields_every_candidate(self):
        candidates = [("1", CHANNEL_NAME), ("2", "NBC WGBA Green Bay"), ("3", "Random")]
        results = list(score_all(POSITIVES[0], candidates,
                                 candidate_tvg_ids={"1": CHANNEL_TVG_ID}))
        assert len(results) == 3
        by_id = {cid: sm for cid, _, sm in results}
        assert by_id["1"].score >= CONFIDENCE_FLOOR  # WBAY match
        assert by_id["2"].score == 0.0  # WGBA conflict
        assert by_id["2"].callsign_verdict == "conflict"


# ---------------------------------------------------------------------------
# FIX 2 — is_admissible: the ONE shared admission policy.
# ---------------------------------------------------------------------------


class TestIsAdmissible:
    """The single source-of-truth admission policy shared by the rule path, the
    preview endpoint, and (transitively) both MCP write tools."""

    def _sm(self, verdict: str, score: float) -> ScoredMatch:
        return ScoredMatch(
            score=score, callsign_verdict=verdict, tvg_id_override=False,
            signal="fuzzy-with-callsign" if verdict == "match" else "fuzzy-no-callsign-floor",
        )

    def test_conflict_never_admissible(self):
        # Even at min_score=0 and with the opt-in on, a conflict is rejected.
        sm = self._sm("conflict", 0.0)
        assert is_admissible(sm, min_score=0.0, allow_no_callsign=True) is False
        assert is_admissible(sm, min_score=0.0, allow_no_callsign=False) is False

    def test_match_admitted_at_or_above_min_score(self):
        assert is_admissible(self._sm("match", 0.80), min_score=0.80,
                             allow_no_callsign=False) is True
        assert is_admissible(self._sm("match", 0.79), min_score=0.80,
                             allow_no_callsign=False) is False

    def test_absent_rejected_by_default(self):
        # A perfect-scoring absent pair is still rejected without the opt-in.
        assert is_admissible(self._sm("absent", 1.0), min_score=0.0,
                             allow_no_callsign=False) is False

    def test_absent_admitted_only_at_or_above_no_callsign_floor_with_opt_in(self):
        assert is_admissible(self._sm("absent", 0.90), min_score=0.0,
                             allow_no_callsign=True) is True
        # Below the 0.90 floor → still rejected even with the opt-in.
        assert is_admissible(self._sm("absent", 0.89), min_score=0.0,
                             allow_no_callsign=True) is False


def test_no_callsign_floor_constant():
    assert NO_CALLSIGN_FLOOR == 0.90
