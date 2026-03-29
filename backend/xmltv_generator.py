"""
XMLTV EPG generator for export profiles.
Generates valid XMLTV format filtered to profile channels.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def generate_xmltv(
    channels: list[dict],
    epg_data: list[dict],
    profile: dict,
) -> str:
    """Generate an XMLTV document from channels and EPG data.

    Args:
        channels: List of channel dicts with keys: id, name, tvg_id, logo_url,
                  channel_number.
        epg_data: List of EPG programme dicts with keys: channel_id (tvg_id reference),
                  start, stop, title, description, category, icon.
        profile: Profile dict (used to filter which channels are included).

    Returns:
        XMLTV XML document as a string.
    """
    root = ET.Element("tv")
    root.set("generator-info-name", "ECM Export")
    root.set("generator-info-url", "")

    # Build set of tvg_ids for filtering programmes
    channel_tvg_ids = set()

    # Add <channel> elements
    for ch in channels:
        tvg_id = ch.get("tvg_id") or ""
        if not tvg_id:
            continue

        channel_tvg_ids.add(tvg_id)
        ch_elem = ET.SubElement(root, "channel")
        ch_elem.set("id", tvg_id)

        display_name = ET.SubElement(ch_elem, "display-name")
        display_name.text = ch.get("name") or "Unknown"

        logo_url = ch.get("logo_url") or ""
        if logo_url and profile.get("include_logos", True):
            icon = ET.SubElement(ch_elem, "icon")
            icon.set("src", logo_url)

    # Add <programme> elements filtered to profile channels
    programmes_added = 0
    for prog in epg_data:
        prog_channel = prog.get("channel_id") or prog.get("channel") or ""
        if prog_channel not in channel_tvg_ids:
            continue

        start = _format_xmltv_time(prog.get("start"))
        stop = _format_xmltv_time(prog.get("stop"))
        if not start or not stop:
            continue

        prog_elem = ET.SubElement(root, "programme")
        prog_elem.set("start", start)
        prog_elem.set("stop", stop)
        prog_elem.set("channel", prog_channel)

        title_text = prog.get("title") or ""
        if title_text:
            title_elem = ET.SubElement(prog_elem, "title")
            title_elem.set("lang", "en")
            title_elem.text = title_text

        desc_text = prog.get("description") or prog.get("desc") or ""
        if desc_text:
            desc_elem = ET.SubElement(prog_elem, "desc")
            desc_elem.set("lang", "en")
            desc_elem.text = desc_text

        category_text = prog.get("category") or ""
        if category_text:
            cat_elem = ET.SubElement(prog_elem, "category")
            cat_elem.set("lang", "en")
            cat_elem.text = category_text

        icon_url = prog.get("icon") or ""
        if icon_url:
            icon_elem = ET.SubElement(prog_elem, "icon")
            icon_elem.set("src", icon_url)

        programmes_added += 1

    logger.info(
        "[XMLTV-GEN] Generated XMLTV with %s channels, %s programmes",
        len(channel_tvg_ids), programmes_added,
    )

    # Produce XML string with declaration
    ET.indent(root, space="  ")
    xml_bytes = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n' + xml_bytes + "\n"


def _format_xmltv_time(value: Optional[str]) -> Optional[str]:
    """Convert a datetime string to XMLTV time format (YYYYMMDDHHMMSS +0000).

    Accepts ISO 8601 strings. Returns None if parsing fails.
    """
    if not value:
        return None

    # Already in XMLTV format
    if len(value) >= 14 and value[:14].isdigit():
        return value

    try:
        # Handle ISO 8601 with timezone
        clean = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        # Format as XMLTV time
        offset = dt.strftime("%z") or "+0000"
        # Ensure offset has no colon (XMLTV uses +0000 not +00:00)
        offset = offset.replace(":", "")
        return dt.strftime("%Y%m%d%H%M%S") + " " + offset
    except (ValueError, TypeError):
        logger.debug("[XMLTV-GEN] Could not parse time value: %s", value)
        return None
