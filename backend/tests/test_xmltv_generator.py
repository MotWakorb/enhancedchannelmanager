"""Tests for XMLTV EPG generator."""
import pytest
import xml.etree.ElementTree as ET
from xmltv_generator import generate_xmltv


@pytest.fixture
def sample_channels():
    return [
        {
            "id": 1,
            "name": "ESPN",
            "tvg_id": "espn.us",
            "logo_url": "https://example.com/espn.png",
            "channel_number": 206,
        },
        {
            "id": 2,
            "name": "CNN",
            "tvg_id": "cnn.us",
            "logo_url": "https://example.com/cnn.png",
            "channel_number": 200,
        },
    ]


@pytest.fixture
def sample_epg():
    return [
        {
            "channel_id": "espn.us",
            "start": "2026-03-07T12:00:00Z",
            "stop": "2026-03-07T13:00:00Z",
            "title": "SportsCenter",
            "description": "Daily sports news",
            "category": "Sports",
            "icon": "https://example.com/sc.png",
        },
        {
            "channel_id": "espn.us",
            "start": "2026-03-07T13:00:00Z",
            "stop": "2026-03-07T14:00:00Z",
            "title": "NBA Basketball",
            "description": "Lakers vs Celtics",
            "category": "Sports",
            "icon": "",
        },
        {
            "channel_id": "cnn.us",
            "start": "2026-03-07T12:00:00Z",
            "stop": "2026-03-07T13:00:00Z",
            "title": "CNN Newsroom",
            "description": "Breaking news coverage",
            "category": "News",
            "icon": "",
        },
        {
            # Programme for a channel NOT in the profile
            "channel_id": "fox.us",
            "start": "2026-03-07T12:00:00Z",
            "stop": "2026-03-07T13:00:00Z",
            "title": "Should Not Appear",
            "description": "",
            "category": "",
            "icon": "",
        },
    ]


@pytest.fixture
def default_profile():
    return {"include_logos": True}


def _parse_xmltv(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str)


def test_valid_xml_output(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    assert result.startswith('<?xml version="1.0"')
    # Should parse without error
    root = _parse_xmltv(result)
    assert root.tag == "tv"


def test_generator_info(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    assert root.get("generator-info-name") == "ECM Export"


def test_channel_elements(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    ch_elements = root.findall("channel")
    assert len(ch_elements) == 2

    ids = {ch.get("id") for ch in ch_elements}
    assert ids == {"espn.us", "cnn.us"}


def test_channel_display_name(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    espn_ch = [ch for ch in root.findall("channel") if ch.get("id") == "espn.us"][0]
    display = espn_ch.find("display-name")
    assert display is not None
    assert display.text == "ESPN"


def test_channel_icon(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    espn_ch = [ch for ch in root.findall("channel") if ch.get("id") == "espn.us"][0]
    icon = espn_ch.find("icon")
    assert icon is not None
    assert icon.get("src") == "https://example.com/espn.png"


def test_channel_icon_excluded_when_disabled(sample_channels, sample_epg):
    profile = {"include_logos": False}
    result = generate_xmltv(sample_channels, sample_epg, profile)
    root = _parse_xmltv(result)
    for ch in root.findall("channel"):
        assert ch.find("icon") is None


def test_programme_elements(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    # 3 programmes match (2 ESPN + 1 CNN), fox.us filtered out
    assert len(progs) == 3


def test_programme_filtered_to_profile_channels(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    prog_channels = {p.get("channel") for p in root.findall("programme")}
    assert "fox.us" not in prog_channels
    assert prog_channels == {"espn.us", "cnn.us"}


def test_programme_time_format(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    prog = root.findall("programme")[0]
    start = prog.get("start")
    stop = prog.get("stop")
    # Should be in YYYYMMDDHHMMSS +0000 format
    assert start.startswith("20260307")
    assert "+0000" in start
    assert stop.startswith("20260307")


def test_programme_title(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    sc_prog = [p for p in progs if p.find("title").text == "SportsCenter"][0]
    assert sc_prog.find("title").get("lang") == "en"


def test_programme_description(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    sc_prog = [p for p in progs if p.find("title").text == "SportsCenter"][0]
    desc = sc_prog.find("desc")
    assert desc is not None
    assert desc.text == "Daily sports news"


def test_programme_category(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    sc_prog = [p for p in progs if p.find("title").text == "SportsCenter"][0]
    cat = sc_prog.find("category")
    assert cat is not None
    assert cat.text == "Sports"


def test_programme_icon(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    sc_prog = [p for p in progs if p.find("title").text == "SportsCenter"][0]
    icon = sc_prog.find("icon")
    assert icon is not None
    assert icon.get("src") == "https://example.com/sc.png"


def test_empty_epg(sample_channels, default_profile):
    result = generate_xmltv(sample_channels, [], default_profile)
    root = _parse_xmltv(result)
    assert len(root.findall("programme")) == 0
    assert len(root.findall("channel")) == 2


def test_empty_channels(sample_epg, default_profile):
    result = generate_xmltv([], sample_epg, default_profile)
    root = _parse_xmltv(result)
    assert len(root.findall("channel")) == 0
    assert len(root.findall("programme")) == 0


def test_channel_without_tvg_id_skipped(sample_epg, default_profile):
    channels = [{
        "id": 1, "name": "NoEPG", "tvg_id": "",
        "logo_url": "", "channel_number": 1,
    }]
    result = generate_xmltv(channels, sample_epg, default_profile)
    root = _parse_xmltv(result)
    assert len(root.findall("channel")) == 0


def test_programme_missing_title_still_included(sample_channels, default_profile):
    epg = [{
        "channel_id": "espn.us",
        "start": "2026-03-07T12:00:00Z",
        "stop": "2026-03-07T13:00:00Z",
        "title": "",
        "description": "No title programme",
        "category": "",
        "icon": "",
    }]
    result = generate_xmltv(sample_channels, epg, default_profile)
    root = _parse_xmltv(result)
    progs = root.findall("programme")
    assert len(progs) == 1
    # Empty title should not create a <title> element
    assert progs[0].find("title") is None
    assert progs[0].find("desc").text == "No title programme"


def test_xmltv_doctype_present(sample_channels, sample_epg, default_profile):
    result = generate_xmltv(sample_channels, sample_epg, default_profile)
    assert '<!DOCTYPE tv SYSTEM "xmltv.dtd">' in result


def test_programme_with_channel_key(sample_channels, default_profile):
    """Some EPG sources use 'channel' instead of 'channel_id'."""
    epg = [{
        "channel": "espn.us",
        "start": "2026-03-07T12:00:00Z",
        "stop": "2026-03-07T13:00:00Z",
        "title": "Test Show",
        "description": "",
        "category": "",
        "icon": "",
    }]
    result = generate_xmltv(sample_channels, epg, default_profile)
    root = _parse_xmltv(result)
    assert len(root.findall("programme")) == 1
