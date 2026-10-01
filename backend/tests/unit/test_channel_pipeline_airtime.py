"""Event-start ordering for pipeline-owned channels."""

from datetime import datetime, timezone

import pytest

from channel_pipeline_airtime import airtime_sort_key, event_start_time


@pytest.mark.parametrize(('name', 'expected'), [
    ('ESPN+ 56: Soccer @ Sep 30 2026 10:00PM ET', '2026-10-01T02:00:00+00:00'),
    ('CA (TSN+ 010): Tokyo (2026-09-30 22:00:10) [1080p]', '2026-10-01T02:00:10+00:00'),
    ('US (ESPN+ 084): Soccer Sep 30 10:00PM ET (2026-09-30 22:00:45) [1080p]',
     '2026-10-01T02:00:45+00:00'),
    ('Example (2026-09-30 22:00:00-04:00)', '2026-10-01T02:00:00+00:00'),
    ('Example (2026-09-30 22:00:00Z)', '2026-09-30T22:00:00+00:00'),
    ('Example (2026-09-30 00:00:00)', '2026-09-30T04:00:00+00:00'),
    ('Example (2026-09-30 12:00:00)', '2026-09-30T16:00:00+00:00'),
])
def test_event_start_time(name, expected):
    assert event_start_time(name) == datetime.fromisoformat(expected)


def test_yearless_day_first_provider_time_uses_event_sync_year_inference():
    from services.event_sync_matcher import parse_event_name

    name = 'TSN+ 11: Tokyo @ 30 Sep 10:00 PM ET'
    assert event_start_time(name) == parse_event_name(name).start.astimezone(timezone.utc)


@pytest.mark.parametrize('name', [
    'TSN+ 11: NO EVENT',
    'ESPN+ 56: Show @ 30 Feb 10:00 PM ET',
    'Show (2026-02-30 22:00:00)',
    'Show (2026-11-01 01:30:00)',  # ambiguous fall-back DST hour
    'Show (2026-03-08 02:30:00)',  # nonexistent spring-forward hour
])
def test_invalid_and_undated_starts_remain_unknown(name):
    assert event_start_time(name) is None


def test_missing_last_in_both_directions_and_stable_tie_breaker():
    earlier = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    later = datetime(2026, 9, 30, 20, tzinfo=timezone.utc)
    items = [(None, 3), (later, 8), (earlier, 7), (earlier, 2)]
    assert [cid for start, cid in sorted(items, key=lambda item: airtime_sort_key(*item))] == [2, 7, 8, 3]
    assert [cid for start, cid in sorted(items, key=lambda item: airtime_sort_key(*item, descending=True))] == [8, 2, 7, 3]
