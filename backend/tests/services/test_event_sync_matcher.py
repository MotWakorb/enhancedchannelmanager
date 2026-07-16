"""
Unit tests for services/event_sync_matcher.py (bead enhancedchannelmanager-ti939.1.1).

Table-driven coverage of the layered event matcher:

    parse (dummy-EPG machinery + year inference)
      -> time-window blocking
      -> parsed-title fuzzy scoring (LOCALS cleaning)
      -> team-token check (order-insensitive; conflict = HARD reject)
      -> event admission policy (EVENT_ATTACH_FLOOR = 0.80 default, operator-lowerable)

Required edges per the bead: DST transitions, year inference around
Dec 31 / Jan 1, 12h/24h times, both observed date shapes ("11 Jul 06:00 PM ET"
and "Jan 17 02:45 PM ET"), order-swapped teams, separator variants,
hard-reject conflict cases, and parse-failure fallback (no time captured ->
stream unmatchable, never guessed).

The aggregate precision/recall gate lives in
tests/test_event_sync_matcher_corpus.py against the frozen corpus fixture.
"""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest
import pytz

import services.event_sync_matcher as event_sync_matcher
from services.event_sync_matcher import (
    BAND_AMBIGUOUS,
    BAND_ATTACH,
    BAND_REJECT,
    DEFAULT_EVENT_TIMEZONE,
    DEFAULT_TIME_WINDOW_MINUTES,
    EVENT_AMBIGUOUS_FLOOR,
    EVENT_ATTACH_FLOOR,
    EVENT_NO_TEAMS_FLOOR,
    EVENT_TITLE_MAX_LEN,
    REJECT_NO_PARSED_TIME,
    REJECT_OUTSIDE_TIME_WINDOW,
    REJECT_PARSE_FAILURE,
    REJECT_TEAM_TOKEN_CONFLICT,
    TEAM_VERDICT_ABSENT,
    TEAM_VERDICT_AGREE,
    TEAM_VERDICT_CONFLICT,
    groups_would_build_start,
    is_event_attachable,
    match_stream_to_masters,
    match_streams,
    parse_event_name,
    score_pair,
)

_ET = pytz.timezone(DEFAULT_EVENT_TIMEZONE)

# Fixed "now" for deterministic year inference: the live-capture date of the
# real provider names used throughout (2026-07-11).
_NOW = _ET.localize(datetime(2026, 7, 11, 12, 0, 0))


def _et(year, month, day, hour, minute):
    return _ET.localize(datetime(year, month, day, hour, minute, 0))


# ---------------------------------------------------------------------------
# Layer 1 — parse: title extraction, slot prefixes, both date shapes,
# 12h/24h, year inference, and the never-guess-time contract.
# ---------------------------------------------------------------------------


class TestParseEventName:
    def test_day_first_date_shape_parses_title_and_start(self):
        parsed = parse_event_name(
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET", now=_NOW
        )
        assert parsed.title == "Mercury vs. Aces"
        assert parsed.start == _et(2026, 7, 11, 18, 0)
        assert parsed.teams == ("Mercury", "Aces")

    def test_month_first_date_shape_parses_title_and_start(self):
        parsed = parse_event_name(
            "Fubo Sports Network 10 : Cagliari vs. Juventus @ Jan 17 02:45 PM ET",
            now=_NOW,
        )
        assert parsed.title == "Cagliari vs. Juventus"
        # Year inferred nearest to now (2026-07-11) -> Jan 17 2026.
        assert parsed.start == _et(2026, 1, 17, 14, 45)

    @pytest.mark.parametrize(
        ("name", "expected_hour", "expected_minute"),
        [
            # 12-hour with AM/PM
            ("A vs B @ 11 Jul 06:00 PM ET", 18, 0),
            ("A vs B @ 11 Jul 06:00 AM ET", 6, 0),
            ("A vs B @ 11 Jul 12:15 PM ET", 12, 15),
            ("A vs B @ 11 Jul 12:15 AM ET", 0, 15),
            # 24-hour (no AM/PM marker)
            ("A vs B @ 11 Jul 18:30 ET", 18, 30),
            ("A vs B @ 11 Jul 00:05 ET", 0, 5),
        ],
    )
    def test_12h_and_24h_times(self, name, expected_hour, expected_minute):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.start is not None
        assert parsed.start.hour == expected_hour
        assert parsed.start.minute == expected_minute

    @pytest.mark.parametrize(
        ("name", "expected_title"),
        [
            # Real observed slot prefixes.
            (
                "Fubo Sports Network 07 : Chelsea vs. Brentford @ Jan 17 10:00 AM ET",
                "Chelsea vs. Brentford",
            ),
            (
                "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                "Mercury vs. Aces",
            ),
            # No slot prefix at all.
            ("Mercury vs. Aces @ 11 Jul 06:00 PM ET", "Mercury vs. Aces"),
            # Title containing an interior colon survives the prefix strip.
            (
                "NFL Game Pass 01: 2026 NFL Draft: Day 3 @ Apr 25 12:00 PM ET",
                "2026 NFL Draft: Day 3",
            ),
        ],
    )
    def test_slot_prefix_stripping(self, name, expected_title):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.title == expected_title

    def test_no_time_captured_means_unmatchable_never_guessed(self):
        # Real placeholder shape from the live instance. The dummy-EPG
        # machinery would fall back to "now" here — the matcher must not.
        parsed = parse_event_name("Peacock 40: NO EVENT", now=_NOW)
        assert parsed.title == "NO EVENT"
        assert parsed.start is None

    def test_time_without_date_is_not_guessed(self):
        # An hour with no month/day must NOT inherit today's date.
        parsed = parse_event_name("Slot 01: A vs B at 06:00 PM", now=_NOW)
        assert parsed.start is None

    def test_empty_name_fails_parse(self):
        parsed = parse_event_name("", now=_NOW)
        assert parsed.title is None
        assert parsed.start is None

    def test_title_is_length_capped(self):
        long_title = "X" * (EVENT_TITLE_MAX_LEN * 3)
        parsed = parse_event_name(
            f"Slot 01: {long_title} @ 11 Jul 06:00 PM ET", now=_NOW
        )
        assert parsed.title is not None
        assert len(parsed.title) <= EVENT_TITLE_MAX_LEN

    @pytest.mark.parametrize(
        ("now", "name", "expected_start"),
        [
            # Late December "now": a January date belongs to NEXT year.
            (
                _et(2026, 12, 30, 12, 0),
                "Slot 01: A vs B @ 2 Jan 03:00 PM ET",
                _et(2027, 1, 2, 15, 0),
            ),
            # Early January "now": a late-December date belongs to LAST year.
            (
                _et(2027, 1, 2, 12, 0),
                "Slot 01: A vs B @ 30 Dec 07:00 PM ET",
                _et(2026, 12, 30, 19, 0),
            ),
            # Mid-year: same year wins.
            (
                _et(2026, 7, 11, 12, 0),
                "Slot 01: A vs B @ 11 Jul 06:00 PM ET",
                _et(2026, 7, 11, 18, 0),
            ),
        ],
    )
    def test_year_inference_around_year_boundary(self, now, name, expected_start):
        parsed = parse_event_name(name, now=now)
        assert parsed.start == expected_start

    def test_explicit_year_is_respected_over_inference(self):
        parsed = parse_event_name(
            "Slot 01: A vs B @ Jan 17 2028 02:45 PM ET", now=_NOW
        )
        assert parsed.start == _et(2028, 1, 17, 14, 45)

    @pytest.mark.parametrize(
        "name",
        [
            # PR #611 finding 1: an EXPLICIT-year calendar-invalid date used
            # to fall through compute_event_times' "now"-based fallback and
            # produce a start of TONIGHT — the never-guess rail must return
            # unmatchable instead.
            "Slot 01: A vs B @ Feb 30 2027 06:00 PM ET",
            "Slot 01: A vs B @ Apr 31 2026 06:00 PM ET",
            # Inferred-year path (already safe, pinned here): no candidate
            # year makes Feb 30 a real date.
            "Slot 01: A vs B @ 30 Feb 06:00 PM ET",
        ],
    )
    def test_calendar_invalid_date_is_unmatchable_never_guessed(self, name):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.title == "A vs B"
        assert parsed.start is None

    def test_at_separator_title_extends_to_the_date_shaped_at(self):
        # PR #611 finding 2: the "@" home/away separator must not truncate
        # the title — only the DATE-shaped "@ <day/month> <h:mm>" tail
        # delimits it.
        parsed = parse_event_name(
            "MSG 01: Rangers @ Islanders @ 11 Jul 07:00 PM ET", now=_NOW
        )
        assert parsed.title == "Rangers @ Islanders"
        assert parsed.teams == ("Rangers", "Islanders")
        assert parsed.start == _et(2026, 7, 11, 19, 0)

    @pytest.mark.parametrize(
        ("name", "expected_title", "expected_start"),
        [
            # bead 9c9j7: a provider/slot label appended AFTER the time used
            # to break the parse (the end-anchored time pattern rejected the
            # trailing " :Flo Racing 03"). Folding captured time into the
            # date pattern lets the tail be ignored. Single-digit hour too.
            (
                "Zenith Racing Series at Road America @ Jul 11 9:30 AM :Flo Racing 03",
                "Zenith Racing Series at Road America",
                _et(2026, 7, 11, 9, 30),
            ),
            # Day-first with a trailing suffix.
            (
                "Mercury vs. Aces @ 11 Jul 06:00 PM ET | Peacock 14",
                "Mercury vs. Aces",
                _et(2026, 7, 11, 18, 0),
            ),
        ],
    )
    def test_trailing_suffix_after_time_still_parses(
        self, name, expected_title, expected_start
    ):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.title == expected_title
        assert parsed.start == expected_start

    @pytest.mark.parametrize(
        ("name", "expected_title", "expected_start"),
        [
            # bead: parenthesized NUMERIC month-first date "(7.12 9:15 AM ET)".
            (
                "PPV EVENT 01: Zenith Racing Series at Road America "
                "(7.12 9:15 AM ET)",
                "Zenith Racing Series at Road America",
                _et(2026, 7, 12, 9, 15),
            ),
            # Slash separator, PM.
            (
                "PPV EVENT 10: Redstall vs. Courtney (7/12 12:00 PM ET)",
                "Redstall vs. Courtney",
                _et(2026, 7, 12, 12, 0),
            ),
        ],
    )
    def test_parenthesized_numeric_date_parses(
        self, name, expected_title, expected_start
    ):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.title == expected_title
        assert parsed.start == expected_start

    def test_numeric_date_needs_a_time_after_it(self):
        # A bare numeric "7.5" with no time must NOT parse a start (never
        # guessed) — guards the numeric-date pattern against false positives.
        parsed = parse_event_name("Slot 01: Game 7.5 preview show", now=_NOW)
        assert parsed.start is None

    @pytest.mark.parametrize(
        "name",
        [
            "Boxing 05 : FURY vs HALL 6PM",
            "Boxing 6: Fury v Makhmudov 19:00",
            "LIVE EVENT 05 - 4:15pm Zenith Racing Series Road America",
        ],
    )
    def test_dateless_stays_unmatchable_without_the_flag(self, name):
        # The never-guess rail: a time with no date is None by default.
        assert parse_event_name(name, now=_NOW).start is None

    @pytest.mark.parametrize(
        ("name", "expected_title", "expected_start"),
        [
            # am/pm, minutes implied :00, slot stripped.
            ("Boxing 05 : FURY vs HALL 6PM", "FURY vs HALL",
             _et(2026, 7, 11, 18, 0)),
            # 24h, 1-digit colon-less slot stripped.
            ("Boxing 6: Fury v Makhmudov 19:00", "Fury v Makhmudov",
             _et(2026, 7, 11, 19, 0)),
            # time-FIRST layout after "-".
            ("LIVE EVENT 05 - 4:15pm Zenith Racing Series Road America",
             "Zenith Racing Series Road America", _et(2026, 7, 11, 16, 15)),
        ],
    )
    def test_dateless_parses_onto_today_with_the_flag(
        self, name, expected_title, expected_start
    ):
        parsed = parse_event_name(name, now=_NOW, assume_current_date=True)
        assert parsed.title == expected_title
        assert parsed.start == expected_start

    def test_flag_does_not_override_a_real_parsed_date(self):
        # A name WITH a date keeps its real date (Jul 11) even when the flag
        # is on and "today" (Jan 1) is a completely different day.
        parsed = parse_event_name(
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            now=_et(2026, 1, 1, 12, 0), assume_current_date=True,
        )
        assert (parsed.start.month, parsed.start.day) == (7, 11)
        assert parsed.start.hour == 18

    def test_flag_still_rejects_a_name_with_no_time(self):
        parsed = parse_event_name(
            "Boxing 7: Jake Paul vs Anthony Joshua", now=_NOW,
            assume_current_date=True,
        )
        assert parsed.start is None

    @pytest.mark.parametrize(
        ("name", "expected_title", "hour", "minute"),
        [
            # bead uwm8d: time-FIRST after a colon slot (no dash).
            ("PPV 06: 4:15pm Zenith Racing Series Road America",
             "Zenith Racing Series Road America", 16, 15),
            ("PPV 08: 6pm DIRTcar Summer Nationals Late Model Crystal",
             "DIRTcar Summer Nationals Late Model Crystal", 18, 0),
            # "@ <time>" dateless — the "@" is consumed, not left in the title.
            ("PPV01: Crystal Motor Speedway @ 06:00 PM EDT",
             "Crystal Motor Speedway", 18, 0),
        ],
    )
    def test_dateless_colon_slot_and_at_time_formats(
        self, name, expected_title, hour, minute
    ):
        parsed = parse_event_name(name, now=_NOW, assume_current_date=True)
        assert parsed.title == expected_title
        assert parsed.start is not None
        assert (parsed.start.hour, parsed.start.minute) == (hour, minute)
        # never-guess preserved without the flag
        assert parse_event_name(name, now=_NOW).start is None

    def test_pipe_delimited_name_with_weekday_and_region_marker_parses(self):
        # bead 9c9j7: live pipe-delimited listing shape that produced
        # "Incomplete date/time" — "|" delimiter, a "Sun" weekday prefix, a
        # "(US)" region marker after the timezone, and a trailing provider.
        parsed = parse_event_name(
            "NEXT | OTAGO NUGGETS VS. AUCKLAND TUATARA (ROUND 14) "
            "| Sun 12 Jul 02:00 EDT (US) |  | US: ESPN+ PPV 40",
            now=_NOW,
        )
        assert parsed.start == _et(2026, 7, 12, 2, 0)
        assert "AUCKLAND TUATARA" in parsed.title

    @pytest.mark.parametrize(
        ("name", "expected_title"),
        [
            # PR #611 finding 4: series-identity prefixes are NOT slot
            # prefixes — 3-digit and 1-digit numbers survive the strip
            # (slot lists are zero-padded two digits).
            (
                "PPV 01: UFC 317: Early Prelims @ 11 Jul 06:00 PM ET",
                "UFC 317: Early Prelims",
            ),
            (
                "PPV 02: Bellator 300: Early Prelims @ 11 Jul 06:00 PM ET",
                "Bellator 300: Early Prelims",
            ),
            (
                "Formula 1: Qualifying @ 11 Jul 10:00 AM ET",
                "Formula 1: Qualifying",
            ),
        ],
    )
    def test_series_identity_prefixes_survive_slot_prefix_strip(
        self, name, expected_title
    ):
        parsed = parse_event_name(name, now=_NOW)
        assert parsed.title == expected_title


# ---------------------------------------------------------------------------
# Layer 2 — time-window blocking (including DST edges).
# ---------------------------------------------------------------------------


class TestTimeWindowBlocking:
    def test_same_teams_different_day_is_rejected(self):
        result = score_pair(
            "Fubo Sports Network 01 : Man United vs. Man City @ Jan 17 07:30 AM ET",
            "Sky 01: Manchester United vs. Manchester City @ Jan 24 07:30 AM ET",
            now=_NOW,
        )
        assert result.band == BAND_REJECT
        assert REJECT_OUTSIDE_TIME_WINDOW in result.reject_reasons
        assert result.score == 0.0

    def test_window_none_disables_gate(self):
        # bead krkm4: window_minutes=None removes the time-window gate. The
        # same far-apart, team-agreeing pair that is rejected above now
        # scores on title/team alone and reaches the attach band. The time
        # delta is still reported; it just no longer rejects.
        result = score_pair(
            "Fubo Sports Network 01 : Man United vs. Man City @ Jan 17 07:30 AM ET",
            "Sky 01: Manchester United vs. Manchester City @ Jan 24 07:30 AM ET",
            window_minutes=None,
            now=_NOW,
        )
        assert REJECT_OUTSIDE_TIME_WINDOW not in result.reject_reasons
        assert result.band == BAND_ATTACH
        assert result.time_delta_minutes is not None
        assert result.time_delta_minutes > 0.0

    def test_window_none_still_hard_rejects_team_conflict(self):
        # The disabled gate is candidacy only — the team-conflict rail is
        # independent and must still force a 0.0 hard reject.
        result = score_pair(
            "P 01: Sparks vs. Liberty @ 11 Jul 06:00 PM ET",
            "Q 01: Mercury vs. Aces @ 11 Jul 11:00 PM ET",
            window_minutes=None,
            now=_NOW,
        )
        assert result.band == BAND_REJECT
        assert result.score == 0.0

    def test_delta_exactly_at_window_edge_is_in_window(self):
        result = score_pair(
            "P 01: Alpha vs Beta @ 11 Jul 06:00 PM ET",
            "Q 01: Alpha vs Beta @ 11 Jul 06:30 PM ET",
            window_minutes=DEFAULT_TIME_WINDOW_MINUTES,
            now=_NOW,
        )
        assert result.time_delta_minutes == 30.0
        assert result.band == BAND_ATTACH

    def test_dst_spring_forward_delta_is_wall_clock_aware(self):
        # US DST 2026 springs forward on Mar 8: 01:30 EST -> 06:30 UTC,
        # 03:30 EDT -> 07:30 UTC. Real elapsed time is 60 minutes, not the
        # naive 120 — the matcher must compute the delta on aware datetimes.
        now = _et(2026, 3, 7, 12, 0)
        result = score_pair(
            "P 01: Alpha vs Beta @ 8 Mar 01:30 AM ET",
            "Q 01: Alpha vs Beta @ 8 Mar 03:30 AM ET",
            now=now,
        )
        assert result.time_delta_minutes == 60.0
        assert result.band == BAND_REJECT
        assert REJECT_OUTSIDE_TIME_WINDOW in result.reject_reasons

    def test_dst_fall_back_same_wall_time_is_zero_delta(self):
        # US DST 2026 falls back on Nov 1. 01:30 is ambiguous; both sides
        # must resolve identically (deterministic) so the delta is 0.
        now = _et(2026, 10, 31, 12, 0)
        result = score_pair(
            "P 01: Alpha vs Beta @ 1 Nov 01:30 AM ET",
            "Q 01: Alpha vs Beta @ 1 Nov 01:30 AM ET",
            now=now,
        )
        assert result.time_delta_minutes == 0.0
        assert result.band == BAND_ATTACH

    def test_same_event_across_midnight_is_in_window(self):
        result = score_pair(
            "PPV 01: Fury vs. Usyk @ 11 Jul 11:59 PM ET",
            "DAZN 05: Fury vs. Usyk @ 12 Jul 12:15 AM ET",
            now=_NOW,
        )
        assert result.time_delta_minutes == 16.0
        assert result.band == BAND_ATTACH


# ---------------------------------------------------------------------------
# Layers 3+4 — fuzzy scoring of parsed titles and the team-token check.
# ---------------------------------------------------------------------------


class TestTeamTokenCheckAndScoring:
    @pytest.mark.parametrize(
        ("name_a", "name_b"),
        [
            # Order-swapped home/away.
            (
                "Fubo Sports Network 03 : Sunderland vs. Crystal Palace @ Jan 17 10:00 AM ET",
                "ESPN+ 11 : Crystal Palace vs. Sunderland @ Jan 17 10:00 AM ET",
            ),
            # Separator variants: "vs." / "v." / "vs".
            (
                "Peacock 17: 4tos de Final: Argentina v. Suiza @ 11 Jul 07:00 PM ET",
                "Telemundo 02: 4tos de Final: Argentina vs. Suiza @ 11 Jul 07:00 PM ET",
            ),
            (
                "P 01: Liverpool vs Burnley @ 11 Jul 03:00 PM ET",
                "Q 01: Liverpool v. Burnley @ 11 Jul 03:00 PM ET",
            ),
        ],
    )
    def test_order_swaps_and_separator_variants_attach(self, name_a, name_b):
        result = score_pair(name_a, name_b, now=_NOW)
        assert result.band == BAND_ATTACH
        assert result.team_verdict == TEAM_VERDICT_AGREE

    @pytest.mark.parametrize(
        "abbreviated",
        [
            "Sky 02: Man United vs. Chelsea @ Jan 24 10:00 AM ET",
            "Sky 02: Man Utd vs. Chelsea @ Jan 24 10:00 AM ET",
            "Sky 02: MUFC vs. Chelsea @ Jan 24 10:00 AM ET",
        ],
    )
    def test_abbreviation_variants_attach_same_fixture_and_time(self, abbreviated):
        result = score_pair(
            abbreviated,
            "Peacock 09: Manchester United vs. Chelsea @ 24 Jan 10:00 AM ET",
            now=_NOW,
        )
        assert result.band == BAND_ATTACH, (
            f"{abbreviated!r} scored {result.score:.3f} "
            f"verdict={result.team_verdict} reasons={result.reject_reasons}"
        )
        assert result.team_verdict == TEAM_VERDICT_AGREE

    @pytest.mark.parametrize(
        ("name_a", "name_b"),
        [
            # Women's vs men's same fixture, same kickoff.
            (
                "DAZN 01: Barcelona vs. Chelsea @ 11 Jul 03:00 PM ET",
                "DAZN 02: Barcelona W vs. Chelsea W @ 11 Jul 03:00 PM ET",
            ),
            # U21 vs senior side, same kickoff.
            (
                "ESPN+ 05 : England vs. Spain @ 11 Jul 02:00 PM ET",
                "ESPN+ 06 : England U21 vs. Spain U21 @ 11 Jul 02:00 PM ET",
            ),
            # Multi-sport, same city names, same kickoff.
            (
                "MSG 01: New York Rangers vs. Boston Bruins @ 11 Jul 07:00 PM ET",
                "TNT 02: New York Knicks vs. Boston Celtics @ 11 Jul 07:00 PM ET",
            ),
            # Shared team, different opponent, same kickoff.
            (
                "Fubo Sports Network 07 : Chelsea vs. Brentford @ Jan 17 10:00 AM ET",
                "Peacock 23: Chelsea vs. Everton @ 17 Jan 10:00 AM ET",
            ),
        ],
    )
    def test_team_token_conflict_is_hard_reject_score_zero(self, name_a, name_b):
        result = score_pair(name_a, name_b, now=_NOW)
        assert result.band == BAND_REJECT
        assert result.team_verdict == TEAM_VERDICT_CONFLICT
        assert result.score == 0.0
        assert REJECT_TEAM_TOKEN_CONFLICT in result.reject_reasons

    def test_subset_expansion_attaches(self):
        result = score_pair(
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            "FS2 05 : Phoenix Mercury vs. Las Vegas Aces @ 11 Jul 06:00 PM ET",
            now=_NOW,
        )
        assert result.band == BAND_ATTACH
        assert result.team_verdict == TEAM_VERDICT_AGREE

    def test_titles_without_team_separator_score_on_fuzzy_alone(self):
        result = score_pair(
            "Peacock 02: Tour de France: Stage 8 @ 11 Jul 06:30 AM ET",
            "FloSports 01 : Tour de France Stage 8 @ 11 Jul 06:30 AM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_ABSENT
        assert result.band == BAND_ATTACH

    def test_unrelated_events_same_time_reject_below_floor(self):
        result = score_pair(
            "Peacock 06: 2026 MLB Draft @ 11 Jul 01:00 PM ET",
            "Peacock 07: Motocross Rd. 23: Southwick @ 11 Jul 01:00 PM ET",
            now=_NOW,
        )
        assert result.band == BAND_REJECT
        assert result.score < EVENT_AMBIGUOUS_FLOOR

    def test_both_sides_unparseable_time_is_no_parsed_time_reject(self):
        result = score_pair("Peacock 40: NO EVENT", "Peacock 39: NO EVENT", now=_NOW)
        assert result.band == BAND_REJECT
        assert REJECT_NO_PARSED_TIME in result.reject_reasons
        assert result.score == 0.0

    def test_unparseable_name_is_parse_failure_reject(self):
        result = score_pair(
            "", "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET", now=_NOW
        )
        assert result.band == BAND_REJECT
        assert REJECT_PARSE_FAILURE in result.reject_reasons
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# Layer 5 — event admission policy (its own function, its own floor).
# ---------------------------------------------------------------------------


class TestEventAdmissionPolicy:
    def test_floor_constant_is_080(self):
        assert EVENT_ATTACH_FLOOR == 0.80

    def test_threshold_below_floor_is_operator_authoritative(self):
        # bead krkm4-sibling: 0.80 is the DEFAULT, not a hard clamp. An
        # operator who lowers the threshold gets exactly that lower bar.
        assert is_event_attachable(0.79, TEAM_VERDICT_AGREE, threshold=0.5)
        # ...and the lowered bar applies to teamless pairs too, so the
        # no-teams 0.90 raise cannot silently veto the lowering.
        assert is_event_attachable(0.70, TEAM_VERDICT_ABSENT, threshold=0.65)
        assert not is_event_attachable(0.60, TEAM_VERDICT_ABSENT, threshold=0.65)
        # A default-floor config is byte-identical to prior behavior: the
        # 0.90 teamless raise still applies at threshold 0.80.
        assert not is_event_attachable(0.85, TEAM_VERDICT_ABSENT, threshold=0.80)
        assert is_event_attachable(0.85, TEAM_VERDICT_AGREE, threshold=0.80)

    def test_threshold_above_floor_is_respected(self):
        assert not is_event_attachable(0.85, TEAM_VERDICT_AGREE, threshold=0.9)
        assert is_event_attachable(0.95, TEAM_VERDICT_AGREE, threshold=0.9)

    def test_team_conflict_never_attaches_regardless_of_score(self):
        assert not is_event_attachable(1.0, TEAM_VERDICT_CONFLICT)
        assert not is_event_attachable(1.0, TEAM_VERDICT_CONFLICT, threshold=0.0)

    def test_no_team_signal_faces_the_higher_floor(self):
        # Without positive team-token agreement, lexical overlap alone must
        # clear EVENT_NO_TEAMS_FLOOR (0.90), not just the 0.80 attach floor.
        assert EVENT_NO_TEAMS_FLOOR == 0.90
        assert not is_event_attachable(0.85, TEAM_VERDICT_ABSENT)
        assert is_event_attachable(0.95, TEAM_VERDICT_ABSENT)
        assert is_event_attachable(0.85, TEAM_VERDICT_AGREE)

    def test_uncertain_team_alignment_faces_the_higher_floor(self):
        from services.event_sync_matcher import TEAM_VERDICT_UNCERTAIN

        assert not is_event_attachable(0.85, TEAM_VERDICT_UNCERTAIN)
        assert is_event_attachable(0.95, TEAM_VERDICT_UNCERTAIN)

    def test_sibling_studio_shows_land_ambiguous_not_attach(self):
        # Real incident-class shape: sibling programs sharing most tokens
        # ("Vive el Mundial" / "Hoy en el Mundial", same franchise, both
        # real provider names) token-set-score ~0.89 with no team signal —
        # must route to operator review, never auto-attach.
        result = score_pair(
            "Peacock 12: Vive el Mundial (Julio 11) @ 11 Jul 04:00 PM ET",
            "Peacock 10: Hoy en el Mundial (Julio 11) @ 11 Jul 03:30 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_ABSENT
        assert result.band == BAND_AMBIGUOUS

    def test_shared_nickname_different_opponent_is_hard_rejected(self):
        # "Rangers vs. Islanders" / "Rangers vs. Yankees" at the same
        # kickoff: opponents clearly differ -> conflict, not a 0.80 attach.
        result = score_pair(
            "ESPN 01 : Rangers vs. Islanders @ 11 Jul 07:00 PM ET",
            "ESPN 02 : Rangers vs. Yankees @ 11 Jul 07:00 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_CONFLICT
        assert result.band == BAND_REJECT
        assert result.score == 0.0

    def test_at_separator_teams_reach_the_conflict_rail(self):
        # PR #611 finding 2: before the fix the first "@" truncated the
        # title to "Rangers", teams never parsed, and token-set subset
        # scoring attached this pair at 1.0.
        result = score_pair(
            "MSG 01: Rangers @ Islanders @ 11 Jul 07:00 PM ET",
            "YES 01: Rangers vs. Yankees @ 11 Jul 07:00 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_CONFLICT
        assert result.band == BAND_REJECT
        assert result.score == 0.0
        assert REJECT_TEAM_TOKEN_CONFLICT in result.reject_reasons

    def test_at_separator_same_event_attaches(self):
        result = score_pair(
            "MSG 01: Rangers @ Islanders @ 11 Jul 07:00 PM ET",
            "ESPN+ 07 : New York Rangers @ New York Islanders @ 11 Jul 07:00 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_AGREE
        assert result.band == BAND_ATTACH

    def test_near_length_sibling_team_names_are_hard_rejected(self):
        # PR #611 finding 3: 'austria' is a same-first-letter subsequence
        # of 'australia' (raw ratio 0.875) — different national teams
        # sharing an opponent at the same kickoff must conflict, not
        # auto-attach at 0.94.
        result = score_pair(
            "P 01: Australia vs France @ 11 Jul 03:00 PM ET",
            "Q 01: Austria vs France @ 11 Jul 03:00 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_CONFLICT
        assert result.band == BAND_REJECT
        assert result.score == 0.0

    def test_disjoint_numeric_identities_hard_reject_teamless_titles(self):
        # PR #611 finding 4: 'UFC 317' vs 'Bellator 300' — series numbers
        # are identity for team-less titles.
        from services.event_sync_matcher import REJECT_NUMERIC_IDENTITY_CONFLICT

        result = score_pair(
            "PPV 01: UFC 317: Early Prelims @ 11 Jul 06:00 PM ET",
            "PPV 02: Bellator 300: Early Prelims @ 11 Jul 06:00 PM ET",
            now=_NOW,
        )
        assert result.band == BAND_REJECT
        assert result.score == 0.0
        assert REJECT_NUMERIC_IDENTITY_CONFLICT in result.reject_reasons

    def test_one_sided_or_overlapping_numbers_pass_the_numeric_rail(self):
        # One side without numbers (Session 2 vs Evening Session) and
        # overlapping sets (Stage 8 vs 2026 ... Stage 8) must not reject.
        r1 = score_pair(
            "Sky 08: World Darts Masters: Session 2 @ 11 Jul 02:00 PM ET",
            "Viaplay 01 : World Darts Masters Evening Session @ 11 Jul 02:00 PM ET",
            now=_NOW,
        )
        assert r1.band == BAND_ATTACH
        r2 = score_pair(
            "Peacock 02: Tour de France: Stage 8 @ 11 Jul 06:30 AM ET",
            "FloSports 01 : Tour de France 2026 Stage 8 @ 11 Jul 06:30 AM ET",
            now=_NOW,
        )
        assert r2.band == BAND_ATTACH

    def test_qualifier_synonyms_are_one_class_across_providers(self):
        # PR #611 finding 5: "W" and "Women" spell the same women's-side
        # qualifier — same fixture, same kickoff, must attach.
        result = score_pair(
            "DAZN 02: Barcelona W vs. Chelsea W @ 11 Jul 03:00 PM ET",
            "Sky 11: Barcelona Women vs. Chelsea Women @ 11 Jul 03:00 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == TEAM_VERDICT_AGREE
        assert result.band == BAND_ATTACH
        # ...while a class mismatch (women's vs men's) still hard-rejects
        # (pinned by test_team_token_conflict_is_hard_reject_score_zero).

    def test_score_pair_honors_low_threshold(self):
        # bead krkm4-sibling: the caller's threshold is authoritative. The
        # same mid-band pair that lands AMBIGUOUS at the default floor
        # ATTACHES when the operator deliberately lowers the threshold below
        # 0.80 — the sub-floor value also bypasses the teamless 0.90 raise.
        default = score_pair(
            "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
            "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
            now=_NOW,
        )
        assert EVENT_AMBIGUOUS_FLOOR <= default.score < EVENT_ATTACH_FLOOR
        assert default.band == BAND_AMBIGUOUS

        lowered = score_pair(
            "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
            "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
            threshold=0.3,
            now=_NOW,
        )
        assert lowered.score == default.score  # same score, different bar
        assert lowered.band == BAND_ATTACH

    def test_event_floor_is_independent_of_dedup_no_callsign_floor(self):
        # Drift protection: the event admission policy must not share the
        # dedup matcher's knob (allow_no_callsign / NO_CALLSIGN_FLOOR).
        from services.dedup_matcher import NO_CALLSIGN_FLOOR

        assert EVENT_ATTACH_FLOOR is not NO_CALLSIGN_FLOOR
        source = inspect.getsource(event_sync_matcher)
        assert "NO_CALLSIGN_FLOOR" not in source
        assert "allow_no_callsign" not in source


# ---------------------------------------------------------------------------
# Layer 3 addendum — title-level initialism bridge (teamless acronyms).
# ---------------------------------------------------------------------------


class TestTitleInitialismBridge:
    """bead — RoC <-> Race of Champions: acronyms in TEAMLESS titles (no
    vs/@ split, so the team-token layer never runs) match their expansion."""

    def test_acronym_title_scores_above_plain_fuzzy(self):
        from services.event_sync_matcher import _fuzzy_title_score

        acro = "RoC Modifieds at Shangri La II"
        expanded = "Race of Champions Modifieds at Shangri La II"
        # Same strings with the acronym pre-expanded on both sides would score
        # ~1.0; the bridge lifts the acronym/expansion pair well above the
        # plain token_set_ratio, which counts r/o/c as three missing tokens.
        assert _fuzzy_title_score(acro, expanded) >= 0.90

    def test_bridge_only_adds_agreement_never_lowers(self):
        # A pair with no acronym relationship is unchanged (the bridge is a
        # no-op when no standalone token spells a consecutive run).
        from services.event_sync_matcher import (
            _collapse_title_initialisms,
        )
        tokens = ["mercury", "vs", "aces"]
        assert _collapse_title_initialisms(tokens, ["liberty", "sparks"]) == tokens

    def test_collapse_reuses_team_initialism_helper_with_fc_suffix(self):
        from services.event_sync_matcher import _collapse_title_initialisms

        # 'mufc' collapses 'manchester united' (FC-suffix aware), same rule
        # the team layer uses.
        out = _collapse_title_initialisms(
            ["manchester", "united", "friendly"], ["mufc"]
        )
        assert out == ["mufc", "friendly"]

    def test_teamless_acronym_pair_reaches_attach_with_clean_titles(self):
        # End-to-end through score_pair: clean titles + the bridge clear the
        # 0.90 teamless floor and ATTACH.
        result = score_pair(
            "RoC Modifieds at Shangri La II @ 11 Jul 03:00 PM ET",
            "Race of Champions Modifieds at Shangri La II @ 11 Jul 03:00 PM ET",
            now=_NOW,
        )
        assert result.band == BAND_ATTACH


class TestHyphenVariantBridge:
    """bead — 'Shangri-La' (hyphen) vs 'Shangri La' (space): provider spelling
    variance that clean_name keeps distinct. Split is corroboration-gated."""

    def test_hyphen_vs_space_scores_full_when_corroborated(self):
        from services.event_sync_matcher import _fuzzy_title_score

        # The other side carries the split form, so 'off-road'/'shangri-la'
        # collapse to a full match.
        assert _fuzzy_title_score(
            "AMSOIL Championship Off Road at Shangri La",
            "AMSOIL Championship Off-Road at Shangri-La",
        ) == 1.0

    def test_hyphenated_compound_not_split_without_corroboration(self):
        # 'pre-race' must NOT split into 'pre'/'race' when the other title has
        # no 'pre race' run — otherwise the spurious 'race' token lifts an
        # unrelated same-venue/same-slot show (frozen-corpus regression class).
        from services.event_sync_matcher import _bridge_hyphen_variants

        out = _bridge_hyphen_variants(
            ["motocross", "southwick", "pre-race", "show"],
            ["race", "day", "live", "southwick"],
        )
        assert "pre-race" in out
        assert "race" not in out  # the compound stayed intact

    def test_different_shows_sharing_venue_stay_rejected(self):
        # End-to-end guard mirroring corpus line 41.
        result = score_pair(
            "Peacock 04: Race Day Live: Southwick @ 11 Jul 10:00 AM ET",
            "MAVTV 01: Motocross Southwick Pre-Race Show @ 11 Jul 10:00 AM ET",
            now=_NOW,
        )
        assert result.band == BAND_REJECT


class TestPunctuationNormalization:
    """event-sync-punct bead — apostrophe-family fusion + underscore split in
    the LOCALS cleaner. Two teamless Flo Racing masters landed in the ambiguous
    band because source punctuation broke the token_set_ratio subset match:
    backtick-as-apostrophe ('Joker`s' 0.837) and underscore ('Utica_Rome'
    0.703). Both need >= EVENT_NO_TEAMS_FLOOR (0.90) to attach."""

    def test_backtick_apostrophe_master_attaches(self):
        result = score_pair(
            "PPV 02: HLR Jokers Jackpot at Eldora @ 11 Jul 06:15 PM ET",
            "FloRacing 10 : HLR Joker`s Jackpot at Eldora @ 11 Jul 06:15 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == "absent"
        assert result.fuzzy_score == 1.0
        assert result.band == BAND_ATTACH

    def test_underscore_master_attaches(self):
        result = score_pair(
            "PPV 03: Weekly Racing at Utica Rome @ 11 Jul 06:15 PM ET",
            "FloRacing 11 : Weekly Racing at Utica_Rome @ 11 Jul 06:15 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == "absent"
        assert result.fuzzy_score == 1.0
        assert result.band == BAND_ATTACH

    def test_hyphen_stream_vs_underscore_master_attaches(self):
        # After the underscore split the master reads 'utica rome', so the
        # corroborated hyphen bridge splits the stream's 'utica-rome' and both
        # reach the subset match — the two punct fixes interacting.
        result = score_pair(
            "PPV 03: Weekly Racing at Utica-Rome @ 11 Jul 06:15 PM ET",
            "FloRacing 11 : Weekly Racing at Utica_Rome @ 11 Jul 06:15 PM ET",
            now=_NOW,
        )
        assert result.team_verdict == "absent"
        assert result.fuzzy_score == 1.0
        assert result.band == BAND_ATTACH

    def test_fuzzy_title_score_lifts_both_shapes_to_full(self):
        from services.event_sync_matcher import _fuzzy_title_score

        assert _fuzzy_title_score(
            "HLR Jokers Jackpot at Eldora", "HLR Joker`s Jackpot at Eldora"
        ) == 1.0
        assert _fuzzy_title_score(
            "Weekly Racing at Utica Rome", "Weekly Racing at Utica_Rome"
        ) == 1.0

    @pytest.mark.parametrize("apostrophe", ["'", "’", "ʼ", "`", "´"])
    def test_team_tokens_fuse_apostrophe_family_consistently(self, apostrophe):
        # Every apostrophe encoding fuses to the SAME single token, so two
        # providers spelling a possessive team differently still align. Without
        # the fix backtick/acute (non-\w) would SPLIT into ['joker','s'].
        from services.event_sync_matcher import _team_tokens

        assert _team_tokens(f"Joker{apostrophe}s FC") == ["jokers", "fc"]


# ---------------------------------------------------------------------------
# Output contract — ordered candidates against master channels.
# ---------------------------------------------------------------------------


class TestMatchStreamToMasters:
    _MASTERS = [
        # True counterpart (same fixture, same kickoff, expanded names).
        "Phoenix Mercury vs. Las Vegas Aces @ 11 Jul 06:00 PM ET",
        # Same teams, wrong day — must be excluded by time blocking.
        "Phoenix Mercury vs. Las Vegas Aces @ 12 Jul 06:00 PM ET",
        # Same kickoff, conflicting teams — candidate, but hard-rejected.
        "New York Liberty vs. Chicago Sky @ 11 Jul 06:00 PM ET",
        # Unparseable master (no time) — never a candidate.
        "NO EVENT",
    ]

    def test_candidates_are_time_blocked_scored_and_ordered(self):
        result = match_stream_to_masters(
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            self._MASTERS,
            now=_NOW,
        )
        assert result.unmatchable_reason is None
        names = [c.master_name for c in result.candidates]
        # Wrong-day and unparseable masters are not candidates at all.
        assert "Phoenix Mercury vs. Las Vegas Aces @ 12 Jul 06:00 PM ET" not in names
        assert "NO EVENT" not in names
        # Best candidate first.
        assert names[0] == "Phoenix Mercury vs. Las Vegas Aces @ 11 Jul 06:00 PM ET"
        top = result.candidates[0]
        assert top.band == BAND_ATTACH
        assert top.team_verdict == TEAM_VERDICT_AGREE
        assert top.time_delta_minutes == 0.0
        # The conflicting same-kickoff master is present but hard-rejected.
        conflicted = next(
            c for c in result.candidates
            if c.master_name == "New York Liberty vs. Chicago Sky @ 11 Jul 06:00 PM ET"
        )
        assert conflicted.band == BAND_REJECT
        assert conflicted.score == 0.0
        assert REJECT_TEAM_TOKEN_CONFLICT in conflicted.reject_reasons

    def test_unparseable_stream_is_unmatchable(self):
        result = match_stream_to_masters("Peacock 40: NO EVENT", self._MASTERS, now=_NOW)
        assert len(result.candidates) == 0
        assert result.unmatchable_reason == REJECT_NO_PARSED_TIME

    def test_candidates_carry_no_channel_ids(self):
        # Master identity is by name/parsed identity only — never cached IDs.
        result = match_stream_to_masters(
            "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            self._MASTERS,
            now=_NOW,
        )
        candidate = result.candidates[0]
        assert not hasattr(candidate, "channel_id")
        assert not hasattr(candidate, "master_channel_id")

    def test_deterministic_tie_break_by_master_name(self):
        masters = [
            "Zeta feed: Alpha vs Beta @ 11 Jul 06:00 PM ET",
            "Acme feed: Alpha vs Beta @ 11 Jul 06:00 PM ET",
        ]
        result = match_stream_to_masters(
            "P 01: Alpha vs Beta @ 11 Jul 06:00 PM ET", masters, now=_NOW
        )
        scores = [c.score for c in result.candidates]
        assert scores[0] == scores[1]
        assert [c.master_name for c in result.candidates] == sorted(masters)

    def test_master_patterns_parse_masters_independently(self):
        # ti939.1.4: per-group pattern overrides mean masters and secondaries
        # may carry different name shapes. master_patterns parses ONLY the
        # master side; ``patterns`` (here None -> built-in defaults) parses
        # the streams.
        # Uses a ">>" delimiter the built-in defaults do NOT recognize (the
        # defaults accept "@" and "|", bead 9c9j7), so the master shape stays
        # unparseable without master_patterns and the test's intent survives.
        master_patterns = [{
            "name": "master-shape",
            "title_pattern": r"^MASTER\s*>>\s*(?P<title>.+?)\s*>>.*$",
            "time_pattern": r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[AP])M\s*$",
            "date_pattern": r">>\s*(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})\s+\d{1,2}:\d{2}",
        }]
        masters = ["MASTER >> Mercury vs. Aces >> 11 Jul 06:00 PM"]
        stream = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"

        # Without master_patterns the default patterns cannot extract a
        # complete date+time from the master shape -> no candidates.
        without = match_streams([stream], masters, now=_NOW)[0]
        assert without.candidates == ()

        with_master = match_streams(
            [stream], masters, master_patterns=master_patterns, now=_NOW
        )[0]
        assert with_master.candidates
        assert with_master.candidates[0].band == BAND_ATTACH
        assert with_master.candidates[0].master_name == masters[0]


# ---------------------------------------------------------------------------
# groups_would_build_start — the matcher-level validity surface the Test
# Patterns panel consumes (bead hirm6). Must agree with parse_event_name's
# never-guess semantics by construction (it IS _groups_have_complete_time +
# _build_start), so these tests pin the contract at the public boundary.
# ---------------------------------------------------------------------------


class TestGroupsWouldBuildStart:
    def _valid_groups(self, **overrides):
        groups = {
            "title": "Mercury vs. Aces",
            "day": "11",
            "month": "Jul",
            "hour": "06",
            "minute": "00",
            "ampm": "P",
        }
        groups.update(overrides)
        return groups

    def test_true_for_a_complete_real_date_time(self):
        assert groups_would_build_start(self._valid_groups(), now=_NOW) is True

    def test_false_for_none_or_missing_time_groups(self):
        assert groups_would_build_start(None, now=_NOW) is False
        assert groups_would_build_start({}, now=_NOW) is False
        assert groups_would_build_start(
            {"title": "Big Fight Night"}, now=_NOW
        ) is False

    def test_false_for_garbage_month_name(self):
        # A custom pattern capturing a non-month token: complete-looking
        # groups, but no month → the matcher would never build a start.
        assert groups_would_build_start(
            self._valid_groups(month="Foo"), now=_NOW
        ) is False

    def test_false_for_day_45_jul_not_a_real_calendar_date(self):
        # The PR #614 review case: 'A vs B @ 45 Jul 06:00 PM ET' shows all
        # date/time groups captured, yet July 45 does not exist.
        assert groups_would_build_start(
            self._valid_groups(day="45"), now=_NOW
        ) is False

    def test_false_for_hour_past_23(self):
        assert groups_would_build_start(
            self._valid_groups(hour="25", ampm=None), now=_NOW
        ) is False

    def test_false_for_feb_30_even_with_explicit_year(self):
        assert groups_would_build_start(
            self._valid_groups(day="30", month="Feb", year="2027"), now=_NOW
        ) is False

    def test_agrees_with_parse_event_name_on_the_45_jul_case(self):
        # The whole point: the panel's flag can never say 'Parsed' where the
        # matcher would record a parse failure (start=None).
        parsed = parse_event_name("A vs B @ 45 Jul 06:00 PM ET", now=_NOW)
        assert parsed.start is None
        assert groups_would_build_start(
            self._valid_groups(title="A vs B", day="45"), now=_NOW
        ) is False


# ---------------------------------------------------------------------------
# Module purity — no engine/executor imports (isolated service module).
# ---------------------------------------------------------------------------


class TestModulePurity:
    def test_no_channel_pipeline_imports(self):
        import ast

        tree = ast.parse(inspect.getsource(event_sync_matcher))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            name for name in imported
            if "channel_pipeline" in name
        }
        assert forbidden == set(), f"engine imports leaked into matcher: {forbidden}"
