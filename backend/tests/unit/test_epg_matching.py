"""Unit tests for epg_matching module."""

from epg_matching import (
    EPGMatchWithScore,
    PRIORITY_TIEBREAK_BAND,
    batch_find_epg_matches,
    build_epg_lookup,
    build_source_priority_order,
    detect_country_from_streams,
    extract_broadcast_call_sign,
    extract_league_prefix,
    extract_paren_acronym,
    find_epg_matches_with_lookup,
    matches_epg_search,
    normalize_for_epg_match,
    normalize_for_epg_match_with_league,
    parse_tvg_id,
    _compute_confidence,
    _sort_matches,
)


# ---------------------------------------------------------------------------
# Helpers to build mock data
# ---------------------------------------------------------------------------

def make_channel(id, name, stream_ids=None):
    return {"id": id, "name": name, "streams": stream_ids or []}


def make_stream(id, name, channel_group_name=""):
    return {"id": id, "name": name, "channel_group_name": channel_group_name}


def make_epg(id, name, tvg_id="", epg_source=None):
    return {
        "id": id,
        "name": name,
        "tvg_id": tvg_id,
        "epg_source": epg_source or {"id": 1, "name": "Default"},
    }


# ===================================================================
# 1. extract_league_prefix
# ===================================================================

class TestExtractLeaguePrefix:
    def test_with_separator(self):
        result = extract_league_prefix("NFL RedZone")
        assert result is not None
        assert result["league"] == "NFL"
        assert result["name"] == "RedZone"

    def test_colon_separator(self):
        result = extract_league_prefix("NBA: League Pass")
        assert result is not None
        assert result["league"] == "NBA"
        assert result["name"] == "League Pass"

    def test_space_only(self):
        result = extract_league_prefix("MLB Network")
        assert result is not None
        assert result["league"] == "MLB"
        assert result["name"] == "Network"

    def test_no_match(self):
        result = extract_league_prefix("ESPN HD")
        assert result is None

    def test_prefix_at_end_no_remainder(self):
        # If the entire string IS the prefix with nothing after, returns None
        result = extract_league_prefix("NFL")
        assert result is None

    def test_dash_separator(self):
        result = extract_league_prefix("NHL - Network")
        assert result is not None
        assert result["league"] == "NHL"
        assert result["name"] == "Network"


# ===================================================================
# 2. extract_broadcast_call_sign
# ===================================================================

class TestExtractBroadcastCallSign:
    def test_k_station(self):
        assert extract_broadcast_call_sign("KABC Los Angeles") == "KABC"

    def test_w_station(self):
        assert extract_broadcast_call_sign("WABC New York") == "WABC"

    def test_with_dt_suffix(self):
        assert extract_broadcast_call_sign("WABC-DT") == "WABC"

    def test_with_hd_suffix(self):
        assert extract_broadcast_call_sign("KHOU-HD") == "KHOU"

    def test_no_match(self):
        assert extract_broadcast_call_sign("ESPN HD") is None

    def test_no_match_short(self):
        # Only 1 letter after K/W — too short
        assert extract_broadcast_call_sign("KA something") is None


# ===================================================================
# 3. detect_country_from_streams
# ===================================================================

class TestDetectCountryFromStreams:
    def test_from_name(self):
        streams = [make_stream(1, "US | ESPN HD")]
        result = detect_country_from_streams(streams)
        assert result == "US"

    def test_from_group(self):
        streams = [make_stream(1, "SomeChannel", channel_group_name="UK Sports")]
        result = detect_country_from_streams(streams)
        assert result == "UK"

    def test_no_match(self):
        streams = [make_stream(1, "ESPN HD")]
        result = detect_country_from_streams(streams)
        assert result is None

    def test_empty_streams(self):
        assert detect_country_from_streams([]) is None

    def test_most_common_wins(self):
        streams = [
            make_stream(1, "US | ESPN"),
            make_stream(2, "US | Fox"),
            make_stream(3, "UK | BBC"),
        ]
        result = detect_country_from_streams(streams)
        assert result == "US"


# ===================================================================
# 4. normalize_for_epg_match
# ===================================================================

class TestNormalizeForEpgMatch:
    def test_strips_country_prefix(self):
        result = normalize_for_epg_match("US | ESPN")
        assert result == "espn"

    def test_strips_quality(self):
        result = normalize_for_epg_match("ESPN HD")
        assert result == "espn"

    def test_strips_timezone(self):
        result = normalize_for_epg_match("ESPN East")
        assert result == "espn"

    def test_strips_special_chars(self):
        result = normalize_for_epg_match("ESPN-2")
        assert result == "espn2"

    def test_strips_numbers_kept(self):
        # Numbers are kept, only non-alnum removed
        result = normalize_for_epg_match("Fox 5")
        assert result == "fox5"

    def test_empty_string(self):
        assert normalize_for_epg_match("") == ""

    def test_combined_stripping(self):
        result = normalize_for_epg_match("US | ESPN HD East")
        assert result == "espn"


# ===================================================================
# 5. normalize_for_epg_match_with_league
# ===================================================================

class TestNormalizeForEpgMatchWithLeague:
    def test_with_league(self):
        result = normalize_for_epg_match_with_league("NFL RedZone")
        assert result["league"] == "NFL"
        assert result["normalized"] == "redzone"
        assert result["original_name"] == "NFL RedZone"

    def test_without_league(self):
        result = normalize_for_epg_match_with_league("ESPN HD")
        assert result["league"] is None
        assert result["normalized"] == "espn"
        assert result["original_name"] == "ESPN HD"


# ===================================================================
# 6. parse_tvg_id
# ===================================================================

class TestParseTvgId:
    def test_country_suffix_dot(self):
        name, country, league = parse_tvg_id("ESPN.us")
        assert name == "espn"
        assert country == "US"
        assert league is None

    def test_country_suffix_underscore(self):
        name, country, league = parse_tvg_id("CNN_us")
        assert name == "cnn"
        assert country == "US"

    def test_league_in_name(self):
        name, country, league = parse_tvg_id("NFL RedZone.us")
        assert country == "US"
        assert league == "NFL"
        assert name == "redzone"

    def test_no_separator(self):
        name, country, league = parse_tvg_id("BBCOne")
        assert name == "bbcone"
        assert country is None
        assert league is None

    def test_empty(self):
        assert parse_tvg_id("") == ("", None, None)

    def test_call_signs_not_parsed_as_country(self):
        # "WABC" has 4 chars so not a valid country code (max 3)
        name, country, league = parse_tvg_id("News.WABC")
        # WABC is 4 chars, exceeds 3-char country limit? Actually regex is 2-3.
        # Wait, the regex is {2,3} AND len <=3, so WABC (4 chars) won't match.
        assert country is None

    def test_three_letter_country(self):
        name, country, league = parse_tvg_id("ESPN.usa")
        assert country == "USA"


# ===================================================================
# 7. build_epg_lookup
# ===================================================================

class TestBuildEpgLookup:
    def test_maps_populated(self):
        epg_data = [
            make_epg(100, "ESPN", "ESPN.us"),
            make_epg(101, "WABC News", "WABC.us"),
            make_epg(102, "CNN", "CNN.uk"),
        ]
        lookup = build_epg_lookup(epg_data)

        assert "by_normalized_name" in lookup
        assert "by_tvg_id" in lookup
        assert "by_call_sign" in lookup
        assert "all_entries" in lookup

        assert len(lookup["all_entries"]) == 3
        assert "espn" in lookup["by_normalized_name"]
        assert "WABC" in lookup["by_call_sign"]

    def test_empty_data(self):
        lookup = build_epg_lookup([])
        assert lookup["all_entries"] == []
        assert lookup["by_normalized_name"] == {}

    def test_tvg_id_indexed_when_different(self):
        # If tvg normalized differs from name normalized, it gets its own index
        epg_data = [make_epg(100, "Fox News Channel", "FoxNews.us")]
        lookup = build_epg_lookup(epg_data)
        # name normalizes to "foxnewschannel", tvg normalizes to "foxnews"
        assert "foxnews" in lookup["by_tvg_id"]


# ===================================================================
# 8. find_epg_matches_with_lookup
# ===================================================================

class TestFindEpgMatchesWithLookup:
    def setup_method(self):
        self.epg_data = [
            make_epg(100, "ESPN", "ESPN.us", {"id": 1, "name": "Source1"}),
            make_epg(101, "ESPN HD", "ESPN-HD.us", {"id": 1, "name": "Source1"}),
            make_epg(102, "CNN", "CNN.us", {"id": 1, "name": "Source1"}),
            make_epg(103, "BBC One", "BBCOne.uk", {"id": 2, "name": "Source2"}),
        ]
        self.lookup = build_epg_lookup(self.epg_data)

    def test_exact_match(self):
        channel = make_channel(1, "ESPN", [1])
        streams = [make_stream(1, "US | ESPN", "US Sports")]
        result = find_epg_matches_with_lookup(channel, streams, self.lookup)

        assert result.best_match is not None
        assert result.best_match.epg_name in ("ESPN", "ESPN HD")
        assert len(result.matches) >= 1

    def test_country_preference(self):
        channel = make_channel(1, "CNN", [1])
        streams = [make_stream(1, "US | CNN", "US News")]
        result = find_epg_matches_with_lookup(channel, streams, self.lookup)

        assert result.detected_country == "US"
        assert result.best_match is not None
        assert result.best_match.epg_name == "CNN"

    def test_no_match(self):
        channel = make_channel(1, "XYZ Unknown", [1])
        streams = [make_stream(1, "XYZ Unknown")]
        result = find_epg_matches_with_lookup(channel, streams, self.lookup)

        assert result.best_match is None
        assert len(result.matches) == 0

    def test_league_match(self):
        epg_data = [
            make_epg(200, "NFL RedZone", "NFLRedZone.us"),
            make_epg(201, "NFL Network", "NFLNetwork.us"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "NFL RedZone", [1])
        streams = [make_stream(1, "US | NFL RedZone")]
        result = find_epg_matches_with_lookup(channel, streams, lookup)

        assert result.detected_league == "NFL"
        assert result.best_match is not None
        assert "RedZone" in result.best_match.epg_name

    def test_empty_channel_name(self):
        channel = make_channel(1, "", [])
        result = find_epg_matches_with_lookup(channel, [], self.lookup)
        assert result.best_match is None


# ===================================================================
# 9. batch_find_epg_matches
# ===================================================================

class TestBatchFindEpgMatches:
    def test_basic_batch(self):
        channels = [
            make_channel(1, "ESPN", [1]),
            make_channel(2, "CNN", [2]),
        ]
        streams = [
            make_stream(1, "US | ESPN", "US Sports"),
            make_stream(2, "US | CNN", "US News"),
        ]
        epg_data = [
            make_epg(100, "ESPN", "ESPN.us"),
            make_epg(101, "CNN", "CNN.us"),
        ]
        results = batch_find_epg_matches(channels, streams, epg_data)

        assert len(results) == 2
        assert results[0].channel_name == "ESPN"
        assert results[0].best_match is not None
        assert results[1].channel_name == "CNN"
        assert results[1].best_match is not None

    def test_source_ordering_prefers_higher_priority_on_tie(self):
        """Two equally-good matches from different sources: priority decides.

        Both EPG entries are identical ESPN/ESPN.us exact matches (same
        confidence band), so only source priority can break the tie. The
        preferred source (rank 0) must win.
        """
        channels = [make_channel(1, "ESPN", [1])]
        streams = [make_stream(1, "US | ESPN")]
        epg_data = [
            make_epg(100, "ESPN", "ESPN.us", {"id": 2, "name": "Backup"}),
            make_epg(101, "ESPN", "ESPN.us", {"id": 1, "name": "Primary"}),
        ]
        # Source 1 (Primary) is most preferred.
        results = batch_find_epg_matches(
            channels, streams, epg_data, source_order={1: 0, 2: 1},
        )
        assert results[0].best_match is not None
        assert results[0].best_match.epg_source["id"] == 1

    def test_source_ordering_does_not_override_better_match(self):
        """Priority must NOT beat a clearly-better lower-priority match.

        The preferred source only offers a weak prefix match ("ESPN News"),
        while the lower-priority source has an exact "ESPN" match. The exact
        match is more than one confidence band better, so it wins despite the
        other source being preferred.
        """
        channels = [make_channel(1, "ESPN", [1])]
        streams = [make_stream(1, "US | ESPN")]
        epg_data = [
            make_epg(200, "ESPN", "ESPN.us", {"id": 2, "name": "Backup"}),
            make_epg(201, "ESPN News", "ESPNNews.us", {"id": 1, "name": "Primary"}),
        ]
        results = batch_find_epg_matches(
            channels, streams, epg_data, source_order={1: 0, 2: 1},
        )
        assert results[0].best_match is not None
        # The exact match from the non-preferred source wins on quality.
        assert results[0].best_match.epg_id == 200

    def test_no_source_order_is_deterministic(self):
        """With no priority, ties fall back to a deterministic key (epg_id)."""
        channels = [make_channel(1, "ESPN", [1])]
        streams = [make_stream(1, "US | ESPN")]
        epg_data = [
            make_epg(101, "ESPN", "ESPN.us", {"id": 2, "name": "B"}),
            make_epg(100, "ESPN", "ESPN.us", {"id": 1, "name": "A"}),
        ]
        results = batch_find_epg_matches(channels, streams, epg_data)
        assert results[0].best_match is not None
        # Lower epg_id breaks the otherwise-total tie.
        assert results[0].best_match.epg_id == 100

    def test_no_channels(self):
        results = batch_find_epg_matches([], [], [])
        assert results == []


# ===================================================================
# 9b. Source priority: banded tiebreaker (_sort_matches)
# ===================================================================

def _scored(epg_id, source_id, confidence, name="ESPN", match_type="exact"):
    """Build an EPGMatchWithScore with an explicit confidence for sort tests."""
    return EPGMatchWithScore(
        epg_id=epg_id,
        epg_name=name,
        tvg_id="ESPN.us",
        epg_source={"id": source_id, "name": f"Source{source_id}"},
        confidence=confidence,
        match_type=match_type,
    )


class TestSortMatchesPriority:
    """Source priority is a banded tiebreaker in _sort_matches."""

    def test_priority_breaks_exact_tie(self):
        matches = [_scored(1, source_id=2, confidence=80),
                   _scored(2, source_id=1, confidence=80)]
        ordered = _sort_matches(matches, None, "espn", {1: 0, 2: 1})
        assert ordered[0].epg_source["id"] == 1

    def test_priority_wins_near_tie_within_band(self):
        """Preferred source wins even with slightly lower confidence (same band)."""
        # 82 and 84 share band floor(82/5)==floor(84/5)==16.
        matches = [_scored(1, source_id=2, confidence=84),
                   _scored(2, source_id=1, confidence=82)]
        ordered = _sort_matches(matches, None, "espn", {1: 0, 2: 1})
        assert ordered[0].epg_source["id"] == 1
        assert ordered[0].confidence == 82

    def test_priority_ignored_across_bands(self):
        """A clearly-better match outside the band beats the preferred source."""
        # 70 (band 14) vs 90 (band 18): different bands, higher wins.
        matches = [_scored(1, source_id=2, confidence=90),
                   _scored(2, source_id=1, confidence=70)]
        ordered = _sort_matches(matches, None, "espn", {1: 0, 2: 1})
        assert ordered[0].epg_source["id"] == 2
        assert ordered[0].confidence == 90

    def test_band_boundary_just_outside(self):
        """Just over a band boundary, confidence wins over priority."""
        # 84 (band 16) backup vs 85 (band 17) preferred-rank... flip the ranks:
        # preferred source (rank 0) has 84, backup (rank 1) has 85 -> 85 is a
        # higher band, so it wins even though its source is less preferred.
        matches = [_scored(1, source_id=1, confidence=84),   # preferred, band 16
                   _scored(2, source_id=2, confidence=85)]   # backup, band 17
        ordered = _sort_matches(matches, None, "espn", {1: 0, 2: 1})
        assert ordered[0].confidence == 85
        assert ordered[0].epg_source["id"] == 2

    def test_no_source_order_preserves_confidence_order(self):
        matches = [_scored(1, source_id=2, confidence=80),
                   _scored(2, source_id=1, confidence=85)]
        ordered = _sort_matches(matches, None, "espn", None)
        assert ordered[0].confidence == 85

    def test_unranked_source_sorts_after_ranked_in_band(self):
        """A source absent from the priority map sorts last within its band."""
        matches = [_scored(1, source_id=99, confidence=80),   # unranked
                   _scored(2, source_id=1, confidence=80)]     # rank 0
        ordered = _sort_matches(matches, None, "espn", {1: 0})
        assert ordered[0].epg_source["id"] == 1

    def test_band_constant_is_five(self):
        assert PRIORITY_TIEBREAK_BAND == 5

    def test_subchannel_breaks_confidence_tie(self):
        # m4hp1: equal confidence -> the lower subchannel (primary feed) wins.
        primary = _scored(1, source_id=1, confidence=80, name="KOCE-DT (PBS)")
        primary.subchannel = 1
        diginet = _scored(2, source_id=1, confidence=80, name="KOCE-DT2 (PBS)")
        diginet.subchannel = 2
        ordered = _sort_matches([diginet, primary], None, "koce", None)
        assert ordered[0].epg_id == 1  # primary feed first

    def test_subchannel_does_not_override_confidence(self):
        # A higher-confidence diginet still beats a lower-confidence primary —
        # subchannel sits AFTER raw confidence in the sort key.
        primary = _scored(1, source_id=1, confidence=70, name="KCOP-DT")
        primary.subchannel = 1
        diginet = _scored(2, source_id=1, confidence=82, name="KCOP-DT4 (MY)")
        diginet.subchannel = 4
        ordered = _sort_matches([primary, diginet], None, "kcop", None)
        assert ordered[0].epg_id == 2  # higher confidence wins despite -DT4

    def test_subchannel_does_not_disturb_source_priority(self):
        # Within a band, source priority must still decide BEFORE subchannel:
        # a preferred-source diginet outranks a non-preferred primary feed.
        primary = _scored(1, source_id=2, confidence=80, name="WXYZ-DT")
        primary.subchannel = 1
        diginet = _scored(2, source_id=1, confidence=80, name="WXYZ-DT2")
        diginet.subchannel = 2
        ordered = _sort_matches([primary, diginet], None, "wxyz", {1: 0, 2: 1})
        assert ordered[0].epg_source["id"] == 1  # preferred source wins


class TestBuildSourcePriorityOrder:
    """build_source_priority_order maps source_id -> rank (0 = best)."""

    def test_higher_priority_gets_rank_zero(self):
        sources = [
            {"id": 5, "name": "low", "priority": 1},
            {"id": 7, "name": "high", "priority": 10},
        ]
        order = build_source_priority_order(sources)
        assert order == {7: 0, 5: 1}

    def test_ties_broken_by_id_deterministically(self):
        sources = [
            {"id": 9, "name": "b", "priority": 10},
            {"id": 7, "name": "a", "priority": 10},
            {"id": 5, "name": "c", "priority": 1},
        ]
        order = build_source_priority_order(sources)
        assert order == {7: 0, 9: 1, 5: 2}

    def test_missing_priority_treated_as_zero(self):
        sources = [{"id": 1, "name": "a"}, {"id": 2, "name": "b", "priority": 3}]
        order = build_source_priority_order(sources)
        assert order == {2: 0, 1: 1}

    def test_skips_sources_without_id(self):
        sources = [{"name": "noid", "priority": 5}, {"id": 2, "priority": 1}]
        order = build_source_priority_order(sources)
        assert order == {2: 0}


# ===================================================================
# 10. matches_epg_search
# ===================================================================

class TestMatchesEpgSearch:
    def test_single_word(self):
        epg = make_epg(1, "ESPN HD", "ESPN.us")
        assert matches_epg_search(epg, ["espn"]) is True

    def test_multi_word(self):
        epg = make_epg(1, "ESPN HD", "ESPN.us")
        assert matches_epg_search(epg, ["espn", "hd"]) is True

    def test_no_match(self):
        epg = make_epg(1, "ESPN HD", "ESPN.us")
        assert matches_epg_search(epg, ["cnn"]) is False

    def test_empty_search(self):
        epg = make_epg(1, "ESPN HD", "ESPN.us")
        assert matches_epg_search(epg, []) is True

    def test_matches_tvg_id(self):
        epg = make_epg(1, "SomeChannel", "ESPN.us")
        assert matches_epg_search(epg, ["espn"]) is True

    def test_matches_source_name_param(self):
        epg = make_epg(1, "ESPN", "ESPN.us")
        assert matches_epg_search(epg, ["mysource"], source_name="MySource") is True

    def test_partial_match_fails(self):
        epg = make_epg(1, "ESPN HD", "ESPN.us")
        # All words must match
        assert matches_epg_search(epg, ["espn", "cnn"]) is False


# ===================================================================
# 11. Confidence scoring
# ===================================================================

class TestConfidenceScoring:
    def _make_entry(self, name="ESPN", country="US", league=None, call_sign=None):
        return {
            "id": 100,
            "name": name,
            "tvg_id": "ESPN.us",
            "epg_source": {"id": 1, "name": "Source1"},
            "normalized_name": normalize_for_epg_match(name),
            "league": league,
            "country": country,
            "call_sign": call_sign,
        }

    def test_country_match_40pts(self):
        entry = self._make_entry(country="US")
        score = _compute_confidence("espn", None, "US", None, entry, "exact")
        # Country 40 + exact 25 (no length term post-m4hp1) = 65
        assert score >= 40

    def test_exact_match_25pts(self):
        entry = self._make_entry(country=None)
        score_exact = _compute_confidence("espn", None, None, None, entry, "exact")
        score_prefix = _compute_confidence("espn", None, None, None, entry, "prefix")
        assert score_exact > score_prefix
        # m4hp1: the name-length-similarity term was REMOVED (it mis-ranked
        # noisier subchannels over the clean feed). With no token sets passed,
        # exact == 25 and prefix (len == MIN_PREFIX_LENGTH, not >) == 0.
        # Previously these were 35 / 10 (the extra +10 was length similarity).
        assert score_exact == 25  # 25 exact, no length term
        assert score_prefix == 0  # 0 prefix (not > MIN_PREFIX_LENGTH), no length

    def test_length_has_no_effect(self):
        # m4hp1: length similarity was removed entirely. A much longer EPG name
        # now scores identically to an exact-length one (only the match tier
        # counts), where previously the closer-length name got up to +10.
        short = self._make_entry(name="ESPN", country=None)
        longer = self._make_entry(name="ESPN Sports Network HD", country=None)
        score_short = _compute_confidence("espn", None, None, None, short, "exact")
        # Strip HD bonus from the comparison by using a non-HD long name.
        longer["name"] = "ESPN Sports Network Plus"
        longer["normalized_name"] = normalize_for_epg_match("ESPN Sports Network Plus")
        score_long = _compute_confidence("espn", None, None, None, longer, "exact")
        # Both are exact (25), no length term -> identical. Pre-m4hp1 the long
        # name would have lost ~10 length points.
        assert score_short == score_long == 25

    def test_call_sign_10pts(self):
        entry = self._make_entry(name="WABC", country=None, call_sign="WABC")
        score_with = _compute_confidence("wabc", None, None, "WABC", entry, "exact")
        score_without = _compute_confidence("wabc", None, None, None, entry, "exact")
        assert score_with - score_without == 10

    def test_hd_5pts(self):
        entry_hd = self._make_entry(name="ESPN HD", country=None)
        entry_no_hd = self._make_entry(name="ESPN", country=None)
        score_hd = _compute_confidence("espn", None, None, None, entry_hd, "exact")
        score_no_hd = _compute_confidence("espn", None, None, None, entry_no_hd, "exact")
        assert score_hd - score_no_hd == 5

    def test_league_match_40pts(self):
        entry = self._make_entry(name="RedZone", country=None, league="NFL")
        score = _compute_confidence("redzone", "NFL", None, None, entry, "exact")
        # League 40 + exact 25 = 65 (no length term post-m4hp1)
        assert score >= 65

    def test_no_country_no_league_0pts(self):
        entry = self._make_entry(country=None, league=None)
        score = _compute_confidence("espn", None, None, None, entry, "exact")
        # No country/league bonus, just exact 25 (length term removed in m4hp1;
        # was 35 = 25 exact + 10 length).
        assert score == 25


# ===================================================================
# 12. Token-overlap ranking (a6445)
# ===================================================================

class TestTokenOverlapRanking:
    """Among same-call-sign candidates, the entry sharing MORE of the
    channel-name's meaningful tokens (call sign AND network/affiliate/city)
    must outrank one sharing fewer. Regression for a6445: '64 | FOX: WTVT
    Tampa' was matching 'WTVT-DT2 (MVIES)' over the correct 'WTVT-DT (FOX)'
    because coincidental name-length similarity beat the shared FOX token.
    """

    def test_wtvt_fox_beats_wtvt_dt2_mvies(self):
        # Headline case. Both EPG entries share call sign WTVT, but only
        # 'WTVT-DT (FOX)' shares the network token FOX with the channel.
        epg_data = [
            make_epg(300, "WTVT-DT2 (MVIES)", "WTVT-DT2.us"),
            make_epg(301, "WTVT-DT (FOX)", "WTVT-DT.us"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "64 | FOX: WTVT Tampa", [1])
        streams = [make_stream(1, "FOX: WTVT Tampa")]
        result = find_epg_matches_with_lookup(channel, streams, lookup)

        assert result.best_match is not None
        assert result.best_match.epg_name == "WTVT-DT (FOX)"

    def test_keye_cbs_beats_keye_dt2_movies(self):
        # Generalization: not WTVT-specific, and structurally the SAME trap.
        # Channel key 'cbskeyeaustin' (len 13); the WRONG subchannel
        # 'KEYE-DT2 (Movies)' flattens to 'keyedt2movies' (len 13 -
        # coincidental length match -> wins on the old length-similarity
        # scoring), while the correct 'KEYE-DT (CBS)' flattens to 'keyedtcbs'
        # (len 9). Only the correct entry shares the CBS network token, so
        # token overlap (CBS + call sign KEYE = 2) must override the
        # misleading length tie. Proves the fix is token-driven, not WTVT luck.
        epg_data = [
            make_epg(310, "KEYE-DT2 (Movies)", "KEYE-DT2.us"),
            make_epg(311, "KEYE-DT (CBS)", "KEYE-DT.us"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "CBS: KEYE Austin", [1])
        streams = [make_stream(1, "CBS: KEYE Austin")]
        result = find_epg_matches_with_lookup(channel, streams, lookup)

        assert result.best_match is not None
        assert result.best_match.epg_name == "KEYE-DT (CBS)"


class TestOtaPrimaryFeedRanking:
    """m4hp1: the clean primary-feed network entry must beat noisier /
    higher-subchannel siblings for OTA call-sign matches. Headline guards for
    the five real cases reported after build 0035.
    """

    def _run(self, channel_name, stream_name, epg_specs):
        # epg_specs: list of (epg_id, name, tvg_id)
        epg_data = [make_epg(i, n, t) for (i, n, t) in epg_specs]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, channel_name, [1])
        streams = [make_stream(1, stream_name, "US")]
        return find_epg_matches_with_lookup(channel, streams, lookup)

    def test_kabc_clean_feed_beats_noise(self):
        # 70 | ABC: KABC LA -> KABC-DT (ABC), NOT 'TW BAK KABC B/O (ABC)' (extra
        # noise tokens) nor 'KABC-DT Stream (ABC)' (extra 'stream' token).
        result = self._run(
            "70 | ABC: KABC Los Angeles", "US | ABC KABC",
            [
                (100, "TW BAK KABC B/O (ABC)", "KABC.us"),
                (101, "KABC-DT (ABC)", "KABC-DT.us"),
                (102, "KABC-DT Stream (ABC)", "KABC.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KABC-DT (ABC)"

    def test_kcal_clean_feed_beats_diginet(self):
        # 71 | CBS: KCAL LA -> the clean KCAL-DT, NOT 'KCAL-DT2 (THENEST)'.
        result = self._run(
            "71 | CBS: KCAL Los Angeles", "US | CBS KCAL",
            [
                (110, "KCAL-DT2 (THENEST)", "KCAL-DT2.us"),
                (111, "KCAL-DT", "KCAL-DT.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KCAL-DT"

    def test_kcop_primary_beats_higher_subchannel(self):
        # 75 | MY: KCOP LA -> KCOP-DT, NOT 'KCOP-DT4 (HEROICN)'.
        result = self._run(
            "75 | MY: KCOP Los Angeles", "US | MY KCOP",
            [
                (120, "KCOP-DT4 (HEROICN)", "KCOP-DT4.us"),
                (121, "KCOP-DT", "KCOP-DT.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KCOP-DT"

    def test_koce_identical_tokens_primary_wins(self):
        # 77 | PBS: KOCE LA -> KOCE-DT (PBS) over KOCE-DT2 (PBS). The IDENTICAL
        # token case: both flatten to {koce, pbs}, so confidence ties exactly
        # and ONLY the subchannel tie-break (primary -DT over -DT2) can decide.
        result = self._run(
            "77 | PBS: KOCE Los Angeles", "US | PBS KOCE",
            [
                (130, "KOCE-DT2 (PBS)", "KOCE-DT2.us"),
                (131, "KOCE-DT (PBS)", "KOCE-DT.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KOCE-DT (PBS)"
        # Prove it was a genuine confidence tie broken by subchannel.
        by_name = {m.epg_name: m for m in result.matches}
        assert by_name["KOCE-DT (PBS)"].confidence == by_name["KOCE-DT2 (PBS)"].confidence
        assert by_name["KOCE-DT (PBS)"].subchannel == 1
        assert by_name["KOCE-DT2 (PBS)"].subchannel == 2

    def test_kcbs_regression_still_correct(self):
        # 72 | CBS: KCBS LA -> KCBS-DT (CBS) (was already correct; must stay).
        result = self._run(
            "72 | CBS: KCBS Los Angeles", "US | CBS KCBS",
            [
                (140, "KCBS-DT (CBS)", "KCBS-DT.us"),
                (141, "KCBS-DT2 (DECADES)", "KCBS-DT2.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KCBS-DT (CBS)"

    def test_subchannel_tiebreak_does_not_beat_better_overlap(self):
        # Guard: the subchannel tie-break must ONLY break genuine ties. If the
        # channel is actually the MyNet diginet, 'KCOP-DT4 (MY)' shares the MY
        # network token and must WIN over the primary 'KCOP-DT' despite having a
        # higher subchannel number — token quality outranks the tie-break.
        result = self._run(
            "75 | MY: KCOP Los Angeles", "US | MY KCOP",
            [
                (150, "KCOP-DT", "KCOP-DT.us"),
                (151, "KCOP-DT4 (MY)", "KCOP-DT4.us"),
            ],
        )
        assert result.best_match is not None
        assert result.best_match.epg_name == "KCOP-DT4 (MY)"


class TestTokenOverlapConfidence:
    """Unit-level checks on the token-overlap term in _compute_confidence."""

    def _entry(self, name, tokens, call_sign=None):
        return {
            "id": 100,
            "name": name,
            "tvg_id": "",
            "epg_source": {"id": 1, "name": "Source1"},
            "normalized_name": normalize_for_epg_match(name),
            "league": None,
            "country": None,
            "call_sign": call_sign,
            "match_tokens": frozenset(tokens),
        }

    def test_more_shared_tokens_scores_higher(self):
        channel_tokens = frozenset({"wtvt", "fox", "tampa"})
        high = self._entry("WTVT-DT (FOX)", {"wtvt", "fox"}, call_sign="WTVT")
        low = self._entry("WTVT-DT2 (MVIES)", {"wtvt", "mvies"}, call_sign="WTVT")
        score_high = _compute_confidence(
            "foxwtvttampa", None, None, "WTVT", high, "callsign",
            channel_tokens=channel_tokens,
        )
        score_low = _compute_confidence(
            "foxwtvttampa", None, None, "WTVT", low, "callsign",
            channel_tokens=channel_tokens,
        )
        assert score_high > score_low

    def test_one_extra_token_outweighs_length_swing(self):
        # The +1 shared token on the FOX entry must beat the misleading length
        # advantage MVIES enjoys (its flattened name is the same length as the
        # channel key). This is the exact failure mode from a6445.
        channel_tokens = frozenset({"wtvt", "fox", "tampa"})
        fox = self._entry("WTVT-DT (FOX)", {"wtvt", "fox"}, call_sign="WTVT")
        mvies = self._entry(
            "WTVT-DT2 (MVIES)", {"wtvt", "mvies"}, call_sign="WTVT"
        )
        # channel key 'foxwtvttampa' len 12; 'wtvtdtfox' len 9, 'wtvtdt2mvies'
        # len 12 (coincidental length match -> MVIES wins on old scoring).
        score_fox = _compute_confidence(
            "foxwtvttampa", None, None, "WTVT", fox, "callsign",
            channel_tokens=channel_tokens,
        )
        score_mvies = _compute_confidence(
            "foxwtvttampa", None, None, "WTVT", mvies, "callsign",
            channel_tokens=channel_tokens,
        )
        assert score_fox > score_mvies

    def test_exact_match_still_outranks_high_overlap_callsign(self):
        # Hierarchy preserved: a mere call-sign match with strong token overlap
        # must NOT beat an exact match. Exact share-all tokens too, plus the
        # exact tier (25). A token-overlap-only callsign sibling stays below.
        channel_tokens = frozenset({"espn"})
        exact = self._entry("ESPN", {"espn"})
        callsign_sibling = self._entry(
            "ESPN News WXYZ", {"espn", "news", "wxyz"}, call_sign="WXYZ"
        )
        score_exact = _compute_confidence(
            "espn", None, None, None, exact, "exact",
            channel_tokens=channel_tokens,
        )
        score_sibling = _compute_confidence(
            "espn", None, None, None, callsign_sibling, "callsign",
            channel_tokens=channel_tokens,
        )
        assert score_exact > score_sibling

    def test_country_match_still_outranks_token_overlap(self):
        # Country (40) dominates token overlap. A country-preferred exact match
        # must stay on top of a fuzzy callsign sibling with more shared tokens.
        channel_tokens = frozenset({"cnn"})
        country_exact = self._entry("CNN", {"cnn"})
        country_exact["country"] = "US"
        fuzzy = self._entry(
            "CNN Sports Network", {"cnn", "sports", "network"}, call_sign=None
        )
        score_country = _compute_confidence(
            "cnn", None, "US", None, country_exact, "exact",
            channel_tokens=channel_tokens,
        )
        score_fuzzy = _compute_confidence(
            "cnn", None, "US", None, fuzzy, "prefix",
            channel_tokens=channel_tokens,
        )
        assert score_country > score_fuzzy

    def test_no_tokens_means_no_overlap_bonus(self):
        # Back-compat: entries built without match_tokens (legacy direct calls)
        # contribute zero token overlap, so scores are unchanged by the term.
        entry = {
            "id": 1, "name": "ESPN", "tvg_id": "", "epg_source": {},
            "normalized_name": "espn", "league": None, "country": None,
            "call_sign": None,
        }
        with_tokens = _compute_confidence(
            "espn", None, None, None, entry, "exact",
            channel_tokens=frozenset({"espn"}),
        )
        without = _compute_confidence("espn", None, None, None, entry, "exact")
        # 25 exact only (length term removed in m4hp1; was 35).
        assert with_tokens == without == 25

    def test_extra_epg_tokens_penalised(self):
        # m4hp1: precision-aware quality. Two entries share the same single
        # token (kabc) but the noisier one carries extra tokens the channel
        # lacks; it must score LOWER. shared(1)*12 - extra*4.
        channel_tokens = frozenset({"abc", "kabc", "los", "angeles"})
        clean = self._entry("KABC-DT (ABC)", {"kabc", "abc"}, call_sign="KABC")
        noisy = self._entry(
            "TW BAK KABC B/O (ABC)", {"kabc", "abc", "tw", "bak"},
            call_sign="KABC",
        )
        score_clean = _compute_confidence(
            "abckabclosangeles", None, None, "KABC", clean, "callsign",
            channel_tokens=channel_tokens,
        )
        score_noisy = _compute_confidence(
            "abckabclosangeles", None, None, "KABC", noisy, "callsign",
            channel_tokens=channel_tokens,
        )
        # clean: shared {kabc,abc}=2 *12, extra 0 -> +24, +10 callsign = 34.
        # noisy: shared 2 *12 - extra {tw,bak}=2 *4 -> +16, +10 = 26.
        assert score_clean == 34
        assert score_noisy == 26
        assert score_clean > score_noisy


# ===================================================================
# 13. Parenthetical acronym matching (bd-6rz70)
# ===================================================================
#
# Root cause: Dispatcharr's guide DB encodes many entries' short acronym
# INSIDE the tvg_id (and sometimes the display name) as a parenthetical,
# e.g. "FamilyEntertainmentTelevision(FETV).us". epg_match_key flattens the
# WHOLE tvg_id into one blob ("familyentertainmenttelevisionfetv"), burying
# the acronym mid-string where no exact/prefix boundary check can find it.
# Real channels named after the acronym ("890 | Fetv", "876 | HGTV",
# "846 | OWN") therefore got zero or wrong candidates.

class TestExtractParenAcronym:
    def test_tvg_id_form(self):
        assert extract_paren_acronym(
            "FamilyEntertainmentTelevision(FETV).us"
        ) == "fetv"

    def test_name_form(self):
        assert extract_paren_acronym(
            "Family Entertainment Television (FETV)"
        ) == "fetv"

    def test_html_escaped_ampersand_in_prefix_ignored(self):
        # The "&amp;" HTML-entity artifact sits OUTSIDE the parens, so
        # extraction is unaffected by it.
        assert extract_paren_acronym(
            "Home&amp;GardenTelevision(HGTV).us"
        ) == "hgtv"

    def test_no_parens_returns_none(self):
        assert extract_paren_acronym("ESPN.us") is None
        assert extract_paren_acronym("ESPN") is None

    def test_empty_string_returns_none(self):
        assert extract_paren_acronym("") is None
        assert extract_paren_acronym(None) is None

    def test_too_short_acronym_returns_none(self):
        # A single-character parenthetical isn't trustworthy as an exact key.
        assert extract_paren_acronym("Something(X).us") is None

    def test_non_alnum_inside_parens_stripped(self):
        assert extract_paren_acronym("Some Channel (F-M)") == "fm"

    def test_first_parenthetical_wins(self):
        assert extract_paren_acronym("Weird(ABC)Name(DEF)") == "abc"


class TestBuildEpgLookupAcronym:
    def test_by_acronym_indexed_from_tvg_id(self):
        epg_data = [make_epg(12334, "Family Entertainment Television",
                              "FamilyEntertainmentTelevision(FETV).us")]
        lookup = build_epg_lookup(epg_data)
        assert "by_acronym" in lookup
        assert "fetv" in lookup["by_acronym"]
        assert lookup["by_acronym"]["fetv"][0]["id"] == 12334

    def test_by_acronym_indexed_from_name(self):
        epg_data = [make_epg(57679, "Family Entertainment Television (FETV)",
                              "FETV.us")]
        lookup = build_epg_lookup(epg_data)
        assert "fetv" in lookup["by_acronym"]
        assert lookup["by_acronym"]["fetv"][0]["id"] == 57679

    def test_no_acronym_no_entry(self):
        epg_data = [make_epg(100, "ESPN", "ESPN.us")]
        lookup = build_epg_lookup(epg_data)
        assert lookup["by_acronym"] == {}

    def test_acronym_folded_into_match_tokens(self):
        # bd-6rz70: the tvg_id-only acronym is folded into match_tokens so
        # the token-overlap term can see it (it isn't otherwise tokenized,
        # since match_tokens only derives from the display name).
        epg_data = [make_epg(12334, "Family Entertainment Television",
                              "FamilyEntertainmentTelevision(FETV).us")]
        lookup = build_epg_lookup(epg_data)
        entry = lookup["by_acronym"]["fetv"][0]
        assert "fetv" in entry["match_tokens"]


class TestAcronymMatching:
    """End-to-end regression for the three headline PO-reported failures."""

    def test_own_zero_matches_to_present(self):
        # "846 | OWN" -> 0 matches before the fix (channel_normalized "own"
        # is 3 chars, below MIN_PREFIX_LENGTH=4, so the prefix-scan path
        # never runs, and no exact/tvg_id/callsign path catches it either).
        epg_data = [
            make_epg(30033, "Oprah Winfrey Network",
                     "OprahWinfreyNetwork(OWN).us"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "846 | OWN", [])
        result = find_epg_matches_with_lookup(channel, [], lookup)

        assert result.best_match is not None
        assert result.best_match.epg_id == 30033
        assert result.best_match.match_type == "acronym"
        assert result.best_match.confidence > 0

    def test_fetv_correct_entry_becomes_candidate(self):
        # "890 | Fetv" -> before the fix, only a wrong low-confidence match
        # ("FeTV Canal 5", a Colombian channel) surfaced; the correct
        # id-12334 entry never became a candidate at all.
        epg_data = [
            make_epg(12334, "Family Entertainment Television",
                     "FamilyEntertainmentTelevision(FETV).us"),
            # tvg_id normalizes to the SAME key as the name ("fetvcanal5"),
            # so this is only ever a weak prefix match, not a coincidental
            # exact tvg_id match — matching the real-world Colombian entry's
            # low-confidence (8pt) behavior reported by the PO.
            make_epg(99001, "FeTV Canal 5", "FeTVCanal5.co"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "890 | Fetv", [])
        result = find_epg_matches_with_lookup(channel, [], lookup)

        matched_ids = {m.epg_id for m in result.matches}
        assert 12334 in matched_ids
        correct = next(m for m in result.matches if m.epg_id == 12334)
        assert correct.match_type == "acronym"
        # The correct entry should outrank the coincidental Colombian match.
        assert result.best_match is not None
        assert result.best_match.epg_id == 12334

    def test_hgtv_correct_entry_becomes_candidate_alongside_wrong_country(self):
        # "876 | HGTV" -> before the fix, candidates existed but were ALL
        # wrong-country HD variants (Chile/Italy/Poland/Brazil); the correct
        # US entry (id 16985) never became a candidate at all.
        epg_data = [
            make_epg(16985, "Home & Garden Television",
                     "Home&amp;GardenTelevision(HGTV).us"),
            make_epg(70001, "HGTVHD", "HGTVCL-HD.cl"),
            make_epg(70002, "HGTVHD", "HGTVIT-HD.it"),
        ]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "876 | HGTV", [])
        result = find_epg_matches_with_lookup(channel, [], lookup)

        matched_ids = {m.epg_id for m in result.matches}
        assert 16985 in matched_ids
        correct = next(m for m in result.matches if m.epg_id == 16985)
        assert correct.match_type == "acronym"
        # Present and reasonably scored — not required to be #1 without
        # real stream/country data (see bd-6rz70 verification notes), but
        # must not be scored at/near zero.
        assert correct.confidence >= 20

    def test_acronym_not_gated_by_min_prefix_length(self):
        # Regression guard: a 3-char acronym match must still surface even
        # though it's below MIN_PREFIX_LENGTH (4).
        from epg_matching import MIN_PREFIX_LENGTH
        assert len("own") < MIN_PREFIX_LENGTH
        epg_data = [make_epg(1, "Oprah Winfrey Network",
                              "OprahWinfreyNetwork(OWN).us")]
        lookup = build_epg_lookup(epg_data)
        channel = make_channel(1, "OWN", [])
        result = find_epg_matches_with_lookup(channel, [], lookup)
        assert result.best_match is not None

    def test_acronym_does_not_duplicate_an_already_exact_match(self):
        # An entry can be indexed under BOTH by_normalized_name (exact) AND
        # by_acronym (its tvg_id also has a matching parenthetical). The
        # acronym branch runs after exact/tvg_id/callsign and must respect
        # seen_epg_ids — the entry should appear exactly once, tagged
        # "exact" (the higher-fidelity signal), never duplicated as a
        # separate "acronym" candidate.
        epg_data = [make_epg(1, "OWN", "OWN(OWN).us")]
        lookup = build_epg_lookup(epg_data)
        assert "own" in lookup["by_acronym"]  # sanity: it IS acronym-indexed
        channel = make_channel(1, "OWN", [])
        result = find_epg_matches_with_lookup(channel, [], lookup)
        assert len(result.matches) == 1
        assert result.best_match is not None
        assert result.best_match.match_type == "exact"

    def test_acronym_weight_equals_exact_weight(self):
        # Documents the confidence-weight decision (bd-6rz70): acronym and
        # exact share the same base weight (25).
        entry = {
            "id": 1, "name": "Oprah Winfrey Network", "tvg_id": "",
            "epg_source": {}, "normalized_name": "oprahwinfreynetwork",
            "league": None, "country": None, "call_sign": None,
        }
        score_acronym = _compute_confidence("own", None, None, None, entry, "acronym")
        score_exact = _compute_confidence("own", None, None, None, entry, "exact")
        assert score_acronym == score_exact == 25
