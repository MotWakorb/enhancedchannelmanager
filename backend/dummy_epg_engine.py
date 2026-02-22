"""
Dummy EPG generation engine.

Generates XMLTV XML from channel/stream names using regex pattern matching,
substitution pairs, and template rendering.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import pytz

logger = logging.getLogger(__name__)


def _js_to_python_named_groups(pattern: str) -> str:
    """Convert JavaScript-style named groups (?<name>...) to Python (?P<name>...)."""
    if not pattern:
        return pattern
    return re.sub(r"\(\?<(?!\=|\!)", "(?P<", pattern)


# Month name lookup for parsing
MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

MONTH_FULL_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def apply_substitutions(name: str, pairs: list[dict]) -> tuple[str, list[dict]]:
    """
    Apply substitution pairs to a name string in order.

    Args:
        name: The input name to transform.
        pairs: List of dicts with keys: find, replace, is_regex, enabled.

    Returns:
        Tuple of (final_name, steps) where steps records each pair that
        actually changed the name.
    """
    steps = []
    current = name

    for pair in pairs:
        if not pair.get("enabled", True):
            continue

        find = pair.get("find", "")
        replace = pair.get("replace", "")
        is_regex = pair.get("is_regex", False)
        before = current

        if is_regex:
            try:
                current = re.sub(find, replace, current)
            except re.error as e:
                logger.warning(
                    "[DUMMY-EPG] Invalid regex in substitution find=%s: %s",
                    find, e,
                )
                continue
        else:
            current = current.replace(find, replace)

        if current != before:
            steps.append({
                "find": find,
                "replace": replace,
                "is_regex": is_regex,
                "before": before,
                "after": current,
            })

    return current, steps


def extract_groups(
    name: str,
    title_pattern: str,
    time_pattern: str = None,
    date_pattern: str = None,
) -> dict | None:
    """
    Apply regex patterns to extract named groups from a name string.

    Args:
        name: The name to match against.
        title_pattern: Primary regex pattern (required to match).
        time_pattern: Optional regex for time extraction.
        date_pattern: Optional regex for date extraction.

    Returns:
        Merged dict of named groups, or None if title_pattern doesn't match.
    """
    if not title_pattern:
        return None

    # Convert JS-style (?<name>...) to Python (?P<name>...)
    title_pattern = _js_to_python_named_groups(title_pattern)
    time_pattern = _js_to_python_named_groups(time_pattern)
    date_pattern = _js_to_python_named_groups(date_pattern)

    try:
        title_match = re.search(title_pattern, name)
    except re.error as e:
        logger.warning("[DUMMY-EPG] Invalid title_pattern regex: %s", e)
        return None

    if not title_match:
        return None

    groups = dict(title_match.groupdict())

    if time_pattern:
        try:
            time_match = re.search(time_pattern, name)
            if time_match:
                groups.update(time_match.groupdict())
        except re.error as e:
            logger.warning("[DUMMY-EPG] Invalid time_pattern regex: %s", e)

    if date_pattern:
        try:
            date_match = re.search(date_pattern, name)
            if date_match:
                groups.update(date_match.groupdict())
        except re.error as e:
            logger.warning("[DUMMY-EPG] Invalid date_pattern regex: %s", e)

    return groups


def render_template(template: str, groups: dict) -> str:
    """
    Replace {key} placeholders in template with values from groups.

    Supports:
      - {key} -> groups[key]
      - {key_normalize} -> groups[key].lower() with non-alphanumeric removed
      - Time/date variables from groups: {starttime}, {starttime24}, etc.

    Args:
        template: Template string with {placeholder} tokens.
        groups: Dict of values to substitute.

    Returns:
        Rendered string, or empty string if template is empty/None.
    """
    if not template:
        return ""

    result = template

    # Find all placeholders
    placeholders = re.findall(r"\{(\w+)\}", result)

    for placeholder in placeholders:
        # Handle _normalize suffix
        if placeholder.endswith("_normalize"):
            base_key = placeholder[: -len("_normalize")]
            value = groups.get(base_key, "")
            if value:
                normalized = re.sub(r"[^a-z0-9]", "", value.lower())
                result = result.replace("{%s}" % placeholder, normalized)
            else:
                result = result.replace("{%s}" % placeholder, "")
        elif placeholder in groups:
            result = result.replace(
                "{%s}" % placeholder, str(groups[placeholder])
            )

    return result


def _parse_month(value: str) -> int | None:
    """Parse a month value as integer or name."""
    if not value:
        return None
    try:
        month_int = int(value)
        if 1 <= month_int <= 12:
            return month_int
        return None
    except ValueError:
        return MONTH_NAMES.get(value.lower().strip())


def compute_event_times(
    groups: dict,
    event_timezone: str,
    output_timezone: str = None,
    program_duration: int = 180,
) -> dict:
    """
    Compute event start/end times from extracted groups.

    Looks for groups: hour, minute, ampm, month, day, year.
    If ampm is present, treats hour as 12-hour format.

    Args:
        groups: Dict with time/date components.
        event_timezone: Timezone of the event (e.g., "US/Eastern").
        output_timezone: Optional output timezone for display.
        program_duration: Duration in minutes (default 180).

    Returns:
        Dict with start_dt, end_dt, and formatted time/date strings.
    """
    now = datetime.now(pytz.timezone(event_timezone))

    # Parse hour and minute
    hour = int(groups.get("hour", now.hour))
    minute = int(groups.get("minute", 0))
    ampm = groups.get("ampm")

    # Convert 12-hour to 24-hour if ampm present
    if ampm:
        ampm_lower = ampm.lower().strip().rstrip(".")
        if ampm_lower in ("am", "a"):
            if hour == 12:
                hour = 0
        elif ampm_lower in ("pm", "p"):
            if hour != 12:
                hour += 12

    # Parse date components
    month_raw = groups.get("month")
    day_raw = groups.get("day")
    year_raw = groups.get("year")

    if month_raw is not None:
        month = _parse_month(str(month_raw))
        if month is None:
            month = now.month
    else:
        month = now.month

    if day_raw is not None:
        try:
            day = int(day_raw)
        except (ValueError, TypeError):
            day = now.day
    else:
        day = now.day

    if year_raw is not None:
        try:
            year = int(year_raw)
            # Handle 2-digit year
            if year < 100:
                year += 2000
        except (ValueError, TypeError):
            year = now.year
    else:
        year = now.year

    # Build datetime in event timezone
    tz = pytz.timezone(event_timezone)
    try:
        start_dt = tz.localize(datetime(year, month, day, hour, minute, 0))
    except (ValueError, OverflowError) as e:
        logger.warning("[DUMMY-EPG] Failed to build event datetime: %s", e)
        start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    end_dt = start_dt + timedelta(minutes=program_duration)

    # Convert to output timezone if specified
    if output_timezone:
        try:
            out_tz = pytz.timezone(output_timezone)
            start_dt = start_dt.astimezone(out_tz)
            end_dt = end_dt.astimezone(out_tz)
        except pytz.exceptions.UnknownTimeZoneError as e:
            logger.warning("[DUMMY-EPG] Unknown output_timezone %s: %s", output_timezone, e)

    # Format time strings
    starttime = start_dt.strftime("%-I %p").replace("AM", "AM").replace("PM", "PM")
    starttime24 = start_dt.strftime("%H:%M")
    endtime = end_dt.strftime("%-I %p").replace("AM", "AM").replace("PM", "PM")
    endtime24 = end_dt.strftime("%H:%M")

    # Format date strings
    date_str = start_dt.strftime("%B %-d")  # "October 17"
    month_name = MONTH_FULL_NAMES[start_dt.month]
    day_str = str(start_dt.day)
    year_str = str(start_dt.year)

    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "starttime": starttime,
        "starttime24": starttime24,
        "endtime": endtime,
        "endtime24": endtime24,
        "date": date_str,
        "month": month_name,
        "day": day_str,
        "year": year_str,
    }


def _xmltv_datetime(dt: datetime) -> str:
    """Format a datetime as XMLTV format in UTC."""
    utc_dt = dt.astimezone(pytz.utc)
    return utc_dt.strftime("%Y%m%d%H%M%S +0000")


def _make_programme(
    start_dt: datetime,
    end_dt: datetime,
    channel_id_str: str,
    title: str,
    description: str = "",
    categories: list[str] = None,
    poster_url: str = "",
    include_date_tag: bool = False,
    include_live_tag: bool = False,
    include_new_tag: bool = False,
) -> ET.Element:
    """Build a single <programme> element."""
    prog = ET.Element("programme")
    prog.set("start", _xmltv_datetime(start_dt))
    prog.set("stop", _xmltv_datetime(end_dt))
    prog.set("channel", channel_id_str)

    title_el = ET.SubElement(prog, "title")
    title_el.set("lang", "en")
    title_el.text = title

    if description:
        desc_el = ET.SubElement(prog, "desc")
        desc_el.set("lang", "en")
        desc_el.text = description

    if categories:
        for cat in categories:
            cat_el = ET.SubElement(prog, "category")
            cat_el.set("lang", "en")
            cat_el.text = cat.strip()

    if poster_url:
        icon_el = ET.SubElement(prog, "icon")
        icon_el.set("src", poster_url)

    if include_date_tag:
        date_el = ET.SubElement(prog, "date")
        date_el.text = start_dt.astimezone(pytz.utc).strftime("%Y-%m-%d")

    if include_live_tag:
        ET.SubElement(prog, "live")

    if include_new_tag:
        ET.SubElement(prog, "new")

    return prog


def extract_groups_from_variants(
    name: str,
    variants: list[dict],
) -> tuple[dict | None, dict | None]:
    """
    Try each variant's patterns in order, return first match.

    Args:
        name: The name to match against.
        variants: List of variant dicts with title_pattern, time_pattern, date_pattern.

    Returns:
        Tuple of (groups, matched_variant) or (None, None) if no variant matched.
    """
    for variant in variants:
        tp = variant.get("title_pattern")
        if not tp:
            continue
        groups = extract_groups(
            name,
            tp,
            variant.get("time_pattern"),
            variant.get("date_pattern"),
        )
        if groups is not None:
            return groups, variant
    return None, None


def _resolve_variant_template(variant: dict | None, profile: dict, field: str) -> str:
    """Get template from variant (if set), else fall back to profile-level."""
    if variant:
        val = variant.get(field)
        if val:
            return val
    return profile.get(field, "") or ""


def generate_channel_xml(
    channel_id: int,
    channel_name: str,
    channel_number: int | None,
    tvg_id: str,
    profile: dict,
    streams: list[dict] = None,
) -> tuple[ET.Element, list[ET.Element]]:
    """
    Generate XMLTV <channel> and <programme> elements for one channel.

    Args:
        channel_id: The channel database ID.
        channel_name: The channel display name.
        channel_number: Optional channel number.
        tvg_id: The tvg-id to use for this channel.
        profile: Profile dict (from DummyEPGProfile.to_dict()).
        streams: Optional list of stream dicts with "name" keys.

    Returns:
        Tuple of (channel_element, list_of_programme_elements).
    """
    channel_id_str = tvg_id
    streams = streams or []

    # Determine source name
    name_source = profile.get("name_source", "channel")
    stream_index = profile.get("stream_index", 1)

    if name_source == "stream" and streams:
        idx = stream_index - 1
        if 0 <= idx < len(streams):
            source_name = streams[idx].get("name", channel_name)
        else:
            source_name = channel_name
    else:
        source_name = channel_name

    # Apply substitutions
    sub_pairs = profile.get("substitution_pairs", [])
    substituted_name, _steps = apply_substitutions(source_name, sub_pairs)

    # Extract groups — variant-aware
    pattern_variants = profile.get("pattern_variants", [])
    matched_variant = None

    if pattern_variants:
        groups, matched_variant = extract_groups_from_variants(substituted_name, pattern_variants)
    else:
        title_pattern = profile.get("title_pattern")
        time_pattern = profile.get("time_pattern")
        date_pattern = profile.get("date_pattern")
        groups = extract_groups(substituted_name, title_pattern, time_pattern, date_pattern)

    # Profile config
    event_timezone = profile.get("event_timezone", "US/Eastern")
    output_timezone = profile.get("output_timezone")
    program_duration = profile.get("program_duration", 180)
    categories_str = profile.get("categories", "")
    categories = [c.strip() for c in categories_str.split(",") if c.strip()] if categories_str else []
    include_date_tag = profile.get("include_date_tag", False)
    include_live_tag = profile.get("include_live_tag", False)
    include_new_tag = profile.get("include_new_tag", False)

    # Base groups for template rendering (always available)
    base_groups = {
        "channel_name": channel_name,
        "channel_number": str(channel_number) if channel_number is not None else "",
        "channel_id": str(channel_id),
        "original_name": source_name,
        "substituted_name": substituted_name,
    }

    # Build <channel> element
    channel_el = ET.Element("channel")
    channel_el.set("id", channel_id_str)
    display_name_el = ET.SubElement(channel_el, "display-name")
    display_name_el.text = channel_name

    programmes = []

    tz = pytz.timezone(event_timezone)
    now = datetime.now(tz)
    today_midnight = tz.localize(
        datetime(now.year, now.month, now.day, 0, 0, 0)
    )
    tomorrow_midnight = today_midnight + timedelta(days=1)

    # Helper to resolve templates: matched variant overrides profile-level for core templates;
    # upcoming/ended/fallback inherit from profile unless variant overrides them
    def get_template(field: str) -> str:
        if matched_variant:
            val = matched_variant.get(field)
            if val:
                return val
        return profile.get(field, "") or ""

    if groups is not None:
        # Matched -- compute times and render templates
        time_vars = compute_event_times(
            groups, event_timezone, output_timezone, program_duration
        )
        all_groups = {**base_groups, **groups, **time_vars}

        # Remove datetime objects from template groups (not string-renderable)
        template_groups = {
            k: v for k, v in all_groups.items()
            if not isinstance(v, datetime)
        }

        title = render_template(get_template("title_template"), template_groups)
        description = render_template(get_template("description_template"), template_groups)

        # Channel logo — variant overrides profile
        logo_url = render_template(get_template("channel_logo_url_template"), template_groups)
        if logo_url:
            icon_el = ET.SubElement(channel_el, "icon")
            icon_el.set("src", logo_url)

        # Poster URL
        poster_url = render_template(get_template("program_poster_url_template"), template_groups)

        start_dt = time_vars["start_dt"]
        end_dt = time_vars["end_dt"]

        has_time = "hour" in groups

        if has_time:
            # Upcoming filler: midnight to event start
            if start_dt > today_midnight:
                upcoming_title = render_template(
                    get_template("upcoming_title_template"), template_groups
                )
                upcoming_desc = render_template(
                    get_template("upcoming_description_template"), template_groups
                )
                programmes.append(_make_programme(
                    today_midnight, start_dt, channel_id_str,
                    upcoming_title or title,
                    upcoming_desc or description,
                    categories, poster_url,
                    include_date_tag, False, False,
                ))

            # Main event
            programmes.append(_make_programme(
                start_dt, end_dt, channel_id_str,
                title, description,
                categories, poster_url,
                include_date_tag, include_live_tag, include_new_tag,
            ))

            # Ended filler: event end to next midnight
            if end_dt < tomorrow_midnight:
                ended_title = render_template(
                    get_template("ended_title_template"), template_groups
                )
                ended_desc = render_template(
                    get_template("ended_description_template"), template_groups
                )
                programmes.append(_make_programme(
                    end_dt, tomorrow_midnight, channel_id_str,
                    ended_title or title,
                    ended_desc or description,
                    categories, poster_url,
                    include_date_tag, False, False,
                ))
        else:
            # No time extracted -- single 24-hour programme
            programmes.append(_make_programme(
                today_midnight, tomorrow_midnight, channel_id_str,
                title, description,
                categories, poster_url,
                include_date_tag, include_live_tag, include_new_tag,
            ))
    else:
        # Fallback -- no pattern match
        template_groups = dict(base_groups)

        fallback_title = render_template(
            get_template("fallback_title_template"), template_groups
        )
        fallback_desc = render_template(
            get_template("fallback_description_template"), template_groups
        )

        # Channel logo (try with base groups)
        logo_url = render_template(
            get_template("channel_logo_url_template"), template_groups
        )
        if logo_url:
            icon_el = ET.SubElement(channel_el, "icon")
            icon_el.set("src", logo_url)

        poster_url = render_template(
            get_template("program_poster_url_template"), template_groups
        )

        programmes.append(_make_programme(
            today_midnight, tomorrow_midnight, channel_id_str,
            fallback_title or channel_name,
            fallback_desc,
            categories, poster_url,
            include_date_tag, False, False,
        ))

    return channel_el, programmes


def generate_xmltv(
    profiles: list[dict],
    channel_data: dict[int, dict],
) -> str:
    """
    Generate a complete XMLTV document from profiles and channel data.

    Args:
        profiles: List of profile dicts with channel_assignments included.
        channel_data: Maps channel_id -> {name, channel_number, streams}.

    Returns:
        XMLTV XML string.
    """
    tv = ET.Element("tv")
    tv.set("generator-info-name", "ECM Enhanced Channel Manager")
    tv.set("generator-info-url", "https://github.com/your/ecm")

    all_channels = []
    all_programmes = []

    for profile in profiles:
        if not profile.get("enabled", True):
            continue

        assignments = profile.get("channel_assignments", [])
        for assignment in assignments:
            ch_id = assignment.get("channel_id")
            if ch_id is None or ch_id not in channel_data:
                logger.debug(
                    "[DUMMY-EPG] Channel %s not found in channel_data, skipping",
                    ch_id,
                )
                continue

            ch_info = channel_data[ch_id]
            ch_name = ch_info.get("name", "Unknown")
            ch_number = ch_info.get("channel_number")
            streams = ch_info.get("streams", [])

            # Determine tvg_id
            tvg_id_override = assignment.get("tvg_id_override")
            if tvg_id_override:
                tvg_id = tvg_id_override
            else:
                tvg_id_template = profile.get("tvg_id_template", "ecm-{channel_number}")
                tvg_id_groups = {
                    "channel_id": str(ch_id),
                    "channel_number": str(ch_number) if ch_number is not None else str(ch_id),
                    "channel_name": ch_name,
                }
                tvg_id = render_template(tvg_id_template, tvg_id_groups)

            channel_el, programmes = generate_channel_xml(
                ch_id, ch_name, ch_number, tvg_id, profile, streams
            )
            all_channels.append(channel_el)
            all_programmes.extend(programmes)

    # Build document: channels first, then programmes
    for ch_el in all_channels:
        tv.append(ch_el)
    for prog_el in all_programmes:
        tv.append(prog_el)

    # Serialize to string
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    tree_str = ET.tostring(tv, encoding="unicode", xml_declaration=False)
    return xml_declaration + tree_str


def preview_pipeline(config: dict, sample_name: str) -> dict:
    """
    Run the full EPG pipeline on a sample name and return preview results.

    Supports multi-variant patterns: if config has pattern_variants, tries
    each variant in order. Otherwise uses flat pattern fields.

    Args:
        config: Profile configuration dict with all pattern/template fields.
        sample_name: Sample channel/stream name to process.

    Returns:
        Dict with original_name, substituted_name, substitution_steps,
        matched status, groups, time_variables, matched_variant, and rendered templates.
    """
    sub_pairs = config.get("substitution_pairs", [])
    substituted_name, substitution_steps = apply_substitutions(sample_name, sub_pairs)

    # Variant-aware matching
    pattern_variants = config.get("pattern_variants", [])
    matched_variant = None

    if pattern_variants:
        groups, matched_variant = extract_groups_from_variants(substituted_name, pattern_variants)
    else:
        title_pattern = config.get("title_pattern")
        time_pattern = config.get("time_pattern")
        date_pattern = config.get("date_pattern")
        groups = extract_groups(substituted_name, title_pattern, time_pattern, date_pattern)

    matched = groups is not None
    time_variables = None
    rendered = {
        "title": "",
        "description": "",
        "upcoming_title": "",
        "upcoming_description": "",
        "ended_title": "",
        "ended_description": "",
        "fallback_title": "",
        "fallback_description": "",
        "channel_logo_url": "",
        "program_poster_url": "",
    }

    base_groups = {
        "original_name": sample_name,
        "substituted_name": substituted_name,
    }

    # Helper to resolve template: variant overrides profile
    def get_template(field: str) -> str:
        if matched_variant:
            val = matched_variant.get(field)
            if val:
                return val
        return config.get(field, "") or ""

    if matched:
        event_timezone = config.get("event_timezone", "US/Eastern")
        output_timezone = config.get("output_timezone")
        program_duration = config.get("program_duration", 180)

        time_vars = compute_event_times(
            groups, event_timezone, output_timezone, program_duration
        )
        # Build time_variables without datetime objects
        time_variables = {
            k: v for k, v in time_vars.items()
            if not isinstance(v, datetime)
        }

        template_groups = {**base_groups, **groups, **time_vars}
        # Remove datetime objects for template rendering
        template_groups = {
            k: v for k, v in template_groups.items()
            if not isinstance(v, datetime)
        }

        rendered["title"] = render_template(get_template("title_template"), template_groups)
        rendered["description"] = render_template(get_template("description_template"), template_groups)
        rendered["upcoming_title"] = render_template(get_template("upcoming_title_template"), template_groups)
        rendered["upcoming_description"] = render_template(get_template("upcoming_description_template"), template_groups)
        rendered["ended_title"] = render_template(get_template("ended_title_template"), template_groups)
        rendered["ended_description"] = render_template(get_template("ended_description_template"), template_groups)
        rendered["fallback_title"] = render_template(get_template("fallback_title_template"), template_groups)
        rendered["fallback_description"] = render_template(get_template("fallback_description_template"), template_groups)
        rendered["channel_logo_url"] = render_template(get_template("channel_logo_url_template"), template_groups)
        rendered["program_poster_url"] = render_template(get_template("program_poster_url_template"), template_groups)
    else:
        # Fallback rendering with base groups only
        template_groups = dict(base_groups)
        rendered["fallback_title"] = render_template(get_template("fallback_title_template"), template_groups)
        rendered["fallback_description"] = render_template(get_template("fallback_description_template"), template_groups)
        rendered["channel_logo_url"] = render_template(get_template("channel_logo_url_template"), template_groups)
        rendered["program_poster_url"] = render_template(get_template("program_poster_url_template"), template_groups)

    return {
        "original_name": sample_name,
        "substituted_name": substituted_name,
        "substitution_steps": substitution_steps,
        "matched": matched,
        "matched_variant": matched_variant.get("name") if matched_variant else None,
        "groups": groups,
        "time_variables": time_variables,
        "rendered": rendered,
    }


def preview_pipeline_batch(config: dict, sample_names: list[str]) -> list[dict]:
    """
    Run the preview pipeline on multiple sample names.

    Args:
        config: Profile configuration dict.
        sample_names: List of sample names to test.

    Returns:
        List of preview result dicts, one per sample name.
    """
    return [preview_pipeline(config, name) for name in sample_names]
