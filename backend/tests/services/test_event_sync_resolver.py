"""
Event sync resolver — shared preview/attach resolution layer
(bead enhancedchannelmanager-ti939.1.4).

``services.event_sync_resolver.resolve_event_sync`` is the ONE function the
Phase 1A preview endpoint calls and the Phase 1B attach path will call —
dry-run parity by construction. These tests pin:

* disposition classification (would_attach / ambiguous / unmatched /
  parse_failed) driven by the matcher's bands;
* determinism — identical inputs produce identical output, in a stable
  order, across repeated calls;
* per-group pattern routing (config.patterns / config.group_patterns,
  including a master-group override parsed with ITS patterns, not the
  secondary group's);
* unparsed-master surfacing (the master-as-ceiling diagnostic).

Pure module — no Dispatcharr client, no DB, no network.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from services.event_sync_matcher import (
    DEFAULT_EVENT_TIMEZONE,
    REJECT_NO_PARSED_TIME,
    REJECT_PARSE_FAILURE,
)
from services.event_sync_resolver import (
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_PARSE_FAILED,
    DISPOSITION_UNMATCHED,
    DISPOSITION_WOULD_ATTACH,
    SecondaryStream,
    effective_patterns,
    resolve_event_sync,
)

# Deterministic "now" anchoring year inference (same convention as the
# frozen-corpus gate).
NOW = pytz.timezone(DEFAULT_EVENT_TIMEZONE).localize(
    datetime(2026, 7, 11, 12, 0, 0)
)


def _config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20, 30],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "enabled": True,
    }
    config.update(overrides)
    return config


MASTERS = [
    "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
    "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
    "Peacock 40: NO EVENT",  # unparsable master (no date/time)
]

# Corpus-proven ambiguous pairing for MASTERS[1]: different IMSA sessions at
# the same venue and slot (see matcher_corpus.jsonl).
AMBIGUOUS_STREAM_NAME = "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET"


class TestDispositions:
    def test_happy_path_classifies_all_four_dispositions(self):
        streams = [
            # Attach: same fixture, different provider spelling.
            SecondaryStream(
                name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                group_id=20, stream_id=201, provider="FuboProvider",
            ),
            # Ambiguous: related non-team titles, no team signal.
            SecondaryStream(
                name=AMBIGUOUS_STREAM_NAME,
                group_id=20, stream_id=202, provider="FuboProvider",
            ),
            # Unmatched: parses fine, no master within the window.
            SecondaryStream(
                name="DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
                group_id=30, stream_id=301, provider="DaznProvider",
            ),
            # Parse failure: idle-slot placeholder, no date/time.
            SecondaryStream(
                name="Fubo Sports Network 07 : NO EVENT",
                group_id=30, stream_id=302, provider="DaznProvider",
            ),
        ]
        resolution = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        by_id = {r.stream.stream_id: r for r in resolution.resolved}

        assert by_id[201].disposition == DISPOSITION_WOULD_ATTACH
        assert by_id[201].best is not None
        assert by_id[201].best.master_name == MASTERS[0]

        assert by_id[202].disposition == DISPOSITION_AMBIGUOUS
        assert by_id[202].best is None

        assert by_id[301].disposition == DISPOSITION_UNMATCHED
        assert by_id[301].result.candidates == ()

        assert by_id[302].disposition == DISPOSITION_PARSE_FAILED
        assert by_id[302].result.unmatchable_reason == REJECT_NO_PARSED_TIME

    def test_title_parse_failure_is_parse_failed(self):
        # A pattern set whose title_pattern never matches -> REJECT_PARSE_FAILURE.
        config = _config(patterns=[{
            "name": "never-matches",
            "title_pattern": r"^ZZZ-(?P<title>.+)-ZZZ$",
        }])
        streams = [SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(config, MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_PARSE_FAILED
        assert resolved.result.unmatchable_reason == REJECT_PARSE_FAILURE

    def test_reject_band_top_candidate_is_unmatched(self):
        # Same kickoff, hard team-token conflict -> candidate exists but is
        # reject-banded; the stream is UNMATCHED, never attached.
        streams = [SecondaryStream(
            name="WNBA TV 02: Sparks vs. Liberty @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=203,
        )]
        resolution = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.best is None
        assert resolved.result.candidates  # in-window candidate was scored


class TestDeterminism:
    def test_identical_inputs_produce_identical_output(self):
        streams = [
            SecondaryStream(
                name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                group_id=30, stream_id=301,
            ),
            SecondaryStream(
                name="FloSports 01 : Tour de France Stage 8 @ 11 Jul 06:30 AM ET",
                group_id=20, stream_id=201,
            ),
        ]
        first = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        second = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        assert first == second

    def test_output_order_is_group_then_name_not_input_order(self):
        # Streams supplied in reverse group order come back ordered by
        # (group_id, name, stream_id) — stable regardless of fetch order.
        streams = [
            SecondaryStream(name="B stream: NO EVENT", group_id=30, stream_id=2),
            SecondaryStream(name="A stream: NO EVENT", group_id=30, stream_id=1),
            SecondaryStream(name="Z stream: NO EVENT", group_id=20, stream_id=3),
        ]
        resolution = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        ordered = [(r.stream.group_id, r.stream.name) for r in resolution.resolved]
        assert ordered == [
            (20, "Z stream: NO EVENT"),
            (30, "A stream: NO EVENT"),
            (30, "B stream: NO EVENT"),
        ]


class TestPatternRouting:
    def test_effective_patterns_prefers_group_override(self):
        shared = [{"name": "shared", "title_pattern": r"(?P<title>.+)"}]
        override = [{"name": "grp-20", "title_pattern": r"(?P<title>.+)"}]
        # group_patterns keys arrive as strings after a JSON round-trip.
        config = _config(patterns=shared, group_patterns={"20": override})
        assert effective_patterns(config, 20) == override
        assert effective_patterns(config, 30) == shared
        assert effective_patterns(_config(), 30) is None  # built-in defaults

    def test_master_group_patterns_parse_masters_not_secondary_patterns(self):
        # Master names in a shape ONLY the master override parses; secondary
        # names in the default shape. If masters were parsed with the
        # secondary group's patterns they would all fail -> zero candidates.
        config = _config(group_patterns={
            "10": [{
                "name": "master-shape",
                "title_pattern": r"^MASTER\s*\|\s*(?P<title>.+?)\s*\|.*$",
                "time_pattern": r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AP])M\s*$",
                "date_pattern": r"\|\s*(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})\s+\d{1,2}:\d{2}",
            }],
        })
        masters = ["MASTER | Mercury vs. Aces | 11 Jul 06:00 PM"]
        streams = [SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(config, masters, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == masters[0]
        assert resolution.unparsed_master_names == ()


class TestUnparsedMasters:
    def test_unparsable_masters_are_surfaced_loudly(self):
        resolution = resolve_event_sync(_config(), MASTERS, [], now=NOW)
        assert resolution.unparsed_master_names == ("Peacock 40: NO EVENT",)

    def test_all_masters_unparsed_still_resolves_streams(self):
        streams = [SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(
            _config(), ["Peacock 40: NO EVENT"], streams, now=NOW
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolution.unparsed_master_names == ("Peacock 40: NO EVENT",)


class TestThresholdClamp:
    def test_sub_floor_threshold_is_clamped_by_the_matcher(self):
        # A (hypothetically stored) sub-floor threshold must behave as 0.80 —
        # the matcher's is_event_attachable clamps; the resolver passes the
        # value through rather than re-implementing policy.
        config = _config(attach_threshold=0.10)
        streams = [SecondaryStream(
            # Same slot as the IMSA master but a different session: scores in
            # the ambiguous band, must NOT attach at 0.10.
            name=AMBIGUOUS_STREAM_NAME,
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(config, MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS
