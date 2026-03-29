"""Tests for stream_normalization module."""
import pytest

from stream_normalization import (
    normalize_unicode_to_ascii,
    strip_quality_suffixes,
    get_stream_quality_priority,
    get_quality_tier,
    strip_network_prefix,
    has_network_prefix,
    strip_network_suffix,
    get_country_prefix,
    strip_country_prefix,
    get_regional_suffix,
    strip_regional_suffix,
    sort_streams_by_quality,
    enrich_stream,
    DEFAULT_QUALITY_PRIORITY,
)


# ---------------------------------------------------------------------------
# Unicode normalization
# ---------------------------------------------------------------------------

class TestNormalizeUnicodeToAscii:
    def test_superscript_uhd(self):
        assert normalize_unicode_to_ascii("\u1D41\u1D34\u1D30") == "UHD"

    def test_full_width_letters(self):
        assert normalize_unicode_to_ascii("\uFF28\uFF24") == "HD"

    def test_subscript_numbers(self):
        assert normalize_unicode_to_ascii("\u2081\u2080\u2088\u2080") == "1080"

    def test_plain_ascii_passthrough(self):
        assert normalize_unicode_to_ascii("ESPN HD") == "ESPN HD"

    def test_mixed(self):
        assert normalize_unicode_to_ascii("CNN \u1D34\u1D30") == "CNN HD"


# ---------------------------------------------------------------------------
# Quality suffix stripping
# ---------------------------------------------------------------------------

class TestStripQualitySuffixes:
    def test_strip_hd(self):
        assert strip_quality_suffixes("ESPN HD") == "ESPN"

    def test_strip_fhd(self):
        assert strip_quality_suffixes("CNN FHD") == "CNN"

    def test_strip_4k(self):
        assert strip_quality_suffixes("BBC 4K") == "BBC"

    def test_strip_resolution(self):
        assert strip_quality_suffixes("Fox News 1080p") == "Fox News"

    def test_strip_arbitrary_resolution(self):
        assert strip_quality_suffixes("Channel 476p") == "Channel"

    def test_strip_with_separator(self):
        assert strip_quality_suffixes("ESPN | HD") == "ESPN"

    def test_no_suffix(self):
        assert strip_quality_suffixes("ESPN") == "ESPN"

    def test_unicode_quality(self):
        # Superscript UHD
        assert strip_quality_suffixes("BBC \u1D41\u1D34\u1D30") == "BBC"


# ---------------------------------------------------------------------------
# Quality priority
# ---------------------------------------------------------------------------

class TestGetStreamQualityPriority:
    def test_4k(self):
        assert get_stream_quality_priority("ESPN 4K") == 10

    def test_uhd(self):
        assert get_stream_quality_priority("ESPN UHD") == 10

    def test_fhd(self):
        assert get_stream_quality_priority("ESPN FHD") == 20

    def test_1080p(self):
        # 1080P uses dynamic formula: round(20000/1080) = 19
        assert get_stream_quality_priority("ESPN 1080P") == 19

    def test_hd(self):
        assert get_stream_quality_priority("ESPN HD") == 30

    def test_sd(self):
        assert get_stream_quality_priority("ESPN SD") == 40

    def test_no_quality(self):
        assert get_stream_quality_priority("ESPN") == DEFAULT_QUALITY_PRIORITY

    def test_arbitrary_resolution(self):
        priority = get_stream_quality_priority("Channel 476P")
        assert 5 <= priority <= 60

    def test_2160p(self):
        # 2160P uses dynamic formula: round(20000/2160) = 9
        assert get_stream_quality_priority("ESPN 2160P") == 9


class TestGetQualityTier:
    def test_4k_tier(self):
        assert get_quality_tier("ESPN 4K") == "4K"

    def test_fhd_tier(self):
        assert get_quality_tier("ESPN FHD") == "FHD"

    def test_hd_tier(self):
        assert get_quality_tier("ESPN HD") == "HD"

    def test_sd_tier(self):
        assert get_quality_tier("ESPN SD") == "SD"

    def test_unknown_tier(self):
        assert get_quality_tier("ESPN") == "HD"  # Default


# ---------------------------------------------------------------------------
# Network prefix/suffix
# ---------------------------------------------------------------------------

class TestNetworkPrefix:
    def test_strip_ppv_prefix(self):
        assert strip_network_prefix("PPV | UFC 300") == "UFC 300"

    def test_strip_nfl_prefix(self):
        assert strip_network_prefix("NFL: Arizona Cardinals") == "Arizona Cardinals"

    def test_no_prefix(self):
        assert strip_network_prefix("ESPN HD") == "ESPN HD"

    def test_has_prefix(self):
        assert has_network_prefix("PPV | UFC 300") is True
        assert has_network_prefix("ESPN HD") is False

    def test_short_content_not_stripped(self):
        # Content must be >= 3 chars
        assert strip_network_prefix("PPV | AB") == "PPV | AB"


class TestNetworkSuffix:
    def test_strip_english_in_parens(self):
        assert strip_network_suffix("BBC One (ENGLISH)") == "BBC One"

    def test_strip_live_in_brackets(self):
        assert strip_network_suffix("ESPN [LIVE]") == "ESPN"

    def test_strip_backup_bare(self):
        assert strip_network_suffix("CNN - BACKUP") == "CNN"

    def test_strip_with_space(self):
        assert strip_network_suffix("Fox News BACKUP") == "Fox News"

    def test_no_suffix(self):
        assert strip_network_suffix("ESPN") == "ESPN"


# ---------------------------------------------------------------------------
# Country prefix
# ---------------------------------------------------------------------------

class TestCountryPrefix:
    def test_detect_us(self):
        assert get_country_prefix("US | ESPN") == "US"

    def test_detect_uk(self):
        assert get_country_prefix("UK: BBC One") == "UK"

    def test_no_prefix(self):
        assert get_country_prefix("ESPN HD") is None

    def test_strip_us(self):
        assert strip_country_prefix("US | ESPN") == "ESPN"

    def test_strip_uk(self):
        assert strip_country_prefix("UK: BBC One") == "BBC One"

    def test_strip_leading_separators(self):
        assert get_country_prefix("| UK | BBC One") == "UK"

    def test_strip_no_prefix(self):
        assert strip_country_prefix("ESPN HD") == "ESPN HD"


# ---------------------------------------------------------------------------
# Regional variants
# ---------------------------------------------------------------------------

class TestRegionalVariants:
    def test_detect_east(self):
        assert get_regional_suffix("ESPN EAST") == "east"

    def test_detect_west(self):
        assert get_regional_suffix("ESPN WEST") == "west"

    def test_no_region(self):
        assert get_regional_suffix("ESPN") is None

    def test_strip_east(self):
        assert strip_regional_suffix("ESPN EAST") == "ESPN"

    def test_strip_west(self):
        assert strip_regional_suffix("ESPN WEST") == "ESPN"


# ---------------------------------------------------------------------------
# Quality sorting
# ---------------------------------------------------------------------------

class TestSortStreamsByQuality:
    def test_sorts_by_quality(self):
        streams = [
            {"name": "ESPN SD", "m3u_account": 1},
            {"name": "ESPN 4K", "m3u_account": 1},
            {"name": "ESPN HD", "m3u_account": 1},
        ]
        result = sort_streams_by_quality(streams)
        assert result[0]["name"] == "ESPN 4K"
        assert result[1]["name"] == "ESPN HD"
        assert result[2]["name"] == "ESPN SD"

    def test_interleaves_providers(self):
        streams = [
            {"name": "ESPN HD", "m3u_account": 1},
            {"name": "ESPN HD", "m3u_account": 2},
            {"name": "ESPN HD", "m3u_account": 1},
        ]
        result = sort_streams_by_quality(streams)
        # Should interleave: provider 1, provider 2, provider 1
        assert result[0]["m3u_account"] == 1
        assert result[1]["m3u_account"] == 2
        assert result[2]["m3u_account"] == 1

    def test_empty_list(self):
        assert sort_streams_by_quality([]) == []


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

class TestEnrichStream:
    def test_basic_enrichment(self):
        stream = {"id": 1, "name": "US | ESPN HD"}
        result = enrich_stream(stream)
        assert result.quality_tier == "HD"
        assert result.quality_priority == 30
        assert result.detected_country == "US"
        assert result.normalized_name == "ESPN"

    def test_4k_enrichment(self):
        stream = {"id": 2, "name": "BBC 4K"}
        result = enrich_stream(stream)
        assert result.quality_tier == "4K"
        assert result.quality_priority == 10
        assert result.detected_country is None

    def test_regional_enrichment(self):
        stream = {"id": 3, "name": "Fox News HD WEST"}
        result = enrich_stream(stream)
        assert result.regional_variant == "west"
