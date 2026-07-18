"""Region-aware EPG matching in the auto-creation executor (bead vznut.4).

``ActionExecutor._match_epg_data`` is auto-creation's OWN simple EPG matcher —
separate from ``epg_matching.match_channels_epg``, which vznut.2 already made
region-aware. These tests mirror the vznut.2 design onto ``_match_epg_data``:

  * CHARACTERIZATION / INVARIANT GUARDS: pin the pre-vznut.4 behavior that
    must NOT change — exact-tvg_id short-circuit, exact-over-prefix tiers,
    length-similarity ordering for non-regional names, call-sign matching,
    single-entry fallback. If one of these goes red, the region change has
    over-reached.
  * REGIONAL: the new behavior — West==Pacific equivalence, Mountain/Central
    as their own regions (no cross-equivalence), tvg_id-paren-then-last-word
    region detection with quality/digit token skipping, and a
    region-consistency preference applied WITHIN each match tier.

Root cause recap (epic vznut, KNM field report): a "USA Network West" channel
ended up linked to the East feed's guide row because the base/East entry and
the "(Pacific)" entry normalize to the SAME key, and the pre-fix matcher broke
the tie by list order / coincidental length instead of region.
"""
from unittest.mock import MagicMock

from channel_pipeline_executor import ActionExecutor


def make_executor() -> ActionExecutor:
    """Bare executor — _match_epg_data uses no instance state beyond logging."""
    return ActionExecutor(MagicMock())


# Canonical JESmann-style guide rows: the base (East default) feed and its
# "(Pacific)" regional sibling. Both tvg_ids normalize to "usanetwork" via
# _parse_tvg_id's paren-stripping, so both land in the EXACT tier.
USA_BASE = {"id": 1, "tvg_id": "USANetwork.us", "name": "USA Network"}
USA_PACIFIC = {
    "id": 2,
    "tvg_id": "USANetwork(Pacific)(USAP).us",
    "name": "USA Network (Pacific)",
}

AMC_BASE = {"id": 11, "tvg_id": "AMC.us", "name": "AMC"}
AMC_PACIFIC = {"id": 12, "tvg_id": "AMC(Pacific)(AMCP).us", "name": "AMC (Pacific)"}


class TestCharacterizationNonRegional:
    """Pre-vznut.4 behavior that must survive the region change unchanged."""

    def setup_method(self):
        self.executor = make_executor()

    def test_exact_tvg_id_short_circuit_wins_even_over_region(self):
        """Step 1 (channel.tvg_id == entry.tvg_id) stays authoritative.

        If the rule explicitly assigned the base tvg_id, region preference
        must not second-guess an exact tvg_id equality — that entry IS the
        requested row.
        """
        channel = {"name": "USA Network West", "tvg_id": "USANetwork.us"}
        result = self.executor._match_epg_data(channel, [USA_PACIFIC, USA_BASE])
        assert result is USA_BASE

    def test_exact_normalized_match_beats_prefix(self):
        channel = {"name": "ESPN", "tvg_id": None}
        espn = {"id": 21, "tvg_id": "ESPN.us", "name": "ESPN"}
        espnews = {"id": 22, "tvg_id": "ESPNews.us", "name": "ESPNews"}
        result = self.executor._match_epg_data(channel, [espnews, espn])
        assert result is espn

    def test_prefix_matches_sort_by_length_similarity(self):
        """Among prefix candidates for a NON-regional name, smaller length
        difference still wins (pre-existing len_diff tie-break)."""
        channel = {"name": "Nick", "tvg_id": None}
        nickelodeon = {"id": 31, "tvg_id": "Nickelodeon.us", "name": "Nickelodeon"}
        nick_jr = {"id": 32, "tvg_id": "NickJr.us", "name": "Nick Jr"}
        result = self.executor._match_epg_data(channel, [nickelodeon, nick_jr])
        assert result is nick_jr

    def test_call_sign_in_parens_still_matches(self):
        channel = {"name": "STOON", "tvg_id": None}
        entry = {
            "id": 41,
            "tvg_id": "CartoonNetwork(STOONHD).us",
            "name": "Cartoon Network",
        }
        other = {"id": 42, "tvg_id": "TSN1.ca", "name": "TSN 1"}
        result = self.executor._match_epg_data(channel, [other, entry])
        assert result is entry

    def test_single_entry_fallback_for_unmatchable_name(self):
        channel = {"name": "###", "tvg_id": None}
        entry = {"id": 51, "tvg_id": "dummy", "name": "Dummy EPG"}
        assert self.executor._match_epg_data(channel, [entry]) is entry

    def test_no_match_returns_none(self):
        channel = {"name": "HBO", "tvg_id": None}
        entries = [
            {"id": 61, "tvg_id": "CNN.us", "name": "CNN"},
            {"id": 62, "tvg_id": "TNT.us", "name": "TNT"},
        ]
        assert self.executor._match_epg_data(channel, entries) is None

    def test_no_region_channel_keeps_first_listed_exact_on_tie(self):
        """A channel WITHOUT a region stays completely unaffected: the region
        rank is inert (0 for every candidate), so the original stable-sort
        outcome — first-listed exact entry on a full tie — is preserved even
        when a regional sibling is present."""
        channel = {"name": "USA Network", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [USA_BASE, USA_PACIFIC])
        assert result is USA_BASE


class TestRegionalPreference:
    """vznut.4: region-consistency preference within each match tier."""

    def setup_method(self):
        self.executor = make_executor()

    def test_west_channel_prefers_pacific_over_east_exact(self):
        """The KNM failure mode: both rows are exact-key candidates; the West
        channel must link the (Pacific) row, not the East/base default."""
        channel = {"name": "USA Network West", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [USA_BASE, USA_PACIFIC])
        assert result is USA_PACIFIC

    def test_west_prefers_pacific_regardless_of_entry_order(self):
        """Pre-fix the winner was whichever row came first in the source list;
        post-fix the region decides, both orders."""
        channel = {"name": "USA Network West", "tvg_id": None}
        for entries in ([USA_BASE, USA_PACIFIC], [USA_PACIFIC, USA_BASE]):
            result = self.executor._match_epg_data(channel, entries)
            assert result is USA_PACIFIC

    def test_east_channel_prefers_base_over_pacific(self):
        """Conflict (Pacific, rank 2) sorts after no-region (base, rank 1)."""
        channel = {"name": "USA Network East", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [USA_PACIFIC, USA_BASE])
        assert result is USA_BASE

    def test_channel_region_read_from_tvg_id_paren(self):
        """Detection order: a parenthetical region in the CHANNEL's tvg_id is
        authoritative (tvg_id-paren-then-last-word, vznut.2 rule 1)."""
        channel = {"name": "USA Network", "tvg_id": "USANetwork(West)(USAW).ca"}
        result = self.executor._match_epg_data(channel, [USA_BASE, USA_PACIFIC])
        assert result is USA_PACIFIC

    def test_west_channel_name_side_pacific_word_detected(self):
        """'Pacific' as the trailing display-name word maps to region W too
        (West == Pacific equivalence), exercised in the prefix tier."""
        channel = {"name": "TNT Pacific", "tvg_id": None}
        tnt_base = {"id": 71, "tvg_id": "TNT.us", "name": "TNT"}
        tnt_pacific = {
            "id": 72,
            "tvg_id": "TNT(Pacific)(TNTP).us",
            "name": "TNT (Pacific)",
        }
        result = self.executor._match_epg_data(channel, [tnt_base, tnt_pacific])
        assert result is tnt_pacific


class TestMountainCentralIsolation:
    """Mountain and Central are their OWN regions — never aliased to Pacific."""

    def setup_method(self):
        self.executor = make_executor()

    def test_mountain_channel_does_not_take_pacific(self):
        channel = {"name": "AMC Mountain", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [AMC_PACIFIC, AMC_BASE])
        assert result is AMC_BASE

    def test_central_channel_does_not_take_pacific(self):
        channel = {"name": "AMC Central", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [AMC_PACIFIC, AMC_BASE])
        assert result is AMC_BASE

    def test_mountain_channel_prefers_mountain_entry(self):
        channel = {"name": "AMC Mountain", "tvg_id": None}
        amc_mountain = {
            "id": 13,
            "tvg_id": "AMC(Mountain)(AMCM).us",
            "name": "AMC (Mountain)",
        }
        result = self.executor._match_epg_data(
            channel, [AMC_BASE, AMC_PACIFIC, amc_mountain]
        )
        assert result is amc_mountain


class TestDetectionTokenSkipping:
    """Trailing quality tokens and bare digits are skipped when reading the
    region off the display name (vznut.2 detection rules)."""

    def setup_method(self):
        self.executor = make_executor()

    def test_trailing_quality_token_skipped(self):
        """'AMC West HD' -> region from 'West', not 'HD' (exact tier: the
        normalizer strips both 'HD' and 'West', so both rows are exact)."""
        channel = {"name": "AMC West HD", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [AMC_BASE, AMC_PACIFIC])
        assert result is AMC_PACIFIC

    def test_trailing_digit_skipped(self):
        """'AMC West 2' -> region from 'West', not '2' (prefix tier: the
        trailing digit survives normalization, so both rows are prefix
        candidates and region breaks the tie there)."""
        channel = {"name": "AMC West 2", "tvg_id": None}
        result = self.executor._match_epg_data(channel, [AMC_BASE, AMC_PACIFIC])
        assert result is AMC_PACIFIC

    def test_bare_digit_never_invents_a_region(self):
        """'TNT 2' -> skip '2' -> 'TNT' -> no region: ranking unchanged, the
        exact base row wins over an unrelated prefix row."""
        channel = {"name": "TNT 2", "tvg_id": None}
        tnt2 = {"id": 81, "tvg_id": "TNT2.us", "name": "TNT 2"}
        tnt_pacific = {
            "id": 82,
            "tvg_id": "TNT(Pacific)(TNTP).us",
            "name": "TNT (Pacific)",
        }
        result = self.executor._match_epg_data(channel, [tnt_pacific, tnt2])
        assert result is tnt2


class TestTierPreservation:
    """Region preference is tier-preserving: it reorders candidates WITHIN the
    exact tier and WITHIN the prefix tier, but a region-matched prefix
    candidate never beats an exact candidate (the analog of vznut.2's
    band-preserving rule)."""

    def setup_method(self):
        self.executor = make_executor()

    def test_exact_neutral_beats_prefix_region_match(self):
        channel = {"name": "Cinemax West", "tvg_id": None}
        exact_base = {"id": 91, "tvg_id": "Cinemax.us", "name": "Cinemax"}
        prefix_pacific = {
            "id": 92,
            "tvg_id": "CinemaxMore(Pacific).us",
            "name": "Cinemax More (Pacific)",
        }
        result = self.executor._match_epg_data(
            channel, [prefix_pacific, exact_base]
        )
        assert result is exact_base
