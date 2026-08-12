"""
Unit tests for channel_pipeline_sort — the backend port of the manual
Sort & Renumber modal's comparator (frontend/src/utils/channelSort.ts).

Fixtures mirror frontend/src/utils/naturalSort.test.ts and
frontend/src/utils/channelSort.test.ts so both sides are locked to the
same behavior (enhancedchannelmanager-vy4fl / hf8t9).
"""
from channel_pipeline_sort import (
    channel_sort_key,
    get_name_for_sorting,
    sort_channels_by_name,
    strip_country_prefix,
)


class TestGetNameForSorting:
    def test_strips_numeric_prefix(self):
        assert get_name_for_sorting("123 | ESPN") == "ESPN"

    def test_strips_numeric_suffix(self):
        assert get_name_for_sorting("ESPN | 123") == "ESPN"

    def test_strips_mid_position_number(self):
        # Matches the frontend's actual (verified) regex output. The
        # source comment on getNameForSorting in ChannelsPane.tsx claims
        # "US | 5034 - Name" -> "US - Name", but the capture group
        # includes the pipe/spaces, so the real output keeps the pipe —
        # ported here verbatim for parity (discovered-not-fixed: stale
        # comment in ChannelsPane.tsx, not a functional bug).
        assert get_name_for_sorting("US | 5034 - ESPN") == "US | - ESPN"

    def test_no_number_returns_unchanged(self):
        assert get_name_for_sorting("ESPN") == "ESPN"

    def test_decimal_prefix_stripped(self):
        assert get_name_for_sorting("2.1 | ESPN") == "ESPN"

    def test_decimal_prefix_with_dash_stripped(self):
        assert get_name_for_sorting("2.1 - ESPN") == "ESPN"

    def test_decimal_prefix_with_dot_stripped(self):
        assert get_name_for_sorting("2.1.ESPN") == "ESPN"

    def test_numeric_brand_with_space_is_not_treated_as_channel_number(self):
        assert get_name_for_sorting("360 Tunebox") == "360 Tunebox"

    def test_numeric_brand_without_space_is_unchanged(self):
        assert get_name_for_sorting("360Tunebox") == "360Tunebox"

    def test_decimal_numeric_brand_with_space_is_unchanged(self):
        assert get_name_for_sorting("2.1 Tunebox") == "2.1 Tunebox"

    def test_integer_dot_separator_is_still_stripped(self):
        assert get_name_for_sorting("123.Name") == "Name"


class TestStripCountryPrefix:
    def test_strips_pipe_separated_prefix(self):
        assert strip_country_prefix("US | ESPN") == "ESPN"

    def test_strips_colon_separated_prefix(self):
        assert strip_country_prefix("UK: BBC One") == "BBC One"

    def test_strips_dash_separated_prefix(self):
        assert strip_country_prefix("CA - CBC") == "CBC"

    def test_strips_three_letter_prefix(self):
        assert strip_country_prefix("USA | Fox") == "Fox"

    def test_strips_no_separator_prefix(self):
        assert strip_country_prefix("US ESPN") == "ESPN"

    def test_no_country_prefix_returns_unchanged(self):
        assert strip_country_prefix("ESPN") == "ESPN"

    def test_lowercase_prefix_not_stripped(self):
        # Only 2-3 UPPERCASE letters count as a country code.
        assert strip_country_prefix("us | ESPN") == "us | ESPN"


class TestChannelSortKeyNaturalOrdering:
    """Locks 'Channel 2' before 'Channel 10' — the natural-sort fixture
    called out explicitly in the bead (naturalCompare parity)."""

    def test_single_digit_before_double_digit(self):
        key_2 = channel_sort_key("Channel 2")
        key_10 = channel_sort_key("Channel 10")
        assert key_2 < key_10

    def test_case_insensitive(self):
        assert channel_sort_key("ESPN") == channel_sort_key("espn")

    def test_strip_numbers_option_normalizes_before_comparing(self):
        # With strip_numbers, "100 | ESPN" and "5 | ESPN" both reduce to
        # "ESPN" and compare equal — the leading numbers are ignored.
        key_a = channel_sort_key("100 | ESPN", strip_numbers=True)
        key_b = channel_sort_key("5 | ESPN", strip_numbers=True)
        assert key_a == key_b

    def test_ignore_country_option_strips_prefix_before_comparing(self):
        key_a = channel_sort_key("US | ESPN", ignore_country=True)
        key_b = channel_sort_key("UK | ESPN", ignore_country=True)
        assert key_a == key_b


class TestSortChannelsByName:
    def test_numeric_names_with_and_without_spaces_share_natural_ordering(self):
        channels = [
            {"id": 1, "name": "360Tunebox"},
            {"id": 2, "name": "Alpha"},
            {"id": 3, "name": "10 Sports"},
            {"id": 4, "name": "360 Tunebox"},
            {"id": 5, "name": "2 Sports"},
        ]
        sorted_channels = sort_channels_by_name(channels, strip_numbers=True)
        assert [c["id"] for c in sorted_channels] == [5, 3, 4, 1, 2]

    def test_decimal_and_integer_brands_follow_natural_ordering(self):
        channels = [
            {"id": 1, "name": "10 Tunebox"},
            {"id": 2, "name": "2.10 Tunebox"},
            {"id": 3, "name": "2 Tunebox"},
            {"id": 4, "name": "2.1 Tunebox"},
        ]
        sorted_channels = sort_channels_by_name(channels, strip_numbers=True)
        assert [c["id"] for c in sorted_channels] == [3, 4, 2, 1]

    def test_sorts_ascending_by_default(self):
        channels = [{"id": 1, "name": "Channel 10"}, {"id": 2, "name": "Channel 2"}]
        sorted_channels = sort_channels_by_name(channels)
        assert [c["id"] for c in sorted_channels] == [2, 1]

    def test_sorts_descending(self):
        channels = [{"id": 1, "name": "Channel 2"}, {"id": 2, "name": "Channel 10"}]
        sorted_channels = sort_channels_by_name(channels, order="desc")
        assert [c["id"] for c in sorted_channels] == [2, 1]

    def test_country_prefix_case_sorts_by_stripped_names(self):
        channels = [
            {"id": 1, "name": "US | Zeta"},
            {"id": 2, "name": "UK | Alpha"},
        ]
        sorted_channels = sort_channels_by_name(channels, ignore_country=True)
        assert [c["id"] for c in sorted_channels] == [2, 1]

    def test_default_options_match_manual_modal_defaults(self):
        # Manual modal defaults: strip_numbers=True, ignore_country=False.
        channels = [
            {"id": 1, "name": "100 | Zeta"},
            {"id": 2, "name": "5 | Alpha"},
        ]
        sorted_channels = sort_channels_by_name(channels)
        assert [c["id"] for c in sorted_channels] == [2, 1]

    def test_desc_preserves_tie_order_stability(self):
        # Two channels with identical sort keys after normalization keep
        # their original relative order even in descending mode (Python's
        # sorted(..., reverse=True) is stable).
        channels = [
            {"id": 1, "name": "100 | ESPN"},
            {"id": 2, "name": "200 | ESPN"},
            {"id": 3, "name": "Zeta"},
        ]
        sorted_channels = sort_channels_by_name(channels, order="desc")
        # "Zeta" sorts after "ESPN" (stripped), so Zeta comes first in desc.
        assert [c["id"] for c in sorted_channels] == [3, 1, 2]

    def test_custom_name_key(self):
        items = [{"label": "Channel 10"}, {"label": "Channel 2"}]
        sorted_items = sort_channels_by_name(items, name_key=lambda i: i["label"])
        assert [i["label"] for i in sorted_items] == ["Channel 2", "Channel 10"]
