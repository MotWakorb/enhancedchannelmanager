"""Unit tests for the shared-engine EPG match key (bd-xxzxe).

EPG matching now flows through ECM's single ``NormalizationEngine`` via
``epg_match_key()``. The headline fix: channel-number noise in display names
("201 ESPN HD") no longer blocks a match against the guide's "ESPN".

These also pin the engine-vs-shim split for residual leading digits:
  - the EPG shim sweeps residual leading digits ("3ABN" -> "abn") for keys,
  - but the engine core name keeps the brand digit ("3ABN" -> "3ABN") so
    auto-creation never loses it.
"""
import pytest

from epg_matching import (
    build_epg_lookup,
    epg_match_key,
    find_epg_matches_with_lookup,
)
from normalization_engine import (
    NormalizationEngine,
    _tag_group_cache,
    clear_abbreviation_cache,
)
from tests.fixtures.factories import create_tag_group, create_tag


@pytest.fixture(autouse=True)
def clear_caches():
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    clear_abbreviation_cache()
    yield
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    clear_abbreviation_cache()


@pytest.fixture
def engine(test_session):
    """Engine with the tag groups the matching-mode key consults."""
    quality = create_tag_group(test_session, name="Quality Tags")
    for tag in ["HD", "FHD", "UHD", "4K", "SD"]:
        create_tag(test_session, group_id=quality.id, value=tag)
    tz = create_tag_group(test_session, name="Timezone Tags")
    for tag in ["EAST", "WEST"]:
        create_tag(test_session, group_id=tz.id, value=tag)
    return NormalizationEngine(test_session)


# ---------------------------------------------------------------------------
# epg_match_key: channel-number noise is stripped
# ---------------------------------------------------------------------------

class TestEpgMatchKeyStripsChannelNumberNoise:
    @pytest.mark.parametrize("name,expected", [
        ("201 ESPN", "espn"),
        ("201 ESPN HD", "espn"),
        ("ESPN (201)", "espn"),
        ("2.2 KATU", "katu"),
        ("535 | ESPN", "espn"),
    ])
    def test_strips(self, engine, name, expected):
        assert epg_match_key(engine, name) == expected


# ---------------------------------------------------------------------------
# epg_match_key: trailing number words are PART of the name -> preserved
# ---------------------------------------------------------------------------

class TestEpgMatchKeyPreservesTrailingNumbers:
    @pytest.mark.parametrize("name,expected", [
        ("Fox 5", "fox5"),
        ("CBS 2", "cbs2"),
        ("WGN 9", "wgn9"),
        ("Channel 4", "channel4"),
    ])
    def test_preserves(self, engine, name, expected):
        assert epg_match_key(engine, name) == expected


# ---------------------------------------------------------------------------
# engine-vs-shim split for residual leading digits
# ---------------------------------------------------------------------------

class TestEngineVsShimResidualDigits:
    def test_shim_sweeps_residual_leading_digit(self, engine):
        """The EPG shim flattens '3ABN' -> 'abn' for match keys."""
        assert epg_match_key(engine, "3ABN") == "abn"

    def test_engine_core_name_keeps_brand_digit(self, engine):
        """But the engine core name keeps '3ABN' (auto-creation must not lose it)."""
        assert engine.extract_core_name("3ABN") == "3ABN"


# ---------------------------------------------------------------------------
# semantic punctuation folding
# ---------------------------------------------------------------------------

class TestEpgMatchKeyPunctuation:
    def test_plus_folds_to_plus(self, engine):
        assert epg_match_key(engine, "AMC+") == "amcplus"

    def test_ampersand_folds_to_and(self, engine):
        assert epg_match_key(engine, "A&E") == "aande"

    def test_empty(self, engine):
        assert epg_match_key(engine, "") == ""


# ---------------------------------------------------------------------------
# end-to-end: a numbered channel matches the bare guide entry
# ---------------------------------------------------------------------------

class TestEndToEndNumberedChannelMatch:
    def test_201_espn_hd_matches_guide_espn(self, engine):
        epg_data = [
            {
                "id": 100,
                "name": "ESPN",
                "tvg_id": "ESPN.us",
                "epg_source": {"id": 1, "name": "S1"},
            },
        ]
        lookup = build_epg_lookup(epg_data, engine=engine)
        channel = {"id": 1, "name": "201 ESPN HD", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)

        assert result.best_match is not None
        assert result.best_match.epg_name == "ESPN"

    def test_legacy_path_without_engine_still_works(self):
        """No-engine call path stays green (back-compat)."""
        epg_data = [
            {
                "id": 100,
                "name": "ESPN",
                "tvg_id": "ESPN.us",
                "epg_source": {"id": 1, "name": "S1"},
            },
        ]
        lookup = build_epg_lookup(epg_data)
        channel = {"id": 1, "name": "ESPN", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup)
        assert result.best_match is not None
        assert result.best_match.epg_name == "ESPN"
