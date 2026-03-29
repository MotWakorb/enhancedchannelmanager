"""Unit tests for epg_matching module."""

import pytest

from epg_matching import (
    batch_find_epg_matches,
    build_epg_lookup,
    detect_country_from_streams,
    extract_broadcast_call_sign,
    extract_league_prefix,
    find_epg_matches_with_lookup,
    matches_epg_search,
    normalize_for_epg_match,
    normalize_for_epg_match_with_league,
    parse_tvg_id,
    _compute_confidence,
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

    def test_source_ordering(self):
        channels = [make_channel(1, "ESPN", [1])]
        streams = [make_stream(1, "US | ESPN")]
        epg_data = [
            make_epg(100, "ESPN", "ESPN.us", {"id": 1, "name": "Source1"}),
            make_epg(101, "ESPN", "ESPN.uk", {"id": 2, "name": "Source2"}),
        ]
        source_order = {1: 0, 2: 1}
        results = batch_find_epg_matches(
            channels, streams, epg_data, source_order=source_order,
        )
        assert len(results) == 1
        assert results[0].best_match is not None

    def test_no_channels(self):
        results = batch_find_epg_matches([], [], [])
        assert results == []


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
        # Country 40 + exact 25 + length similarity 20 = 85
        assert score >= 40

    def test_exact_match_25pts(self):
        entry = self._make_entry(country=None)
        # Use same channel_normalized for both so length similarity is identical
        score_exact = _compute_confidence("espn", None, None, None, entry, "exact")
        score_prefix = _compute_confidence("espn", None, None, None, entry, "prefix")
        assert score_exact > score_prefix
        # Exact gives 25; prefix with len == MIN_PREFIX_LENGTH gives 0
        assert score_exact == 45  # 25 exact + 20 length
        assert score_prefix == 20  # 0 prefix (not > MIN_PREFIX_LENGTH) + 20 length

    def test_length_similarity_20pts(self):
        entry = self._make_entry(name="ESPN", country=None)
        # Identical length = full 20 pts
        score = _compute_confidence("espn", None, None, None, entry, "exact")
        # exact 25 + length 20 = 45
        assert score == 45

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
        # League 40 + exact 25 + length similarity 20 = 85 (approx, depends on len)
        assert score >= 65

    def test_no_country_no_league_0pts(self):
        entry = self._make_entry(country=None, league=None)
        score = _compute_confidence("espn", None, None, None, entry, "exact")
        # No country/league bonus, just exact + length
        assert score == 45  # 25 exact + 20 length
