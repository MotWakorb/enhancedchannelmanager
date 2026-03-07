"""Tests for M3U playlist generator."""
import pytest
from m3u_generator import generate_m3u


@pytest.fixture
def sample_channels():
    return [
        {
            "id": 1,
            "name": "ESPN",
            "channel_number": 206,
            "channel_group_name": "Sports",
            "logo_url": "https://example.com/espn.png",
            "tvg_id": "espn.us",
            "streams": [101],
        },
        {
            "id": 2,
            "name": "CNN",
            "channel_number": 200,
            "channel_group_name": "News",
            "logo_url": "https://example.com/cnn.png",
            "tvg_id": "cnn.us",
            "streams": [102],
        },
        {
            "id": 3,
            "name": "HBO",
            "channel_number": 501,
            "channel_group_name": "Premium",
            "logo_url": "",
            "tvg_id": "",
            "streams": [103],
        },
    ]


@pytest.fixture
def stream_lookup():
    return {
        101: {"id": 101, "url": "http://stream.example.com/espn"},
        102: {"id": 102, "url": "http://stream.example.com/cnn"},
        103: {"id": 103, "url": "http://stream.example.com/hbo"},
    }


@pytest.fixture
def default_profile():
    return {
        "include_logos": True,
        "include_epg_ids": True,
        "include_channel_numbers": True,
        "stream_url_mode": "direct",
    }


def test_valid_m3u_header(sample_channels, stream_lookup, default_profile):
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert result.startswith("#EXTM3U\n")


def test_all_channels_present(sample_channels, stream_lookup, default_profile):
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert "ESPN" in result
    assert "CNN" in result
    assert "HBO" in result


def test_tvg_attributes_present(sample_channels, stream_lookup, default_profile):
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert 'tvg-id="espn.us"' in result
    assert 'tvg-name="ESPN"' in result
    assert 'tvg-logo="https://example.com/espn.png"' in result
    assert 'tvg-chno="206"' in result
    assert 'group-title="Sports"' in result


def test_stream_urls_present(sample_channels, stream_lookup, default_profile):
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert "http://stream.example.com/espn" in result
    assert "http://stream.example.com/cnn" in result


def test_exclude_logos(sample_channels, stream_lookup, default_profile):
    default_profile["include_logos"] = False
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert "tvg-logo" not in result


def test_exclude_epg_ids(sample_channels, stream_lookup, default_profile):
    default_profile["include_epg_ids"] = False
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert "tvg-id" not in result


def test_exclude_channel_numbers(sample_channels, stream_lookup, default_profile):
    default_profile["include_channel_numbers"] = False
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    assert "tvg-chno" not in result


def test_missing_logo_no_attr(sample_channels, stream_lookup, default_profile):
    """Channel with empty logo_url should not have tvg-logo attribute."""
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    # HBO has no logo — find its EXTINF line
    lines = result.split("\n")
    hbo_line = [l for l in lines if l.endswith(",HBO")]
    assert len(hbo_line) == 1
    assert "tvg-logo" not in hbo_line[0]


def test_missing_epg_id_empty_string(sample_channels, stream_lookup, default_profile):
    """Channel with empty tvg_id should still have tvg-id="" if epg_ids enabled."""
    result = generate_m3u(sample_channels, default_profile, stream_lookup)
    lines = result.split("\n")
    hbo_line = [l for l in lines if l.endswith(",HBO")]
    assert len(hbo_line) == 1
    assert 'tvg-id=""' in hbo_line[0]


def test_special_characters_in_name(stream_lookup, default_profile):
    channels = [{
        "id": 10,
        "name": 'Channel "Special" & <Test>',
        "channel_number": 1,
        "channel_group_name": "Test",
        "logo_url": "",
        "tvg_id": "test",
        "streams": [101],
    }]
    result = generate_m3u(channels, default_profile, stream_lookup)
    # Quotes in attr should be escaped to single quotes
    assert "tvg-name=\"Channel 'Special' & <Test>\"" in result
    # Display name after comma preserves original (except newlines)
    assert ',Channel "Special" & <Test>' in result


def test_empty_channel_list(default_profile):
    result = generate_m3u([], default_profile, {})
    assert result.strip() == "#EXTM3U"


def test_channel_no_streams_skipped(default_profile):
    channels = [{
        "id": 1, "name": "NoStream", "channel_number": 1,
        "channel_group_name": "", "logo_url": "", "tvg_id": "",
        "streams": [],
    }]
    result = generate_m3u(channels, default_profile, {})
    assert "NoStream" not in result


def test_channel_with_direct_stream_url(default_profile):
    """Channels with stream_url key bypass stream_lookup."""
    channels = [{
        "id": 1, "name": "Direct", "channel_number": 1,
        "channel_group_name": "", "logo_url": "", "tvg_id": "",
        "streams": [], "stream_url": "http://direct.example.com/live",
    }]
    result = generate_m3u(channels, default_profile)
    assert "http://direct.example.com/live" in result


def test_float_channel_number_whole(stream_lookup, default_profile):
    """Float channel number that's a whole number should render without decimal."""
    channels = [{
        "id": 1, "name": "Test", "channel_number": 5.0,
        "channel_group_name": "", "logo_url": "", "tvg_id": "",
        "streams": [101],
    }]
    result = generate_m3u(channels, default_profile, stream_lookup)
    assert 'tvg-chno="5"' in result


def test_newlines_in_name_stripped(stream_lookup, default_profile):
    channels = [{
        "id": 1, "name": "Line\nBreak", "channel_number": 1,
        "channel_group_name": "", "logo_url": "", "tvg_id": "",
        "streams": [101],
    }]
    result = generate_m3u(channels, default_profile, stream_lookup)
    assert "\nBreak" not in result
    assert "Line Break" in result
