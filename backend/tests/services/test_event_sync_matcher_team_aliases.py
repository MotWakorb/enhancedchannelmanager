"""
Unit tests for the operator team-alias dictionary in the event matcher
(bead enhancedchannelmanager-ti939.4.2).

The alias layer adds KNOWN equivalences ("Red Devils" == "Manchester United")
to the team-token check — both the hard-reject path (a would-be
team_token_conflict between aliased spellings becomes agreement) and the
boost path (team AGREE via alias lifts a lexically-distant pair to the
attach band) — WITHOUT touching the fuzzy threshold or any admission floor.

Safety contract pinned here:

* aliases are MONOTONIC — they can only raise team similarity, never lower
  it, so an alias can never CREATE a disagree that didn't exist;
* two teams resolving to DIFFERENT alias groups score byte-identically to
  the no-alias run (different-group membership is NOT a conflict signal);
* the qualifier rail (women's/U21/reserves) outranks aliases;
* an empty/absent dictionary is byte-identical to pre-alias behavior.

These tests use FIXTURE aliases only. The shipped dictionary is empty by
design — operator aliases are corpus-gated (docs/event_sync.md).
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
import pytz

from services.event_sync_matcher import (
    BAND_ATTACH,
    BAND_REJECT,
    DEFAULT_EVENT_TIMEZONE,
    REJECT_TEAM_TOKEN_CONFLICT,
    TEAM_VERDICT_AGREE,
    TEAM_VERDICT_CONFLICT,
    _team_similarity,
    build_team_alias_index,
    match_streams,
    normalize_alias_term,
    score_pair,
)

_ET = pytz.timezone(DEFAULT_EVENT_TIMEZONE)

# Fixed "now" for deterministic year inference (mirrors the sibling matcher
# suite's live-capture anchor).
_NOW = _ET.localize(datetime(2026, 7, 11, 12, 0, 0))

# One fixture alias group: nickname <-> canonical <-> initialism.
_MUFC_GROUP = ["Red Devils", "Manchester United", "MUFC"]

_NICKNAME = "Sky 02: Red Devils vs. Chelsea @ 11 Jul 10:00 AM ET"
_CANONICAL = "Peacock 09: Manchester United vs. Chelsea @ 11 Jul 10:00 AM ET"


class TestNormalizeAliasTerm:
    def test_lowercases_and_tokenizes(self):
        assert normalize_alias_term("Red Devils") == ("red", "devils")

    def test_strips_generic_club_suffix_tokens(self):
        # Same normalization pipeline as the team layer: "FC" carries no
        # identity, so "Manchester United FC" keys like "Manchester United".
        assert normalize_alias_term("Manchester United FC") == (
            "manchester", "united",
        )
        assert normalize_alias_term("Manchester United") == (
            "manchester", "united",
        )

    def test_fuses_apostrophe_family(self):
        # Mirrors _team_tokens' apostrophe fusing: both spellings of the
        # same possessive normalize to one token.
        assert normalize_alias_term("Joker's XI") == normalize_alias_term(
            "Joker`s XI"
        )

    def test_qualifier_tokens_are_stripped(self):
        # Qualifiers are class-checked by the rail BEFORE aliases apply, so
        # they never participate in the alias key.
        assert normalize_alias_term("Barcelona W") == ("barcelona",)

    def test_identity_free_term_normalizes_empty(self):
        assert normalize_alias_term("FC") == ()
        assert normalize_alias_term("  ") == ()


class TestBuildTeamAliasIndex:
    def test_maps_every_term_to_its_group(self):
        index = build_team_alias_index([_MUFC_GROUP, ["Spurs", "Tottenham"]])
        assert index[("red", "devils")] == index[("manchester", "united")]
        assert index[("mufc",)] == index[("red", "devils")]
        assert index[("spurs",)] == index[("tottenham",)]
        assert index[("spurs",)] != index[("mufc",)]

    def test_skips_identity_free_terms(self):
        index = build_team_alias_index([["FC", "Manchester United"]])
        assert () not in index
        assert ("manchester", "united") in index

    def test_first_group_wins_on_cross_group_duplicate(self):
        # The router rejects cross-group duplicates at save time; the index
        # builder stays deterministic anyway (first group wins).
        # NB: bare "B"/"W" would be qualifier tokens (reserves/women) and
        # normalize empty — real words keep this fixture about duplicates.
        index = build_team_alias_index([["Spurs", "Alpha"], ["Spurs", "Beta"]])
        assert index[("spurs",)] == index[("alpha",)]
        assert index[("spurs",)] != index[("beta",)]

    def test_empty_input_builds_empty_index(self):
        assert build_team_alias_index([]) == {}
        assert build_team_alias_index(None) == {}


class TestAliasRescuesHardReject:
    def test_nickname_pair_hard_rejects_without_alias(self):
        # Baseline: with no dictionary this is the team_token_conflict shape
        # the alias exists to fix.
        result = score_pair(_NICKNAME, _CANONICAL, now=_NOW, team_aliases=())
        assert result.band == BAND_REJECT
        assert result.team_verdict == TEAM_VERDICT_CONFLICT
        assert REJECT_TEAM_TOKEN_CONFLICT in result.reject_reasons

    @pytest.mark.parametrize(
        ("name_a", "name_b"),
        [(_NICKNAME, _CANONICAL), (_CANONICAL, _NICKNAME)],
        ids=["nickname-vs-canonical", "canonical-vs-nickname"],
    )
    def test_alias_turns_conflict_into_agree_both_directions(
        self, name_a, name_b
    ):
        result = score_pair(
            name_a, name_b, now=_NOW, team_aliases=[_MUFC_GROUP]
        )
        assert result.band == BAND_ATTACH, (
            f"score={result.score:.3f} verdict={result.team_verdict} "
            f"reasons={result.reject_reasons}"
        )
        assert result.team_verdict == TEAM_VERDICT_AGREE

    def test_alias_boost_lifts_score_above_fuzzy(self):
        # The boost path: team AGREE via alias feeds max(fuzzy, team_score),
        # so the pair reaches the attach band even though the titles share
        # almost no words.
        result = score_pair(
            _NICKNAME, _CANONICAL, now=_NOW, team_aliases=[_MUFC_GROUP]
        )
        assert result.team_score == 1.0
        assert result.score == 1.0
        assert result.score > result.fuzzy_score


class TestAliasNeverCreatesDisagree:
    # Team-side pairs spanning agree/uncertain/conflict shapes.
    _TEAM_PAIRS = [
        ("Manchester United", "Manchester United"),
        ("Man Utd", "Manchester United"),
        ("Red Devils", "Manchester United"),
        ("Chelsea", "Everton"),
        ("Austria", "Australia"),
        ("New York Rangers", "New York Knicks"),
        ("Barcelona W", "Barcelona"),
    ]

    def test_similarity_is_monotonic_under_any_dictionary(self):
        # An alias can only ADD equivalence: for every pair, similarity with
        # the dictionary is >= similarity without it.
        index = build_team_alias_index(
            [_MUFC_GROUP, ["Chelsea", "The Blues"], ["Austria"]]
        )
        for team_a, team_b in self._TEAM_PAIRS:
            base = _team_similarity(team_a, team_b)
            aliased = _team_similarity(team_a, team_b, alias_index=index)
            assert aliased >= base, (team_a, team_b, base, aliased)

    def test_different_alias_groups_score_byte_identical_to_no_alias(self):
        # Membership in DIFFERENT groups is not a conflict signal: the pair
        # scores exactly as if there were no dictionary at all.
        groups = [["Chelsea", "The Blues"], ["Everton", "The Toffees"]]
        pair = (
            "Fubo 07 : Chelsea vs. Brentford @ 11 Jul 10:00 AM ET",
            "Peacock 23: Everton vs. Brentford @ 11 Jul 10:00 AM ET",
        )
        without = score_pair(*pair, now=_NOW, team_aliases=())
        with_groups = score_pair(*pair, now=_NOW, team_aliases=groups)
        assert with_groups == without
        assert with_groups.team_verdict == TEAM_VERDICT_CONFLICT

    def test_empty_dictionary_is_byte_identical(self):
        pairs = [
            (_NICKNAME, _CANONICAL),
            (
                "P 01: Liverpool vs Burnley @ 11 Jul 03:00 PM ET",
                "Q 01: Liverpool v. Burnley @ 11 Jul 03:00 PM ET",
            ),
            (
                "Peacock 02: Tour de France: Stage 8 @ 11 Jul 06:30 AM ET",
                "FloSports 01 : Tour de France Stage 8 @ 11 Jul 06:30 AM ET",
            ),
        ]
        for name_a, name_b in pairs:
            default = score_pair(name_a, name_b, now=_NOW, team_aliases=())
            empty = score_pair(name_a, name_b, now=_NOW, team_aliases=[])
            assert default == empty

    def test_qualifier_rail_outranks_alias(self):
        # Women's side vs men's side stays a hard conflict even when both
        # spellings live in one alias group — the qualifier-class rail runs
        # BEFORE the alias lookup.
        result = score_pair(
            "Sky 02: Red Devils W vs. Chelsea W @ 11 Jul 10:00 AM ET",
            _CANONICAL,
            now=_NOW,
            team_aliases=[_MUFC_GROUP],
        )
        assert result.band == BAND_REJECT
        assert result.team_verdict == TEAM_VERDICT_CONFLICT


class TestSettingsBackedDefault:
    def test_default_none_loads_operator_dictionary_from_settings(self):
        class _Settings:
            event_sync_team_aliases = [
                {"terms": _MUFC_GROUP, "note": "fixture"},
            ]

        with patch("config.get_settings", return_value=_Settings()):
            result = score_pair(_NICKNAME, _CANONICAL, now=_NOW)
        assert result.band == BAND_ATTACH
        assert result.team_verdict == TEAM_VERDICT_AGREE

    def test_explicit_empty_overrides_settings(self):
        class _Settings:
            event_sync_team_aliases = [
                {"terms": _MUFC_GROUP, "note": "fixture"},
            ]

        with patch("config.get_settings", return_value=_Settings()):
            result = score_pair(_NICKNAME, _CANONICAL, now=_NOW, team_aliases=())
        assert result.band == BAND_REJECT

    def test_settings_load_failure_fails_open_to_no_aliases(self):
        with patch("config.get_settings", side_effect=RuntimeError("boom")):
            result = score_pair(_NICKNAME, _CANONICAL, now=_NOW)
        assert result.band == BAND_REJECT
        assert result.team_verdict == TEAM_VERDICT_CONFLICT


class TestMatchStreamsThreading:
    def test_alias_dictionary_reaches_the_stream_vs_masters_path(self):
        results = match_streams(
            [_NICKNAME],
            [_CANONICAL, "Peacock 40: NO EVENT"],
            now=_NOW,
            team_aliases=[_MUFC_GROUP],
        )
        assert len(results) == 1
        top = results[0].candidates[0]
        assert top.master_name == _CANONICAL
        assert top.band == BAND_ATTACH
        assert top.team_verdict == TEAM_VERDICT_AGREE

    def test_without_alias_same_pair_stays_rejected(self):
        results = match_streams(
            [_NICKNAME],
            [_CANONICAL],
            now=_NOW,
            team_aliases=(),
        )
        top = results[0].candidates[0]
        assert top.band == BAND_REJECT
        assert REJECT_TEAM_TOKEN_CONFLICT in top.reject_reasons
