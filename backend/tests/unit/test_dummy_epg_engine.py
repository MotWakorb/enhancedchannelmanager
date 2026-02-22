"""
Unit tests for the Dummy EPG generation engine.
"""
import xml.etree.ElementTree as ET

import pytest
import pytz

from dummy_epg_engine import (
    _js_to_python_named_groups,
    apply_substitutions,
    compute_event_times,
    extract_groups,
    generate_xmltv,
    preview_pipeline,
    render_template,
)


# ---------------------------------------------------------------------------
# _js_to_python_named_groups
# ---------------------------------------------------------------------------


def test_js_named_group_converted_to_python():
    """JS (?<name>...) becomes Python (?P<name>...)."""
    assert _js_to_python_named_groups("(?<title>.+)") == "(?P<title>.+)"


def test_js_multiple_named_groups():
    """Multiple JS named groups are all converted."""
    pattern = "(?<team>.+) vs (?<opponent>.+)"
    result = _js_to_python_named_groups(pattern)
    assert result == "(?P<team>.+) vs (?P<opponent>.+)"


def test_js_lookahead_not_converted():
    """Positive lookahead (?=...) must not be converted."""
    assert _js_to_python_named_groups("foo(?=bar)") == "foo(?=bar)"


def test_js_negative_lookahead_not_converted():
    """Negative lookahead (?!...) must not be converted."""
    assert _js_to_python_named_groups("foo(?!bar)") == "foo(?!bar)"


def test_js_empty_string():
    """Empty string returns empty string."""
    assert _js_to_python_named_groups("") == ""


def test_js_none_returns_none():
    """None input returns None."""
    assert _js_to_python_named_groups(None) is None


def test_js_no_named_groups():
    """Pattern with no named groups passes through unchanged."""
    assert _js_to_python_named_groups("(\\d+)-(\\w+)") == "(\\d+)-(\\w+)"


def test_js_python_style_already():
    """Already-Python (?P<name>) patterns pass through unchanged."""
    assert _js_to_python_named_groups("(?P<title>.+)") == "(?P<title>.+)"


# ---------------------------------------------------------------------------
# apply_substitutions
# ---------------------------------------------------------------------------


def test_plain_text_substitution():
    """Plain text find/replace works."""
    pairs = [{"find": "FOO", "replace": "BAR", "is_regex": False, "enabled": True}]
    result, steps = apply_substitutions("hello FOO world", pairs)
    assert result == "hello BAR world"
    assert len(steps) == 1
    assert steps[0]["before"] == "hello FOO world"
    assert steps[0]["after"] == "hello BAR world"


def test_regex_substitution():
    """Regex substitution works."""
    pairs = [{"find": r"\d+", "replace": "#", "is_regex": True, "enabled": True}]
    result, _steps = apply_substitutions("abc123def456", pairs)
    assert result == "abc#def#"


def test_disabled_pair_skipped():
    """Disabled substitution pairs are not applied."""
    pairs = [{"find": "X", "replace": "Y", "is_regex": False, "enabled": False}]
    result, steps = apply_substitutions("X marks the spot", pairs)
    assert result == "X marks the spot"
    assert len(steps) == 0


def test_multiple_substitutions_applied_in_order():
    """Multiple pairs are applied sequentially."""
    pairs = [
        {"find": "A", "replace": "B", "is_regex": False, "enabled": True},
        {"find": "B", "replace": "C", "is_regex": False, "enabled": True},
    ]
    result, steps = apply_substitutions("A", pairs)
    assert result == "C"
    assert len(steps) == 2


def test_no_match_produces_no_step():
    """A pair that doesn't match produces no step entry."""
    pairs = [{"find": "ZZZ", "replace": "YYY", "is_regex": False, "enabled": True}]
    result, steps = apply_substitutions("hello", pairs)
    assert result == "hello"
    assert len(steps) == 0


def test_invalid_regex_gracefully_skipped():
    """Invalid regex in a pair is skipped without error."""
    pairs = [{"find": "[invalid", "replace": "", "is_regex": True, "enabled": True}]
    result, steps = apply_substitutions("test [invalid data", pairs)
    assert result == "test [invalid data"
    assert len(steps) == 0


def test_empty_pairs_list():
    """Empty pairs list returns name unchanged."""
    result, steps = apply_substitutions("unchanged", [])
    assert result == "unchanged"
    assert steps == []


def test_regex_capture_group_replacement():
    """Regex substitution with capture group back-reference."""
    pairs = [{"find": r"(\w+)@(\w+)", "replace": r"\2/\1", "is_regex": True, "enabled": True}]
    result, _steps = apply_substitutions("user@host", pairs)
    assert result == "host/user"


def test_substitution_enabled_defaults_true():
    """Pairs without explicit 'enabled' key default to enabled."""
    pairs = [{"find": "X", "replace": "Y", "is_regex": False}]
    result, steps = apply_substitutions("X", pairs)
    assert result == "Y"
    assert len(steps) == 1


# ---------------------------------------------------------------------------
# extract_groups
# ---------------------------------------------------------------------------


def test_extract_title_groups():
    """Title pattern extracts named groups."""
    groups = extract_groups("PBL: Wolves vs Hawks", r"(?P<league>\w+): (?P<title>.+)")
    assert groups is not None
    assert groups["league"] == "PBL"
    assert groups["title"] == "Wolves vs Hawks"


def test_extract_with_js_named_groups():
    """JS-style named groups are auto-converted and work."""
    groups = extract_groups("PBL: Wolves vs Hawks", r"(?<league>\w+): (?<title>.+)")
    assert groups is not None
    assert groups["league"] == "PBL"
    assert groups["title"] == "Wolves vs Hawks"


def test_extract_no_match_returns_none():
    """Non-matching title pattern returns None."""
    groups = extract_groups("random text", r"(?P<team>TEAM\d+)")
    assert groups is None


def test_extract_empty_pattern_returns_none():
    """Empty title pattern returns None."""
    groups = extract_groups("anything", "")
    assert groups is None


def test_extract_none_pattern_returns_none():
    """None title pattern returns None."""
    groups = extract_groups("anything", None)
    assert groups is None


def test_extract_invalid_regex_returns_none():
    """Invalid regex in title_pattern returns None gracefully."""
    groups = extract_groups("test", "[unclosed")
    assert groups is None


def test_extract_time_pattern_merges():
    """Time pattern groups are merged with title groups."""
    groups = extract_groups(
        "Game 7pm Wolves",
        r"(?P<title>Game)",
        time_pattern=r"(?P<hour>\d+)(?P<ampm>pm)",
    )
    assert groups["title"] == "Game"
    assert groups["hour"] == "7"
    assert groups["ampm"] == "pm"


def test_extract_date_pattern_merges():
    """Date pattern groups are merged with title groups."""
    groups = extract_groups(
        "Game 03/15 Wolves",
        r"(?P<title>Game)",
        date_pattern=r"(?P<month>\d{2})/(?P<day>\d{2})",
    )
    assert groups["title"] == "Game"
    assert groups["month"] == "03"
    assert groups["day"] == "15"


def test_extract_time_pattern_invalid_regex_still_returns_title():
    """Invalid time_pattern does not prevent title groups from returning."""
    groups = extract_groups("Game", r"(?P<title>Game)", time_pattern="[bad")
    assert groups is not None
    assert groups["title"] == "Game"


def test_extract_date_pattern_invalid_regex_still_returns_title():
    """Invalid date_pattern does not prevent title groups from returning."""
    groups = extract_groups("Game", r"(?P<title>Game)", date_pattern="[bad")
    assert groups is not None
    assert groups["title"] == "Game"


def test_extract_time_pattern_no_match_keeps_title():
    """Non-matching time pattern still returns title groups."""
    groups = extract_groups("Game", r"(?P<title>Game)", time_pattern=r"(?P<hour>\d+)pm")
    assert groups is not None
    assert groups["title"] == "Game"
    assert "hour" not in groups


def test_extract_js_named_groups_in_time_and_date():
    """JS-style named groups in time and date patterns are auto-converted."""
    groups = extract_groups(
        "Game 7pm 03/15",
        r"(?<title>Game)",
        time_pattern=r"(?<hour>\d+)(?<ampm>pm)",
        date_pattern=r"(?<month>\d{2})/(?<day>\d{2})",
    )
    assert groups["title"] == "Game"
    assert groups["hour"] == "7"
    assert groups["month"] == "03"


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def test_render_simple_placeholder():
    """Simple {key} placeholder is replaced."""
    result = render_template("{title} Live", {"title": "Wolves vs Hawks"})
    assert result == "Wolves vs Hawks Live"


def test_render_normalize_placeholder():
    """The {key_normalize} suffix lowercases and strips non-alphanumeric."""
    result = render_template("{title_normalize}", {"title": "Wolves vs. Hawks!"})
    assert result == "wolvesvshawks"


def test_render_missing_key_left_as_is():
    """Unknown placeholders remain in the output."""
    result = render_template("{unknown}", {})
    assert result == "{unknown}"


def test_render_empty_template():
    """Empty template returns empty string."""
    assert render_template("", {"title": "test"}) == ""


def test_render_none_template():
    """None template returns empty string."""
    assert render_template(None, {"title": "test"}) == ""


def test_render_multiple_placeholders():
    """Multiple different placeholders are all replaced."""
    result = render_template("{team} at {venue}", {"team": "Wolves", "venue": "Metro Arena"})
    assert result == "Wolves at Metro Arena"


def test_render_normalize_empty_value():
    """Normalize of empty value produces empty string."""
    result = render_template("{title_normalize}", {"title": ""})
    assert result == ""


def test_render_normalize_missing_key():
    """Normalize of missing key produces empty string."""
    result = render_template("{missing_normalize}", {})
    assert result == ""


def test_render_integer_value():
    """Integer values are converted to string."""
    result = render_template("Ch {channel_number}", {"channel_number": 42})
    assert result == "Ch 42"


def test_render_no_placeholders():
    """Template with no placeholders passes through unchanged."""
    assert render_template("plain text", {}) == "plain text"


# ---------------------------------------------------------------------------
# compute_event_times
# ---------------------------------------------------------------------------


def test_compute_times_basic_24h():
    """24-hour time without ampm produces correct hour."""
    groups = {"hour": "14", "minute": "30"}
    result = compute_event_times(groups, "America/New_York")
    assert result["starttime24"] == "14:30"
    assert result["start_dt"].hour == 14
    assert result["start_dt"].minute == 30


def test_compute_times_am():
    """AM time correctly parsed (9 AM stays 9)."""
    groups = {"hour": "9", "minute": "00", "ampm": "AM"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 9


def test_compute_times_pm():
    """PM time correctly converted (3 PM becomes 15)."""
    groups = {"hour": "3", "minute": "00", "ampm": "PM"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 15


def test_compute_times_12am_is_midnight():
    """12 AM is midnight (hour 0)."""
    groups = {"hour": "12", "minute": "00", "ampm": "AM"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 0


def test_compute_times_12pm_is_noon():
    """12 PM stays as noon (hour 12)."""
    groups = {"hour": "12", "minute": "00", "ampm": "PM"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 12


def test_compute_times_pm_lowercase():
    """Lowercase 'pm' is handled."""
    groups = {"hour": "5", "minute": "00", "ampm": "pm"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 17


def test_compute_times_ampm_short_p():
    """Short 'p' style ampm is handled as PM."""
    groups = {"hour": "5", "minute": "00", "ampm": "p"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].hour == 17


def test_compute_times_duration():
    """Default 180-min duration yields correct end time."""
    groups = {"hour": "10", "minute": "00"}
    result = compute_event_times(groups, "America/New_York", program_duration=180)
    assert result["end_dt"].hour == 13
    assert result["end_dt"].minute == 0


def test_compute_times_custom_duration():
    """Custom duration is respected."""
    groups = {"hour": "10", "minute": "00"}
    result = compute_event_times(groups, "America/New_York", program_duration=60)
    assert result["end_dt"].hour == 11


def test_compute_times_explicit_date():
    """Explicit month/day/year are used."""
    groups = {"hour": "12", "minute": "00", "month": "3", "day": "15", "year": "2025"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].month == 3
    assert result["start_dt"].day == 15
    assert result["start_dt"].year == 2025


def test_compute_times_month_name():
    """Month can be given as a name string."""
    groups = {"hour": "12", "minute": "00", "month": "March", "day": "15"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].month == 3


def test_compute_times_two_digit_year():
    """Two-digit year has 2000 added."""
    groups = {"hour": "12", "minute": "00", "year": "25"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"].year == 2025


def test_compute_times_output_timezone_conversion():
    """Output timezone converts times correctly."""
    groups = {"hour": "12", "minute": "00", "month": "6", "day": "15", "year": "2025"}
    result = compute_event_times(groups, "America/New_York", output_timezone="America/Los_Angeles")
    # Eastern noon -> Pacific 9 AM (EDT is UTC-4, PDT is UTC-7)
    assert result["start_dt"].hour == 9


def test_compute_times_returns_formatted_strings():
    """Result includes all expected formatted time/date strings."""
    groups = {"hour": "14", "minute": "30"}
    result = compute_event_times(groups, "America/New_York")
    assert "starttime" in result
    assert "starttime24" in result
    assert "endtime" in result
    assert "endtime24" in result
    assert "date" in result
    assert "month" in result
    assert "day" in result
    assert "year" in result


def test_compute_times_no_hour_uses_current():
    """Missing hour defaults to current hour."""
    groups = {}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"] is not None


def test_compute_times_invalid_output_timezone_ignored():
    """Invalid output timezone is ignored; event timezone times preserved."""
    groups = {"hour": "12", "minute": "00"}
    result = compute_event_times(groups, "America/New_York", output_timezone="Invalid/Zone")
    assert result["start_dt"].hour == 12


def test_compute_times_invalid_day_falls_back():
    """Invalid day value falls back to current day."""
    groups = {"hour": "12", "minute": "00", "day": "notaday"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"] is not None


def test_compute_times_invalid_year_falls_back():
    """Invalid year value falls back to current year."""
    groups = {"hour": "12", "minute": "00", "year": "abc"}
    result = compute_event_times(groups, "America/New_York")
    assert result["start_dt"] is not None


# ---------------------------------------------------------------------------
# preview_pipeline
# ---------------------------------------------------------------------------


def test_preview_pipeline_matched():
    """Full pipeline with matching pattern returns matched=True and rendered templates."""
    config = {
        "substitution_pairs": [
            {"find": "USA: ", "replace": "", "is_regex": False, "enabled": True},
        ],
        "title_pattern": r"(?P<title>.+) (?P<hour>\d+):(?P<minute>\d+)(?P<ampm>[AP]M)",
        "title_template": "{title}",
        "description_template": "Starts at {starttime}",
        "event_timezone": "America/New_York",
        "program_duration": 120,
    }
    result = preview_pipeline(config, "USA: Wolves vs Hawks 7:00PM")
    assert result["matched"] is True
    assert result["original_name"] == "USA: Wolves vs Hawks 7:00PM"
    assert result["substituted_name"] == "Wolves vs Hawks 7:00PM"
    assert result["groups"]["title"] == "Wolves vs Hawks"
    assert result["groups"]["hour"] == "7"
    assert result["rendered"]["title"] == "Wolves vs Hawks"
    assert result["time_variables"] is not None
    assert len(result["substitution_steps"]) == 1


def test_preview_pipeline_no_match():
    """Pipeline with non-matching pattern returns matched=False."""
    config = {
        "title_pattern": r"(?P<title>NOMATCH\d+)",
        "fallback_title_template": "{original_name}",
    }
    result = preview_pipeline(config, "Some Random Name")
    assert result["matched"] is False
    assert result["groups"] is None
    assert result["time_variables"] is None
    assert result["rendered"]["fallback_title"] == "Some Random Name"


def test_preview_pipeline_no_substitutions():
    """Pipeline with empty substitutions passes name through unchanged."""
    config = {
        "substitution_pairs": [],
        "title_pattern": r"(?P<title>.+)",
        "title_template": "{title}",
        "event_timezone": "America/New_York",
    }
    result = preview_pipeline(config, "Test Name")
    assert result["substituted_name"] == "Test Name"
    assert result["matched"] is True


def test_preview_pipeline_time_variables_exclude_datetime():
    """Time variables in pipeline result do not contain datetime objects."""
    config = {
        "title_pattern": r"(?P<title>.+) (?P<hour>\d+):(?P<minute>\d+)",
        "title_template": "{title}",
        "event_timezone": "America/New_York",
    }
    result = preview_pipeline(config, "Game 14:30")
    for v in result["time_variables"].values():
        assert not hasattr(v, "astimezone"), f"datetime object leaked: {v}"


def test_preview_pipeline_all_rendered_keys_present():
    """Pipeline result always has all rendered template keys."""
    config = {
        "title_pattern": r"(?P<title>.+)",
        "title_template": "{title}",
        "event_timezone": "America/New_York",
    }
    result = preview_pipeline(config, "Test")
    expected_keys = {
        "title", "description",
        "upcoming_title", "upcoming_description",
        "ended_title", "ended_description",
        "fallback_title", "fallback_description",
        "channel_logo_url", "program_poster_url",
    }
    assert set(result["rendered"].keys()) == expected_keys


def test_preview_pipeline_fallback_renders_logo_url():
    """Unmatched pipeline still renders channel_logo_url from base groups."""
    config = {
        "title_pattern": r"NOMATCH",
        "channel_logo_url_template": "https://img.example.com/{original_name_normalize}.png",
    }
    result = preview_pipeline(config, "Sports One HD")
    assert result["rendered"]["channel_logo_url"] == "https://img.example.com/sportsonehd.png"


# ---------------------------------------------------------------------------
# generate_xmltv
# ---------------------------------------------------------------------------


def test_generate_xmltv_valid_xml():
    """generate_xmltv produces parseable XML with correct root element."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {
        1: {"name": "Sports One", "channel_number": 100, "streams": []},
    }
    xml_str = generate_xmltv(profiles, channel_data)
    assert xml_str.startswith('<?xml version="1.0"')
    root = ET.fromstring(xml_str)
    assert root.tag == "tv"
    assert root.get("generator-info-name") == "ECM Enhanced Channel Manager"


def test_generate_xmltv_channel_element():
    """Channel element has correct id and display-name."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    channels = root.findall("channel")
    assert len(channels) == 1
    assert channels[0].get("id") == "ecm-100"
    assert channels[0].find("display-name").text == "Sports One"


def test_generate_xmltv_programme_elements():
    """At least one programme element is created per channel."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    programmes = root.findall("programme")
    assert len(programmes) >= 1
    assert programmes[0].get("channel") == "ecm-100"
    assert programmes[0].find("title").text is not None


def test_generate_xmltv_disabled_profile_skipped():
    """Disabled profiles produce no output."""
    profiles = [
        {
            "enabled": False,
            "title_pattern": r"(?P<title>.+)",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    assert len(root.findall("channel")) == 0
    assert len(root.findall("programme")) == 0


def test_generate_xmltv_missing_channel_skipped():
    """Channel IDs not in channel_data are silently skipped."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "channel_assignments": [{"channel_id": 999}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    assert len(root.findall("channel")) == 0


def test_generate_xmltv_tvg_id_override():
    """Assignment-level tvg_id_override takes precedence over template."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [
                {"channel_id": 1, "tvg_id_override": "custom-espn"},
            ],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    assert root.findall("channel")[0].get("id") == "custom-espn"


def test_generate_xmltv_multiple_channels():
    """Multiple channel assignments produce multiple channel and programme elements."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [
                {"channel_id": 1},
                {"channel_id": 2},
            ],
        }
    ]
    channel_data = {
        1: {"name": "Sports One", "channel_number": 100, "streams": []},
        2: {"name": "Sports Plus", "channel_number": 200, "streams": []},
    }
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    assert len(root.findall("channel")) == 2
    assert len(root.findall("programme")) >= 2


def test_generate_xmltv_categories():
    """Categories in profile appear as <category> elements."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "categories": "Sports, Live",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    prog = root.findall("programme")[0]
    cats = [c.text for c in prog.findall("category")]
    assert "Sports" in cats
    assert "Live" in cats


def test_generate_xmltv_fallback_when_no_match():
    """Non-matching pattern uses fallback title template."""
    profiles = [
        {
            "enabled": True,
            "title_pattern": r"NOMATCH",
            "fallback_title_template": "Fallback: {channel_name}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {1: {"name": "Sports One", "channel_number": 100, "streams": []}}
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    prog = root.findall("programme")[0]
    assert prog.find("title").text == "Fallback: Sports One"


def test_generate_xmltv_stream_name_source():
    """name_source='stream' uses stream name instead of channel name."""
    profiles = [
        {
            "enabled": True,
            "name_source": "stream",
            "stream_index": 1,
            "title_pattern": r"(?P<title>.+)",
            "title_template": "{title}",
            "event_timezone": "America/New_York",
            "tvg_id_template": "ecm-{channel_number}",
            "channel_assignments": [{"channel_id": 1}],
        }
    ]
    channel_data = {
        1: {
            "name": "Sports One",
            "channel_number": 100,
            "streams": [{"name": "Sports One HD Live Feed"}],
        },
    }
    xml_str = generate_xmltv(profiles, channel_data)
    root = ET.fromstring(xml_str)
    prog = root.findall("programme")[0]
    assert prog.find("title").text == "Sports One HD Live Feed"


def test_generate_xmltv_empty_profiles():
    """Empty profiles list produces valid but empty XMLTV."""
    xml_str = generate_xmltv([], {})
    root = ET.fromstring(xml_str)
    assert root.tag == "tv"
    assert len(root.findall("channel")) == 0
    assert len(root.findall("programme")) == 0
