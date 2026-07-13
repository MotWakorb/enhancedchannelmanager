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
from unittest.mock import AsyncMock

import pytest
import pytz

from services.event_sync_matcher import (
    BAND_AMBIGUOUS,
    BAND_ATTACH,
    BAND_REJECT,
    DEFAULT_EVENT_TIMEZONE,
    TEAM_VERDICT_ABSENT,
    TEAM_VERDICT_AGREE,
    MasterCandidate,
    ParsedEvent,
    REJECT_NO_PARSED_TIME,
    REJECT_PARSE_FAILURE,
    StreamMatchResult,
)
from services.event_sync_resolver import (
    AMBIGUOUS_REASON_BAND,
    AMBIGUOUS_REASON_CONTESTED,
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_PARSE_FAILED,
    DISPOSITION_UNMATCHED,
    DISPOSITION_WOULD_ATTACH,
    MATCHED_VIA_ASSUME_CURRENT_DATE,
    MATCHED_VIA_LOWERED_THRESHOLD,
    MATCHED_VIA_MASTER_FROM_STREAM,
    MATCHED_VIA_TIME_WINDOW_IGNORED,
    ResolvedStream,
    SecondaryStream,
    _classify,
    _matched_via,
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


class TestThresholdFloor:
    def test_default_threshold_leaves_ambiguous_pair_ambiguous(self):
        # Baseline: at the default 0.80 floor the different-IMSA-session pair
        # scores in the ambiguous band and is NOT attached.
        streams = [SecondaryStream(
            name=AMBIGUOUS_STREAM_NAME, group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS

    def test_operator_lowered_threshold_attaches_the_ambiguous_pair(self):
        # bead krkm4-sibling: 0.80 is a default, not a hard clamp. An operator
        # who lowers attach_threshold gets exactly that lower bar — the same
        # ambiguous-band pair now auto-attaches. The resolver passes the value
        # straight through; is_event_attachable honors it.
        config = _config(attach_threshold=0.50)
        streams = [SecondaryStream(
            name=AMBIGUOUS_STREAM_NAME, group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(config, MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best is not None


class TestContestedRail:
    """PR #613 review rail (bead ti939.2.1): a stream whose top candidates
    cannot be confidently separated is AMBIGUOUS — skip + count, never an
    attach to an alphabetical tie-break winner."""

    def test_same_fixture_different_session_tie_is_contested(self):
        # The exact review scenario: the team-agree boost ties the main card
        # and the prelims at identical scores against either stream. Without
        # the rail the alphabetical tie-break would attach to "PPV 01" — the
        # wrong master half the time.
        masters = [
            "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET",
        ]
        streams = [SecondaryStream(
            name="BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            group_id=20, stream_id=201, provider="BoxProvider",
        )]
        resolution = resolve_event_sync(_config(), masters, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS
        assert resolved.ambiguous_reason == AMBIGUOUS_REASON_CONTESTED
        assert resolved.best is None
        # Both candidates really did land in the attach band (the trap).
        assert len(resolved.result.candidates) >= 2
        assert resolved.result.candidates[0].band == BAND_ATTACH
        assert resolved.result.candidates[1].band == BAND_ATTACH

    def test_single_attach_candidate_still_attaches(self):
        # Removing the second session removes the contest — same stream, one
        # master, clean attach. Proves the rail keys on the candidate SET,
        # not on the stream.
        masters = ["PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET"]
        streams = [SecondaryStream(
            name="BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(_config(), masters, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.ambiguous_reason is None
        assert resolved.best.master_name == masters[0]

    def test_distant_runner_up_does_not_contest(self):
        # A runner-up far below the winner (different fixture, reject band,
        # outside CONTESTED_SCORE_EPSILON) must not block the attach.
        masters = [
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            "Peacock 15: Sparks vs. Liberty @ 11 Jul 06:00 PM ET",
        ]
        streams = [SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )]
        resolution = resolve_event_sync(_config(), masters, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == masters[0]

    def test_band_ambiguous_top_candidate_carries_band_reason(self):
        streams = [SecondaryStream(
            name=AMBIGUOUS_STREAM_NAME, group_id=20, stream_id=202,
        )]
        resolution = resolve_event_sync(_config(), MASTERS, streams, now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS
        assert resolved.ambiguous_reason == AMBIGUOUS_REASON_BAND

    def test_epsilon_near_tie_is_contested_unit(self):
        # Unit-level epsilon proof against _classify with synthetic
        # candidates: winner in the attach band, runner-up BELOW the band
        # but within CONTESTED_SCORE_EPSILON — still contested.
        top = _candidate("PPV 01: A vs. B", score=0.82, band=BAND_ATTACH)
        near = _candidate("PPV 02: A vs. C", score=0.79, band=BAND_AMBIGUOUS)
        result = StreamMatchResult(
            stream_name="s", parsed=top.parsed, candidates=(top, near),
        )
        disposition, best, reason = _classify(result)
        assert disposition == DISPOSITION_AMBIGUOUS
        assert best is None
        assert reason == AMBIGUOUS_REASON_CONTESTED

    def test_outside_epsilon_runner_up_is_not_contested_unit(self):
        top = _candidate("PPV 01: A vs. B", score=0.90, band=BAND_ATTACH)
        far = _candidate("PPV 02: A vs. C", score=0.70, band=BAND_AMBIGUOUS)
        result = StreamMatchResult(
            stream_name="s", parsed=top.parsed, candidates=(top, far),
        )
        disposition, best, reason = _classify(result)
        assert disposition == DISPOSITION_WOULD_ATTACH
        assert best is top
        assert reason is None

    def test_second_attach_band_candidate_contests_even_outside_epsilon(self):
        # Two attach-band candidates always contest, no matter the gap.
        top = _candidate("PPV 01: A vs. B", score=1.0, band=BAND_ATTACH)
        second = _candidate("PPV 02: A vs. C", score=0.81, band=BAND_ATTACH)
        result = StreamMatchResult(
            stream_name="s", parsed=top.parsed, candidates=(top, second),
        )
        disposition, best, reason = _classify(result)
        assert disposition == DISPOSITION_AMBIGUOUS
        assert reason == AMBIGUOUS_REASON_CONTESTED

    def test_attach_band_runner_hidden_below_higher_scored_reject_contests(self):
        # Band is NOT monotonic in score: a no-teams-rail REJECT can outscore
        # a lower attach-band candidate. The attach-band scan must cover ALL
        # runners, not stop at the first non-contesting one.
        top = _candidate("PPV 01: A vs. B", score=0.95, band=BAND_ATTACH)
        reject = _candidate("PPV 02: Some Show", score=0.88, band=BAND_REJECT)
        hidden = _candidate("PPV 03: A vs. C", score=0.85, band=BAND_ATTACH)
        result = StreamMatchResult(
            stream_name="s", parsed=top.parsed,
            candidates=(top, reject, hidden),
        )
        disposition, best, reason = _classify(result)
        assert disposition == DISPOSITION_AMBIGUOUS
        assert reason == AMBIGUOUS_REASON_CONTESTED


class TestMasterGroupSelfAttach:
    """bead 6xxmp: the master group as its own secondary stream source.

    When ``include_master_group_streams`` is on, the fetch layer appends the
    master group's streams with ``group_id == master_group_id``. The resolver
    drops those already attached to a master channel (passed via
    ``attached_stream_ids``) so preview/run parity holds and the auto-synced
    provider's own streams are not re-offered.
    """

    # A master-group stream that matches MASTERS[0] (a second provider's copy).
    MASTER_GROUP_STREAM = "Fubo 09 : Mercury vs. Aces @ 11 Jul 06:00 PM ET"

    def test_already_attached_master_group_stream_is_dropped(self):
        streams = [SecondaryStream(
            name=self.MASTER_GROUP_STREAM, group_id=10, stream_id=999,
        )]
        resolution = resolve_event_sync(
            _config(include_master_group_streams=True), MASTERS, streams,
            now=NOW, attached_stream_ids={999},
        )
        # Filtered before scoring -> not in the resolution at all.
        assert resolution.resolved == ()

    def test_unattached_master_group_stream_still_attaches(self):
        streams = [SecondaryStream(
            name=self.MASTER_GROUP_STREAM, group_id=10, stream_id=1000,
        )]
        resolution = resolve_event_sync(
            _config(include_master_group_streams=True), MASTERS, streams,
            now=NOW, attached_stream_ids={999},  # different id -> not filtered
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == MASTERS[0]

    def test_filter_is_scoped_to_the_master_group_only(self):
        # A normal secondary-group stream (group 20) whose id happens to be in
        # attached_stream_ids must NOT be dropped — the filter is master-group
        # scoped so every other group behaves exactly as before.
        streams = [SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=555,
        )]
        resolution = resolve_event_sync(
            _config(), MASTERS, streams, now=NOW, attached_stream_ids={555},
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH

    def test_no_attached_ids_filters_nothing(self):
        streams = [SecondaryStream(
            name=self.MASTER_GROUP_STREAM, group_id=10, stream_id=999,
        )]
        resolution = resolve_event_sync(
            _config(include_master_group_streams=True), MASTERS, streams,
            now=NOW,  # attached_stream_ids defaults to None
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH


class TestAssumeCurrentDate:
    """bead assume-current-date: the flag threads through to BOTH sides.

    A dateless master and a dateless secondary both land on "today", so their
    times compare on one day and a same-time same-event pair attaches.
    """

    DATELESS_MASTERS = ["Boxing 01 : Fury vs. Usyk 6PM"]

    def test_dateless_pair_attaches_only_with_the_flag(self):
        streams = [SecondaryStream(
            name="PPV 07 : Tyson Fury vs. Oleksandr Usyk 6PM",
            group_id=20, stream_id=701,
        )]
        # OFF: both sides are dateless -> unmatchable, no candidate.
        off = resolve_event_sync(
            _config(), self.DATELESS_MASTERS, streams, now=NOW,
        )
        assert off.resolved[0].disposition != DISPOSITION_WOULD_ATTACH

        # ON: both land on today at 18:00 -> in-window, attaches.
        on = resolve_event_sync(
            _config(assume_current_date=True), self.DATELESS_MASTERS,
            streams, now=NOW,
        )
        assert on.resolved[0].disposition == DISPOSITION_WOULD_ATTACH
        assert on.resolved[0].best.master_name == self.DATELESS_MASTERS[0]


class TestBuildMasterNameToId:
    """bead parse-from-stream: master identity from channel name vs stream."""

    CHANNELS = [
        {"id": 100, "name": "Clean Master A", "streams": [9001]},
        {"id": 101, "name": "Clean Master B", "streams": [{"id": 9002}]},
        {"id": 102, "name": "No Streams", "streams": []},
    ]

    async def test_default_uses_channel_names(self):
        from services.event_sync_resolver import build_master_name_to_id
        client = AsyncMock()
        result = await build_master_name_to_id(self.CHANNELS, client, False)
        assert result == {
            "Clean Master A": 100,
            "Clean Master B": 101,
            "No Streams": 102,
        }
        client.get_streams_by_ids.assert_not_awaited()

    async def test_flag_uses_first_attached_stream_name(self):
        from services.event_sync_resolver import build_master_name_to_id
        client = AsyncMock()
        client.get_streams_by_ids.return_value = [
            {"id": 9001, "name": "Fury vs. Usyk @ 12 Jul 06:00 PM ET"},
            {"id": 9002, "name": "Canelo vs. GGG @ 12 Jul 09:00 PM ET"},
        ]
        result = await build_master_name_to_id(self.CHANNELS, client, True)
        # Identity is the STREAM name; the channel with no streams drops out.
        assert result == {
            "Fury vs. Usyk @ 12 Jul 06:00 PM ET": 100,
            "Canelo vs. GGG @ 12 Jul 09:00 PM ET": 101,
        }
        client.get_streams_by_ids.assert_awaited_once_with([9001, 9002])


def _candidate(master_name: str, *, score: float, band: str) -> MasterCandidate:
    """Synthetic MasterCandidate for unit-level _classify proofs."""
    parsed = ParsedEvent(
        raw_name=master_name, title=master_name, start=NOW, teams=None,
        matched_pattern="synthetic",
    )
    return MasterCandidate(
        master_name=master_name, parsed=parsed, score=score, band=band,
        team_verdict="agree", time_delta_minutes=0.0, reject_reasons=(),
    )


class TestEnforceTimeWindowToggle:
    """bead krkm4: enforce_time_window=False disables the candidacy gate so
    a stream whose true master sits outside the window still attaches on
    title/team score alone. Default (True) preserves the gate exactly."""

    # Same fixture as MASTERS[0] (Mercury vs. Aces @ 6:00 PM) but the
    # secondary provider timestamps it 3 hours later — far outside the
    # 30-minute window. Team tokens agree, so with the gate off it scores
    # into the attach band.
    FAR_STREAM = SecondaryStream(
        name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 09:00 PM ET",
        group_id=20, stream_id=201, provider="FuboProvider",
    )

    def test_far_master_is_unmatched_when_window_enforced(self):
        # Default config (gate on): no master within 30 min -> no candidate.
        resolution = resolve_event_sync(
            _config(), MASTERS, [self.FAR_STREAM], now=NOW
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.result.candidates == ()

    def test_far_master_attaches_when_window_disabled(self):
        # enforce_time_window=False -> gate off, true master becomes a
        # candidate and the agreeing team tokens lift it to the attach band.
        config = _config(enforce_time_window=False)
        resolution = resolve_event_sync(
            config, MASTERS, [self.FAR_STREAM], now=NOW
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best is not None
        assert resolved.best.master_name == MASTERS[0]
        # The time delta is still reported (~180 min) even though it no
        # longer rejects — diagnostics survive the gate being off.
        assert resolved.best.time_delta_minutes == pytest.approx(180.0)

    def test_disabled_gate_still_rejects_team_conflict(self):
        # A same-name-different-teams stream must NOT attach even with the
        # time gate off — the team-conflict rail is independent of the window.
        conflict = SecondaryStream(
            name="WNBA TV 02: Sparks vs. Liberty @ 11 Jul 09:00 PM ET",
            group_id=20, stream_id=202,
        )
        config = _config(enforce_time_window=False)
        resolution = resolve_event_sync(config, MASTERS, [conflict], now=NOW)
        (resolved,) = resolution.resolved
        assert resolved.disposition != DISPOSITION_WOULD_ATTACH
        assert resolved.best is None


class TestMatchedViaProvenance:
    """S5 (bead sf8dj): the diagnostic ``matched_via`` provenance list.

    Annotation only — these tests assert WHICH optional relaxation admitted a
    would-attach row; the frozen matcher corpus and every band/score decision
    are covered elsewhere and unchanged.
    """

    def _keys(self, resolved: ResolvedStream) -> list[str]:
        return [key for key, _label in resolved.matched_via]

    def test_plain_in_window_default_match_has_empty_provenance(self):
        stream = SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )
        (resolved,) = resolve_event_sync(_config(), MASTERS, [stream], now=NOW).resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.matched_via == ()

    def test_assume_current_date_marks_the_row(self):
        # Time but no date -> a candidate only because the date was assumed.
        stream = SecondaryStream(
            name="Fubo 01: Mercury vs. Aces @ 06:00 PM ET",
            group_id=20, stream_id=201,
        )
        config = _config(assume_current_date=True)
        (resolved,) = resolve_event_sync(config, MASTERS, [stream], now=NOW).resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert self._keys(resolved) == [MATCHED_VIA_ASSUME_CURRENT_DATE[0]]

    def test_time_window_ignored_marks_the_row(self):
        # 180 min from the master; a candidate only because the gate is off.
        stream = SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 09:00 PM ET",
            group_id=20, stream_id=201,
        )
        config = _config(enforce_time_window=False)
        (resolved,) = resolve_event_sync(config, MASTERS, [stream], now=NOW).resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert self._keys(resolved) == [MATCHED_VIA_TIME_WINDOW_IGNORED[0]]

    def test_master_from_stream_marks_the_row(self):
        # parse_master_from_stream on -> the master identity came from a stream.
        stream = SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201,
        )
        config = _config(parse_master_from_stream=True)
        (resolved,) = resolve_event_sync(config, MASTERS, [stream], now=NOW).resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert self._keys(resolved) == [MATCHED_VIA_MASTER_FROM_STREAM[0]]

    # --- lowered_threshold: score below the DEFAULT effective floor. Scores
    # are exercised directly on _matched_via (constructed candidates) so the
    # provenance rule is pinned independently of the frozen fuzzy corpus. ---

    def _resolved(self, *, score, team_verdict, time_delta=0.0) -> ResolvedStream:
        parsed = ParsedEvent(
            raw_name="stream", title="T", start=NOW, teams=None,
            matched_pattern="p",
        )
        master = MasterCandidate(
            master_name="Master", parsed=parsed, score=score, band=BAND_ATTACH,
            team_verdict=team_verdict, time_delta_minutes=time_delta,
            reject_reasons=(),
        )
        result = StreamMatchResult(
            stream_name="stream", parsed=parsed, candidates=(master,),
        )
        return ResolvedStream(
            stream=SecondaryStream(name="stream", group_id=20),
            result=result, disposition=DISPOSITION_WOULD_ATTACH, best=master,
        )

    def _via(self, resolved, **overrides):
        kwargs = dict(
            patterns=None, now=NOW, assume_current_date=False,
            enforce_time_window=True, time_window_minutes=30,
            parse_master_from_stream=False,
        )
        kwargs.update(overrides)
        return [key for key, _ in _matched_via(resolved, **kwargs)]

    def test_lowered_threshold_when_score_below_teamless_default_floor(self):
        # Teamless default floor is 0.90; a 0.85 attach only cleared a lowered
        # attach_threshold.
        resolved = self._resolved(score=0.85, team_verdict=TEAM_VERDICT_ABSENT)
        assert self._via(resolved) == [MATCHED_VIA_LOWERED_THRESHOLD[0]]

    def test_lowered_threshold_when_score_below_team_agree_default_floor(self):
        # Team-agree default floor is 0.80; a 0.75 attach only cleared a
        # lowered threshold.
        resolved = self._resolved(score=0.75, team_verdict=TEAM_VERDICT_AGREE)
        assert self._via(resolved) == [MATCHED_VIA_LOWERED_THRESHOLD[0]]

    def test_no_lowered_flag_at_or_above_default_floor(self):
        # A 0.95 teamless attach clears the 0.90 default floor on its own.
        resolved = self._resolved(score=0.95, team_verdict=TEAM_VERDICT_ABSENT)
        assert self._via(resolved) == []
        # A 0.85 team-agree attach clears the 0.80 default floor on its own.
        resolved = self._resolved(score=0.85, team_verdict=TEAM_VERDICT_AGREE)
        assert self._via(resolved) == []

    def test_non_attach_disposition_never_annotated(self):
        resolved = self._resolved(score=0.75, team_verdict=TEAM_VERDICT_AGREE)
        # Same data but a non-attach disposition -> no provenance at all.
        ambiguous = ResolvedStream(
            stream=resolved.stream, result=resolved.result,
            disposition=DISPOSITION_AMBIGUOUS, best=None,
        )
        assert self._via(ambiguous) == []

    def test_multiple_relaxations_accumulate(self):
        # Gate off + teamless below floor -> both flags, keys stable/ordered.
        resolved = self._resolved(
            score=0.85, team_verdict=TEAM_VERDICT_ABSENT, time_delta=120.0,
        )
        keys = self._via(resolved, enforce_time_window=False)
        assert keys == [
            MATCHED_VIA_TIME_WINDOW_IGNORED[0],
            MATCHED_VIA_LOWERED_THRESHOLD[0],
        ]


class TestDebugLogging:
    """Gated per-candidate DEBUG logging (bead 03nji) — observability only.

    The resolver emits one structured DEBUG line per stream plus one per
    candidate for live tailing. Guarded by ``logger.isEnabledFor(DEBUG)`` so
    it is a no-op below DEBUG; it never alters the match.
    """

    _STREAMS = [
        SecondaryStream(
            name="WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=201, provider="FuboProvider",
        ),
        SecondaryStream(
            name="Fubo Sports Network 07 : NO EVENT",
            group_id=30, stream_id=302, provider="DaznProvider",
        ),
    ]

    def test_emits_per_stream_and_per_candidate_records_at_debug(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="services.event_sync_resolver"):
            resolve_event_sync(_config(), MASTERS, self._STREAMS, now=NOW)

        msgs = [
            r.getMessage() for r in caplog.records
            if r.name == "services.event_sync_resolver"
        ]
        # One resolve line per stream (2), each tagged [EVENT-SYNC].
        resolve_lines = [m for m in msgs if "resolve stream=" in m]
        assert len(resolve_lines) == 2
        assert all("[EVENT-SYNC]" in m for m in resolve_lines)
        # The attach stream's line names its winning master + disposition.
        attach_line = next(m for m in resolve_lines if "WNBA TV 01" in m)
        assert "would_attach" in attach_line
        assert "Mercury vs. Aces" in attach_line
        # At least one per-candidate line was emitted.
        assert any("candidate stream=" in m for m in msgs)

    def test_silent_below_debug(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="services.event_sync_resolver"):
            resolve_event_sync(_config(), MASTERS, self._STREAMS, now=NOW)

        assert not [
            r for r in caplog.records
            if r.name == "services.event_sync_resolver"
        ]

    def test_logging_does_not_change_resolution(self, caplog):
        import logging

        baseline = resolve_event_sync(_config(), MASTERS, self._STREAMS, now=NOW)
        with caplog.at_level(logging.DEBUG, logger="services.event_sync_resolver"):
            debugged = resolve_event_sync(_config(), MASTERS, self._STREAMS, now=NOW)

        assert [
            (r.stream.stream_id, r.disposition,
             r.best.master_name if r.best else None)
            for r in baseline.resolved
        ] == [
            (r.stream.stream_id, r.disposition,
             r.best.master_name if r.best else None)
            for r in debugged.resolved
        ]
