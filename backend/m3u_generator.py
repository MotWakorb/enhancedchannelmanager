"""
M3U playlist generator for export profiles.
Generates EXTM3U format with tvg attributes from managed channels.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def generate_m3u(
    channels: list[dict],
    profile: dict,
    stream_lookup: Optional[dict] = None,
) -> str:
    """Generate an M3U playlist string from channels and profile settings.

    Args:
        channels: List of channel dicts with keys: id, name, channel_number,
                  channel_group_name, logo_url, tvg_id, streams (list of stream IDs).
        profile: Profile dict with keys: include_logos, include_epg_ids,
                 include_channel_numbers, stream_url_mode.
        stream_lookup: Dict mapping stream_id -> stream dict with "url" key.
                       If None, channels must have a "stream_url" key directly.

    Returns:
        M3U playlist as a string.
    """
    lines = ["#EXTM3U"]

    for ch in channels:
        url = _get_stream_url(ch, stream_lookup, profile.get("stream_url_mode", "direct"))
        if not url:
            logger.debug("[M3U-GEN] Skipping channel %s — no stream URL", ch.get("name"))
            continue

        attrs = _build_extinf_attrs(ch, profile)
        display_name = _escape_m3u(ch.get("name", "Unknown"))
        lines.append(f"#EXTINF:-1 {attrs},{display_name}")
        lines.append(url)

    logger.info("[M3U-GEN] Generated M3U with %s channels", (len(lines) - 1) // 2)
    return "\n".join(lines) + "\n"


def _build_extinf_attrs(channel: dict, profile: dict) -> str:
    """Build the EXTINF attribute string for a channel."""
    parts = []

    if profile.get("include_epg_ids", True):
        tvg_id = channel.get("tvg_id") or ""
        parts.append(f'tvg-id="{_escape_attr(tvg_id)}"')

    tvg_name = channel.get("name") or "Unknown"
    parts.append(f'tvg-name="{_escape_attr(tvg_name)}"')

    if profile.get("include_logos", True):
        logo = channel.get("logo_url") or ""
        if logo:
            parts.append(f'tvg-logo="{_escape_attr(logo)}"')

    if profile.get("include_channel_numbers", True):
        chno = channel.get("channel_number")
        if chno is not None:
            # Format as integer if it's a whole number
            chno_str = str(int(chno)) if isinstance(chno, float) and chno == int(chno) else str(chno)
            parts.append(f'tvg-chno="{chno_str}"')

    group = channel.get("channel_group_name") or ""
    if group:
        parts.append(f'group-title="{_escape_attr(group)}"')

    return " ".join(parts)


def _get_stream_url(
    channel: dict,
    stream_lookup: Optional[dict],
    url_mode: str,
) -> Optional[str]:
    """Get the stream URL for a channel.

    Uses the first stream assigned to the channel.
    """
    # Direct URL on the channel dict (test/simple mode)
    if "stream_url" in channel and channel["stream_url"]:
        return channel["stream_url"]

    if not stream_lookup:
        return None

    stream_ids = channel.get("streams", [])
    if not stream_ids:
        return None

    # Use the first (highest priority) stream
    first_id = stream_ids[0]
    stream = stream_lookup.get(first_id)
    if not stream:
        return None

    return stream.get("url")


def _escape_m3u(text: str) -> str:
    """Escape text for M3U display name (after the comma)."""
    # M3U display names shouldn't contain newlines
    return text.replace("\n", " ").replace("\r", "")


def _escape_attr(text: str) -> str:
    """Escape text for use inside EXTINF attribute quotes."""
    return text.replace('"', "'").replace("\n", " ").replace("\r", "")
