"""
channel_pipeline_sort — shared alphabetical channel-sort semantics.

Backend port of the manual Sort & Renumber modal's comparator
(``frontend/src/utils/channelSort.ts``, consumed by
``frontend/src/components/ChannelsPane.tsx``). Kept in lock-step with that
file so the ``sort_group`` pipeline action (see
``channel_pipeline_engine.py``, Pass 3.6 / ``_sort_channel_groups``) and the
manual Sort & Renumber modal always agree on ordering. If either side's
regexes or natural-sort algorithm changes, update the other and re-run:

  - frontend: ``npx vitest run src/utils/channelSort.test.ts``
  - backend:  ``python -m pytest tests/unit/test_channel_pipeline_sort.py``

enhancedchannelmanager-vy4fl (sort_group action) /
enhancedchannelmanager-hf8t9 (manual modal descending toggle).
"""
import re

# Ported 1:1 from frontend/src/utils/channelSort.ts::getNameForSorting.
# Strips embedded channel numbers from a name for sorting purposes:
# "US | 5034 - Name" -> "US - Name", "123 | Name" -> "Name", "Name | 123" -> "Name".
_MID_NUMBER_RE = re.compile(r"^([A-Za-z].+?\s*\|\s*)\d+(?:\.\d+)?\s*([-:]\s*.+)$")
_PREFIX_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[|\-.\s]\s*(.+)$")
_SUFFIX_NUMBER_RE = re.compile(r"^(.+)\s*[|\-.]\s*(\d+(?:\.\d+)?)$")

# Ported 1:1 from frontend/src/utils/channelSort.ts::stripCountryPrefix.
_COUNTRY_PREFIX_RE = re.compile(r"^[A-Z]{2,3}\s*[|:-]\s*(.+)$")
_COUNTRY_PREFIX_NOSEP_RE = re.compile(r"^[A-Z]{2,3}\s+([A-Z].+)$")

# Splits a string into alternating text/digit-run tokens for natural sort,
# e.g. "Channel 2" -> ["Channel ", "2", ""]. Mirrors the frontend's
# `naturalCompare` (a.localeCompare(b, undefined, {numeric: true})) for the
# common case this feature targets (ASCII channel names with embedded
# numbers); it does not attempt to replicate ICU collation for non-ASCII
# text. This split pattern is intentionally independent from
# channel_pipeline_engine._natural_sort_key (private to that module's
# stream_name_natural sort mode) to avoid a reverse import dependency.
_DIGIT_SPLIT_RE = re.compile(r"(\d+)")


def get_name_for_sorting(channel_name: str) -> str:
    """Strip an embedded/leading/trailing channel number from a name.

    Backend port of ``getNameForSorting`` in
    ``frontend/src/utils/channelSort.ts``. Used when the "ignore channel
    numbers in names when sorting" option is enabled.
    """
    mid_match = _MID_NUMBER_RE.match(channel_name)
    if mid_match:
        return (mid_match.group(1) + mid_match.group(2)).strip()

    prefix_match = _PREFIX_NUMBER_RE.match(channel_name)
    if prefix_match:
        return prefix_match.group(2).strip()

    suffix_match = _SUFFIX_NUMBER_RE.match(channel_name)
    if suffix_match:
        return suffix_match.group(1).strip()

    return channel_name


def strip_country_prefix(channel_name: str) -> str:
    """Strip a leading country-code prefix (e.g. "US | ", "UK: ") from a name.

    Backend port of ``stripCountryPrefix`` in
    ``frontend/src/utils/channelSort.ts``. Used when the "ignore country
    prefix when sorting" option is enabled.
    """
    match = _COUNTRY_PREFIX_RE.match(channel_name)
    if match:
        return match.group(1).strip()

    nosep_match = _COUNTRY_PREFIX_NOSEP_RE.match(channel_name)
    if nosep_match:
        return nosep_match.group(1).strip()

    return channel_name


def _natural_sort_key(s: str) -> list:
    """Split a (lowercased) string into text/int tokens for natural sort.

    "channel 2" -> ["channel ", 2, ""] sorts before
    "channel 10" -> ["channel ", 10, ""] — numeric runs compare as
    integers rather than lexicographically.
    """
    return [
        int(part) if part.isdigit() else part
        for part in _DIGIT_SPLIT_RE.split(s)
    ]


def channel_sort_key(
    name: str,
    *,
    strip_numbers: bool = True,
    ignore_country: bool = False,
) -> list:
    """Compute the natural-sort key for a channel name.

    Applies the same optional transforms as the manual Sort & Renumber
    modal, in the same order: strip embedded numbers, then strip country
    prefix, then case-insensitive natural sort.
    """
    normalized = name
    if strip_numbers:
        normalized = get_name_for_sorting(normalized)
    if ignore_country:
        normalized = strip_country_prefix(normalized)
    return _natural_sort_key(normalized.lower())


def sort_channels_by_name(
    channels: list,
    *,
    name_key=lambda ch: ch.get("name", ""),
    strip_numbers: bool = True,
    ignore_country: bool = False,
    order: str = "asc",
) -> list:
    """Sort a list of channel dicts (or objects) by name.

    ``name_key`` extracts the display name from each item (default:
    ``dict["name"]``). ``order`` is ``"asc"`` or ``"desc"``.

    Uses Python's ``sorted(..., reverse=True)`` rather than sorting
    ascending and reversing the list — ``reverse=True`` preserves the
    original relative order of equal-key items (stable), matching the
    frontend's approach of negating the comparator result rather than
    reversing the sorted array.
    """
    return sorted(
        channels,
        key=lambda ch: channel_sort_key(
            name_key(ch), strip_numbers=strip_numbers, ignore_country=ignore_country
        ),
        reverse=(order == "desc"),
    )
