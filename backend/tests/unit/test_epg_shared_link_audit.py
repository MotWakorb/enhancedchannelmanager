"""Unit tests for the shared-EPG-link audit (bead vznut.1).

Covers the West-shares-East fingerprint: two+ channels linked to one
``epg_data_id``. QA oracle cases: shared-id detected, singletons ignored,
NULL/unlinked ignored, deterministic output.
"""
from epg_matching import find_shared_epg_links


def _channel(cid, name, epg_data_id):
    return {"id": cid, "name": name, "epg_data_id": epg_data_id}


class TestFindSharedEpgLinks:
    def test_detects_shared_id(self):
        """Two channels on one epg_data_id are reported as one group of 2."""
        channels = [
            _channel(10, "USA Network", 500),
            _channel(55, "USA Network West", 500),
        ]
        epg_data = [
            {"id": 500, "name": "USA Network East", "tvg_id": "USANetwork.us",
             "epg_source": {"id": 3, "name": "JESmann"}},
        ]
        result = find_shared_epg_links(channels, epg_data)
        assert len(result["shared_links"]) == 1
        group = result["shared_links"][0]
        assert group["epg_data_id"] == 500
        assert group["count"] == 2
        assert group["epg_name"] == "USA Network East"
        assert group["tvg_id"] == "USANetwork.us"
        assert group["epg_source_id"] == 3
        assert group["channels"] == [
            {"channel_id": 10, "channel_name": "USA Network"},
            {"channel_id": 55, "channel_name": "USA Network West"},
        ]
        assert result["summary"] == {
            "shared_link_groups": 1,
            "affected_channels": 2,
            "total_channels": 2,
        }

    def test_singletons_ignored(self):
        """A channel that is the sole owner of its epg_data_id is not reported."""
        channels = [
            _channel(1, "ESPN", 100),
            _channel(2, "CNN", 101),
            _channel(3, "TNT", 102),
        ]
        result = find_shared_epg_links(channels, [])
        assert result["shared_links"] == []
        assert result["summary"]["shared_link_groups"] == 0
        assert result["summary"]["affected_channels"] == 0
        assert result["summary"]["total_channels"] == 3

    def test_null_and_missing_excluded(self):
        """NULL / missing epg_data_id channels are unlinked, not mis-linked."""
        channels = [
            _channel(1, "Unlinked A", None),
            _channel(2, "Unlinked B", None),
            {"id": 3, "name": "No key at all"},  # missing epg_data_id entirely
            _channel(4, "Shared A", 700),
            _channel(5, "Shared B", 700),
        ]
        result = find_shared_epg_links(channels, [])
        # Only the genuine 700 group surfaces; NULLs never group together.
        assert len(result["shared_links"]) == 1
        assert result["shared_links"][0]["epg_data_id"] == 700
        assert result["summary"]["affected_channels"] == 2
        assert result["summary"]["total_channels"] == 5

    def test_deterministic_ordering(self):
        """Groups sort by smallest channel id; channels within a group by id —
        regardless of input order (repeatable test oracle)."""
        channels = [
            _channel(90, "Z high B", 800),
            _channel(20, "Group-low B", 900),
            _channel(30, "Z high A", 800),
            _channel(10, "Group-low A", 900),
        ]
        result = find_shared_epg_links(channels, [])
        # Group 900 (min channel id 10) sorts before group 800 (min id 30).
        assert [g["epg_data_id"] for g in result["shared_links"]] == [900, 800]
        # Within each group channels are id-ascending.
        assert [c["channel_id"] for c in result["shared_links"][0]["channels"]] == [10, 20]
        assert [c["channel_id"] for c in result["shared_links"][1]["channels"]] == [30, 90]

    def test_enrichment_none_when_epg_row_absent(self):
        """A shared link whose EPG row isn't in epg_data still reports, with
        name/tvg_id/source as None (the linkage is what matters)."""
        channels = [_channel(1, "A", 42), _channel(2, "B", 42)]
        result = find_shared_epg_links(channels, epg_data=None)
        group = result["shared_links"][0]
        assert group["epg_data_id"] == 42
        assert group["epg_name"] is None
        assert group["tvg_id"] is None
        assert group["epg_source_id"] is None

    def test_epg_source_as_bare_int(self):
        """Dispatcharr may serialize epg_source as a bare FK int, not a dict."""
        channels = [_channel(1, "A", 42), _channel(2, "B", 42)]
        epg_data = [{"id": 42, "name": "X", "tvg_id": "x.us", "epg_source": 7}]
        result = find_shared_epg_links(channels, epg_data)
        assert result["shared_links"][0]["epg_source_id"] == 7

    def test_multiple_groups_and_larger_group(self):
        """Several groups, including one of size 3, all surface correctly."""
        channels = [
            _channel(1, "A1", 10), _channel(2, "A2", 10), _channel(3, "A3", 10),
            _channel(4, "B1", 20), _channel(5, "B2", 20),
            _channel(6, "solo", 30),
        ]
        result = find_shared_epg_links(channels, [])
        counts = {g["epg_data_id"]: g["count"] for g in result["shared_links"]}
        assert counts == {10: 3, 20: 2}
        assert result["summary"]["affected_channels"] == 5
