"""Airtime ordering for event-channel pipeline rules.

Reuse Event Sync's date/time parser so the two pipeline paths agree on ET,
12-hour times, year inference and DST. Provider feeds that only carry an ISO
timestamp in parentheses use that as an ET wall-clock time instead.
"""

import re
from datetime import datetime, timezone

import pytz

from services.event_sync_matcher import DEFAULT_EVENT_TIMEZONE, parse_event_name


_PROVIDER_TIMESTAMP = re.compile(
    r"\((?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<time>\d{2}:\d{2}(?::\d{2})?)"
    r"(?P<offset>Z|[+-]\d{2}:?\d{2})?\)"
)


def event_start_time(stream_name: str) -> datetime | None:
    """Return an aware UTC start, or None when the schedule cannot be parsed.

    Prefer Event Sync's dated provider format to an ISO metadata suffix: the
    former is the advertised event time, while the latter may include seconds
    reflecting a provider's stream-start delay. Never infer a date from a
    bare time, and never guess through an ambiguous DST wall-clock hour.
    """
    parsed = parse_event_name(stream_name)
    if parsed.start is not None:
        return parsed.start.astimezone(timezone.utc)

    match = _PROVIDER_TIMESTAMP.search(stream_name)
    if match is None:
        return None
    try:
        stamp = match.group('date') + 'T' + match.group('time') + (match.group('offset') or '')
        local = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if local.tzinfo is None:
            local = pytz.timezone(DEFAULT_EVENT_TIMEZONE).localize(local, is_dst=None)
        return local.astimezone(timezone.utc)
    except (ValueError, pytz.AmbiguousTimeError, pytz.NonExistentTimeError):
        return None


def airtime_sort_key(start: datetime | None, channel_id: int, *, descending: bool = False) -> tuple:
    """Put unparseable events last in either direction; tie-break by stable ID."""
    stamp = start.timestamp() if start is not None else 0
    return (start is None, -stamp if descending else stamp, channel_id)
