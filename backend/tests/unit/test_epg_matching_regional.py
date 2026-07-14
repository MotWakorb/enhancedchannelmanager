"""Timezone/region-aware EPG matching (bead vznut.2, spike vznut.3).

These tests were written TESTS-FIRST as characterization tests locking the
CURRENT (buggy) behavior of ``epg_matching`` for regional ("...West" ->
"(Pacific)") variants, then the Hybrid A+C fix flips the buggy-behavior
assertions to the new expected behavior. Each characterization block is
labelled either:

  * CHARACTERIZATION (flips): documents pre-fix behavior; the assertion is
    updated to the post-fix expectation when Hybrid A+C lands. The comment
    records what the pre-fix assertion was so the flip is a deliberate,
    reviewable act (code-review precondition).
  * INVARIANT GUARD (stays green): pins behavior that must NOT change across
    the fix (non-regional nets, East->East, base-only, genuine acronyms). If
    one of these ever goes red, the region change has over-reached.

Root cause recap (see bead vznut / vznut.2): a "USA Network West" channel
had its ``epg_data_id`` pointing at the East feed's guide row because the
correct "(Pacific)" row either was never a candidate (short-name nets) or lost
on confidence/token scoring, and the lone regional tie-break (``has_regional``)
was latently backwards. West == Pacific (US West-coast feeds run Pacific time);
Central and Mountain are their own regions.
"""
import pytest

from epg_matching import (
    EPGMatchWithScore,
    build_epg_lookup,
    detect_region,
    extract_paren_acronym,
    find_epg_matches_with_lookup,
    _sort_matches,
)
from normalization_engine import (
    NormalizationEngine,
    _tag_group_cache,
    clear_abbreviation_cache,
)
from tests.fixtures.factories import create_tag_group, create_tag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    """Engine with the tag groups the matching-mode key consults.

    Mirrors production: the "Timezone Tags" group carries EAST/WEST only —
    NOT PACIFIC (that is the whole point of the bug and the fix). PACIFIC is
    collapsed by epg_matching's own match-only region sweep, never by the tag
    group (which also renames displayed channel names).
    """
    quality = create_tag_group(test_session, name="Quality Tags")
    for tag in ["HD", "FHD", "UHD", "4K", "SD"]:
        create_tag(test_session, group_id=quality.id, value=tag)
    tz = create_tag_group(test_session, name="Timezone Tags")
    for tag in ["EAST", "WEST"]:
        create_tag(test_session, group_id=tz.id, value=tag)
    return NormalizationEngine(test_session)


def _epg(id, name, tvg_id, source=None):
    return {
        "id": id,
        "name": name,
        "tvg_id": tvg_id,
        "epg_source": source or {"id": 1, "name": "JESmann"},
    }


def _flat(text):
    return "".join(c for c in text.lower() if c.isalnum())


def _base_name(channel_name):
    """Network base name = channel name without the trailing region word."""
    return channel_name.rsplit(" West", 1)[0]


def _regional_pair_lookup(engine, channel_name, pacific_tvg_id):
    """Build a lookup holding the East (base) feed + the (Pacific) feed for one
    network, exactly the ambiguity a "...West" channel faces in production."""
    base = _base_name(channel_name)
    east = _epg(1, base, f"{_flat(base)}.us")
    pacific = _epg(2, f"{base} (Pacific)", pacific_tvg_id)
    return build_epg_lookup([east, pacific], engine=engine)


# The 24 West channels from the KNM debug bundle (20260714) whose JESmann guide
# carries a genuine (Pacific) row — the real (Pacific) tvg_ids, verbatim from
# the bundle's channels.csv. Each is (channel_name, pacific_tvg_id).
KNM_WEST_PACIFIC = [
    ("AMC West", "AMC(Pacific)(AMCP).us"),
    ("Discovery West", "DiscoveryChannel(Pacific)(DSCP).us"),
    ("E! West", "E!EntertainmentTelevision(Pacific)(EP).us"),
    ("FX West", "FX(Pacific)(FXP).us"),
    ("FXX West", "FXXHD(Pacific)(FXXPHD).us"),
    ("Hallmark West", "HallmarkChannel(Pacific)(HALLP).us"),
    ("History West", "History(Pacific)(HISTP).us"),
    ("ION Television West", "IONTelevisionSatelliteFeed(Pacific)HD(ION)(IONSPHD).us"),
    ("Lifetime West", "Lifetime(Pacific)(LIFEP).us"),
    ("Oxygen West", "OxygenTrueCrime(Pacific)(OXYGENP).us"),
    ("Syfy West", "Syfy(Pacific)(SYFYP).us"),
    ("TBS West", "TBSHD(Pacific)(TBSHDP).us"),
    ("TNT West", "TNT(Pacific)(TNTP).us"),
    ("USA Network West", "USANetwork(Pacific)(USAP).us"),
    ("Cinemax West", "Cinemax(Pacific)(MAXP).us"),
    ("Flix West", "Flix(Pacific)(FLIXP).us"),
    ("HBO Comedy West", "HBOComedy(Pacific)(HBOCP).us"),
    ("HBO West", "HBO(Pacific)(HBOP).us"),
    ("Showtime Showcase West", "ShowtimeShowcase(Pacific)(SHOCSEP).us"),
    ("Starz Cinema West", "StarzCinema(Pacific)(STZCIP).us"),
    ("Starz Comedy West", "StarzComedy(Pacific)(STZCP).us"),
    ("Starz Encore Classic West", "StarzEncoreClassic(Pacific)(STZECLP).us"),
    ("Starz Encore West", "StarzEncore(Pacific)(STZENCP).us"),
    ("Starz Kids & Family West", "StarzKids&Family(Pacific)(STZKP).us"),
]


# ===========================================================================
# CT1 — CHARACTERIZATION (flips): the lone regional tie-break prefers the
# NON-regional entry. Two otherwise-identical exact candidates, the regional
# one alphabetically FIRST; pre-fix ``has_regional`` demotes it below the
# non-regional entry despite the alphabetical order.
# ===========================================================================

class TestRegionalTieBreakDirection:
    def _two_tied_exact(self):
        # Same confidence, exact, no league/source; region word only in one
        # name; the regional name sorts alphabetically FIRST ("aaa" < "zzz")
        # so any preference for the non-regional "ZZZ" can only come from the
        # region tie-break, not from the alphabetical fallback.
        regional = EPGMatchWithScore(
            epg_id=1, epg_name="AAA Pacific", tvg_id="", epg_source={"id": 1},
            confidence=25, match_type="exact",
        )
        nonregional = EPGMatchWithScore(
            epg_id=2, epg_name="ZZZ", tvg_id="", epg_source={"id": 1},
            confidence=25, match_type="exact",
        )
        return [regional, nonregional]

    def test_tiebreak_no_channel_region_falls_to_alphabetical(self):
        # PRE-FIX (characterization): the backwards ``has_regional`` tie-break
        # demoted the regional entry, so "ZZZ" won despite "AAA Pacific" being
        # alphabetically first:
        #     assert ordered[0].epg_name == "ZZZ"
        # POST-FIX: ``has_regional`` is replaced by region-consistency, which is
        # INERT when the channel has no region (both rank 0), so the tie now
        # falls through to the alphabetical fallback -> "AAA Pacific" first.
        ordered = _sort_matches(self._two_tied_exact(), None, "qqq")
        assert ordered[0].epg_name == "AAA Pacific"


# ===========================================================================
# CT2 — CHARACTERIZATION (flips): a short-name West net produces NO Pacific
# candidate at all pre-fix (the (Pacific) key differs and the prefix scan is
# gated at len>=4), so no tie-break can rescue it.
# ===========================================================================

class TestShortNamePacificCandidate:
    def test_amc_west_prefers_pacific(self, engine):
        lookup = _regional_pair_lookup(engine, "AMC West", "AMC(Pacific)(AMCP).us")
        channel = {"id": 1, "name": "AMC West", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)

        matched_ids = {m.epg_id for m in result.matches}
        # PRE-FIX (characterization): "AMC" is 3 chars, the (Pacific) row keyed
        # to "amcpacific" != "amc" and the prefix scan is gated at len>=4, so
        # the Pacific row (id=2) was never even a candidate and the East row
        # (id=1) was the only/best match:
        #     assert 2 not in matched_ids
        #     assert result.best_match.epg_id == 1
        # POST-FIX: the match-only region collapse makes "AMC (Pacific)" an
        # EXACT candidate and the region-consistency tie-break selects it.
        assert 2 in matched_ids
        assert result.best_match.epg_id == 2
        assert "Pacific" in result.best_match.epg_name


# ===========================================================================
# CT3 — CHARACTERIZATION (flips): a "(Pacific)" parenthetical mis-extracts as
# an acronym and adds a spurious extra identity token, penalising the correct
# Pacific row and mis-indexing it under by_acronym['pacific'].
# ===========================================================================

class TestPacificParenNotAnAcronym:
    def test_extract_paren_acronym_on_region_paren(self):
        # PRE-FIX (characterization): the first parenthetical wins, so the
        # region word "Pacific" is returned as the "acronym":
        #     assert extract_paren_acronym("USANetwork(Pacific)(USAP).us") == "pacific"
        # POST-FIX: a region parenthetical yields NO acronym (the entry is
        # already an exact candidate via its collapsed key and must stay
        # token-symmetric with its base sibling).
        assert extract_paren_acronym("USANetwork(Pacific)(USAP).us") is None

    def test_genuine_acronym_still_extracted(self):
        # INVARIANT: a non-region parenthetical still yields its acronym.
        assert extract_paren_acronym("OprahWinfreyNetwork(OWN).us") == "own"

    def test_original_first_paren_semantics_preserved(self):
        # Review #3: region-suppression is the ONLY intended behavior change to
        # this load-bearing fn (bd-6rz70). The original semantics stand: the
        # FIRST parenthetical is used, and a too-short first paren yields None
        # (it does NOT scan on to a later, longer paren).
        assert extract_paren_acronym("(A)(BCD)") is None
        assert extract_paren_acronym("Net(HGTV)(extra).us") == "hgtv"

    def test_pacific_row_not_indexed_under_pacific_acronym(self, engine):
        lookup = build_epg_lookup(
            [_epg(2, "USA Network (Pacific)", "USANetwork(Pacific)(USAP).us")],
            engine=engine,
        )
        entry = lookup["all_entries"][0]
        # PRE-FIX (characterization):
        #     assert "pacific" in lookup["by_acronym"]
        #     assert "pacific" in entry["match_tokens"]
        # POST-FIX: "pacific" is neither an acronym key nor an identity token.
        assert "pacific" not in lookup["by_acronym"]
        assert "pacific" not in entry["match_tokens"]


# ===========================================================================
# CT4 — INVARIANT GUARDS (stay green throughout the fix)
# ===========================================================================

class TestInvariantGuards:
    def test_nonregional_net_unaffected(self, engine):
        # ESPN has no region; it must match ESPN, not anything else.
        lookup = build_epg_lookup(
            [_epg(1, "ESPN", "ESPN.us"), _epg(2, "ESPN2", "ESPN2.us")],
            engine=engine,
        )
        channel = {"id": 1, "name": "ESPN", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_name == "ESPN"

    def test_east_channel_prefers_east_not_pacific(self, engine):
        # An East channel must NOT be pulled to the Pacific row (East != West).
        lookup = build_epg_lookup(
            [
                _epg(1, "USA Network", "USANetwork(USA).us"),
                _epg(2, "USA Network (Pacific)", "USANetwork(Pacific)(USAP).us"),
            ],
            engine=engine,
        )
        channel = {"id": 1, "name": "USA Network East", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_id == 1

    def test_west_channel_with_only_base_still_matches_base(self, engine):
        # When no Pacific row exists, the West channel still links the base row.
        lookup = build_epg_lookup([_epg(1, "AMC", "AMC.us")], engine=engine)
        channel = {"id": 1, "name": "AMC West", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_id == 1

    def test_genuine_acronym_still_resolves(self, engine):
        # OWN channel resolves via the parenthetical acronym in the tvg_id.
        lookup = build_epg_lookup(
            [_epg(1, "Oprah Winfrey Network", "OprahWinfreyNetwork(OWN).us")],
            engine=engine,
        )
        channel = {"id": 1, "name": "OWN", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_id == 1


# ===========================================================================
# CT5 — CHARACTERIZATION (flips): KNM 24-channel integration. Each West
# channel is matched against a lookup holding its own East + (Pacific) rows.
# ===========================================================================

class TestKnmWestPacificIntegration:
    def _pacific_hits(self, engine):
        hits = 0
        misses = []
        for channel_name, pac_tvg in KNM_WEST_PACIFIC:
            lookup = _regional_pair_lookup(engine, channel_name, pac_tvg)
            channel = {"id": 1, "name": channel_name, "streams": []}
            result = find_epg_matches_with_lookup(
                channel, [], lookup, engine=engine
            )
            if result.best_match and result.best_match.epg_id == 2:
                hits += 1
            else:
                got = result.best_match.epg_name if result.best_match else None
                misses.append((channel_name, got))
        return hits, misses

    def test_west_channels_pick_pacific(self, engine):
        hits, misses = self._pacific_hits(engine)
        total = len(KNM_WEST_PACIFIC)
        # PRE-FIX (characterization) locked ``hits == 0`` — every West channel
        # linked its East feed (the reported bug). POST-FIX all 24 pick Pacific
        # (spike proved 24/24; the AC bar is >=90%, so the stricter 24/24
        # assertion below subsumes it).
        assert hits == total, f"expected all {total} Pacific; misses={misses}"


# ===========================================================================
# detect_region: encoding/detection + edge guards (spike vznut.3 sub-decisions)
# ===========================================================================

class TestDetectRegion:
    @pytest.mark.parametrize("tvg_id,name,expected", [
        # tvg_id parenthetical is authoritative and scanned before the acronym.
        ("USANetwork(Pacific)(USAP).us", "USA Network (Pacific)", "W"),
        ("AMC(Pacific)(AMCP).us", "AMC (Pacific)", "W"),
        # Trailing name word when the tvg_id has no region paren.
        ("", "USA Network West", "W"),
        ("", "USA Network East", "E"),
        # Review #1: a trailing quality/format token must not mask the region.
        ("", "AMC West HD", "W"),
        ("", "AMC West FHD", "W"),
        ("", "AMC West UHD", "W"),
        ("", "AMC West 4K", "W"),
        ("", "AMC West 1080p", "W"),
        ("", "AMC West HD FHD", "W"),
        ("", "USA Network East HD", "E"),
        # Re-review: a bare trailing digit after the region word is also noise
        # ("AMC West 2" -> region from "West"), matching the old [A-Za-z]+
        # regex's implicit digit-drop without the 4K-in-quality-token bug.
        ("", "AMC West 2", "W"),
        # ...but the digit skip only looks PAST the number for a real region;
        # it never manufactures one ("ESPN 2" -> skip "2" -> "ESPN" -> None).
        ("", "ESPN 2", None),
        # ...and a quality token alone is still not a region.
        ("", "AMC HD", None),
        ("", "Comedy Central", "C"),
        ("", "Something Mountain", "M"),
        # West == Pacific (both "W"); Mountain and Central are their OWN regions.
        ("", "Foo Pacific", "W"),
        # Non-regional networks -> None.
        ("ESPN.us", "ESPN", None),
        ("", "CNN", None),
    ])
    def test_detect(self, tvg_id, name, expected):
        assert detect_region(tvg_id, name) == expected

    @pytest.mark.parametrize("name", [
        "PBS Eastern",           # adjective, not a region tag
        "Western Channel",       # adjective as leading word
        "Starz Encore Westerns", # plural, whole-word guard
        "West Coast News",       # leading descriptor, not a trailing tag
    ])
    def test_adjective_and_leading_words_are_not_regions(self, name):
        assert detect_region("", name) is None

    def test_tvg_paren_beats_name_word(self):
        # tvg_id region wins even when the name's trailing word disagrees.
        assert detect_region("Net(Pacific)(NETP).us", "Net East") == "W"


# ===========================================================================
# Band preservation: the region tie-break only reorders genuine ties; it
# never lets a region-consistent candidate outrank a country/exact-preferred
# one (invariant guard, QA T5 / spike band-preserving semantics).
# ===========================================================================

class TestRegionTieBreakIsBandPreserving:
    def test_country_match_still_outranks_region_only(self, engine):
        # A US channel with a region: the EAST row matches its country (+40),
        # the Pacific row does not. Country must dominate the region tie-break.
        lookup = build_epg_lookup(
            [
                _epg(1, "AMC", "AMC.us"),                       # US
                _epg(2, "AMC (Pacific)", "AMC(Pacific)(AMCP).ca"),  # CA
            ],
            engine=engine,
        )
        channel = {
            "id": 1, "name": "AMC West", "tvg_id": "",
            "streams": [10],
        }
        streams = [{"id": 10, "name": "US: AMC West", "channel_group_name": "US"}]
        result = find_epg_matches_with_lookup(
            channel, streams, lookup, engine=engine
        )
        # East (id=1) wins on the +40 country band despite the region conflict.
        assert result.best_match.epg_id == 1

    def test_region_only_breaks_genuine_tie(self, engine):
        # Same country/confidence band on both rows -> the region tie-break
        # decides, and the West channel gets Pacific.
        lookup = build_epg_lookup(
            [
                _epg(1, "AMC", "AMC.us"),
                _epg(2, "AMC (Pacific)", "AMC(Pacific)(AMCP).us"),
            ],
            engine=engine,
        )
        channel = {"id": 1, "name": "AMC West", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_id == 2

    def test_region_tiebreak_applies_on_prefix_matches_too(self, engine):
        # "Discovery West" -> key "discovery"; both rows key to
        # "discoverychannel", so they are PREFIX (not exact) candidates. The
        # region signal must be carried on the prefix path too, else the
        # tie-break can't fire and East wins alphabetically.
        lookup = build_epg_lookup(
            [
                _epg(1, "Discovery Channel", "DiscoveryChannel(DSC).us"),
                _epg(
                    2, "Discovery Channel (Pacific)",
                    "DiscoveryChannel(Pacific)(DSCP).us",
                ),
            ],
            engine=engine,
        )
        channel = {"id": 1, "name": "Discovery West", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.match_type == "prefix"
        assert result.best_match.epg_id == 2


# ===========================================================================
# End-to-end regressions from code review (folded in before merge, vznut.2)
# ===========================================================================

class TestRegionalEndToEndGuards:
    def test_west_channel_with_trailing_quality_tag_prefers_pacific(self, engine):
        # Review #1: a "...West HD" channel with NO tvg_id relies entirely on
        # the name for its region; the trailing "HD" must not mask "West".
        lookup = build_epg_lookup(
            [
                _epg(1, "AMC", "AMC.us"),
                _epg(2, "AMC (Pacific)", "AMC(Pacific)(AMCP).us"),
            ],
            engine=engine,
        )
        channel = {"id": 1, "name": "AMC West HD", "streams": []}
        assert "tvg_id" not in channel  # region must come from the name alone
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match.epg_id == 2

    def test_comedy_central_still_links_after_region_collapse(self, engine):
        # Review #2: the region collapse strips the noun "Central" and
        # detect_region reads "Comedy Central" as region C. The channel must
        # still link its own "Comedy Central" guide row (and not misfire onto
        # an unrelated network).
        lookup = build_epg_lookup(
            [
                _epg(1, "Comedy Central", "ComedyCentral(CC).us"),
                _epg(2, "MTV", "MTV.us"),
            ],
            engine=engine,
        )
        channel = {"id": 1, "name": "Comedy Central", "streams": []}
        result = find_epg_matches_with_lookup(channel, [], lookup, engine=engine)
        assert result.best_match is not None
        assert result.best_match.epg_name == "Comedy Central"
