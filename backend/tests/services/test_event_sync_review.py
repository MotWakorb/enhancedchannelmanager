"""
Event sync review queue — fingerprint identity + decision application
(bead enhancedchannelmanager-ti939.3.2).

These tests pin the SECURITY-critical keying contract (epic ti939.3):
review decisions key on content fingerprints — (provider_id,
normalized_stream_name_hash, normalized_event_key) — never channel/stream
IDs, so a decision survives Dispatcharr refreshes (ID churn) and re-applies
whenever the same provider string + event identity recurs.

Decision application is tested THROUGH ``resolve_event_sync`` — the single
decision path shared by preview and attach — so these proofs inherit the
dry-run-parity guarantee:

* accepted pairing → ``would_attach`` with ``attach_source="review_queue"``
  (contested and band-ambiguous cases both);
* rejected pairing → suppressed before classification (a contest collapses
  to the surviving candidate; a lone rejected winner becomes unmatched);
* accepts never override the matcher's hard rejects (reject-band
  candidates stay dead);
* still-ambiguous streams expose enqueue-eligible ``review_candidates``.

Pure modules — no DB, no Dispatcharr client, no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from services.event_sync_matcher import (
    BAND_ATTACH,
    DEFAULT_EVENT_TIMEZONE,
    SYNTHESIZED_DATE_PATTERN_NAMES,
    parse_event_name,
)
from services.event_sync_resolver import (
    AMBIGUOUS_REASON_CONTESTED,
    AMBIGUOUS_REASON_STALE_DATELESS_NAME,
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_UNMATCHED,
    DISPOSITION_WOULD_ATTACH,
    SecondaryStream,
    resolve_event_sync,
)
from services.event_sync_review import (
    ATTACH_SOURCE_REVIEW_QUEUE,
    ATTACH_SOURCE_THRESHOLD,
    EMPTY_DECISIONS,
    PROVIDER_ID_UNKNOWN,
    ReviewDecisions,
    master_event_key,
    normalize_stream_name,
    pairing_key,
    stream_name_hash,
)

NOW = pytz.timezone(DEFAULT_EVENT_TIMEZONE).localize(
    datetime(2026, 7, 11, 12, 0, 0)
)


def _config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "enabled": True,
    }
    config.update(overrides)
    return config


def _key(provider_id: int | None, stream_name: str, master_name: str):
    """Fingerprint of one (stream, master) pairing, from NAMES only."""
    parsed = parse_event_name(master_name, None, now=NOW)
    return pairing_key(provider_id, stream_name, parsed)


# The corpus-proven contested scenario (PR #613 rail): the team-agree boost
# ties the main card and the prelims at identical scores.
CONTESTED_MASTERS = [
    "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
    "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET",
]
CONTESTED_STREAM = SecondaryStream(
    name="BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
    group_id=20, stream_id=201, provider="BoxProvider", provider_id=7,
)


class TestFingerprints:
    def test_hash_is_stable_across_cosmetic_churn(self):
        # Case + spacing churn must NOT mint a new question after a refresh.
        assert stream_name_hash("BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET") \
            == stream_name_hash("box hd: fury VS. usyk @ 11 jul 08:00 pm et")

    def test_hash_differs_for_different_events(self):
        assert stream_name_hash("BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET") \
            != stream_name_hash("BOX HD: Canelo vs. Crawford @ 11 Jul 08:00 PM ET")

    def test_normalize_falls_back_when_cleaner_strips_everything(self):
        # An all-punctuation name must fingerprint deterministically, not
        # collide on the empty string with every other such name.
        assert normalize_stream_name("###") != ""
        assert normalize_stream_name("###") != normalize_stream_name("@@@")

    def test_event_key_is_timezone_representation_independent(self):
        # Same instant parsed under different display timezones → same key.
        parsed_et = parse_event_name(
            "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET", None, now=NOW,
        )
        utc_now = NOW.astimezone(pytz.utc)
        parsed_utc = parse_event_name(
            "PPV 01: Fury vs. Usyk @ 12 Jul 00:00 AM ET", None, now=utc_now,
            event_timezone="UTC",
        )
        assert master_event_key(parsed_et) is not None
        assert master_event_key(parsed_et) == master_event_key(parsed_utc)

    def test_event_key_distinguishes_sessions_of_one_fixture(self):
        main = parse_event_name(CONTESTED_MASTERS[0], None, now=NOW)
        prelims = parse_event_name(CONTESTED_MASTERS[1], None, now=NOW)
        assert master_event_key(main) != master_event_key(prelims)

    def test_event_key_none_for_incomplete_parse(self):
        parsed = parse_event_name("Peacock 40: NO EVENT", None, now=NOW)
        assert master_event_key(parsed) is None
        assert pairing_key(7, "any stream", parsed) is None

    def test_unknown_provider_maps_to_sentinel(self):
        parsed = parse_event_name(CONTESTED_MASTERS[0], None, now=NOW)
        key = pairing_key(None, "some stream", parsed)
        assert key[0] == PROVIDER_ID_UNKNOWN


class TestAcceptedDecisions:
    def test_accept_resolves_a_contested_stream(self):
        # Operator accepted the PRELIMS pairing: the contested stream now
        # attaches to the accepted master, marked as queue-driven.
        decisions = ReviewDecisions(accepted=frozenset({
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[1]),
        }))
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == CONTESTED_MASTERS[1]
        assert resolved.attach_source == ATTACH_SOURCE_REVIEW_QUEUE
        assert resolved.ambiguous_reason is None
        assert resolved.review_candidates == ()

    def test_accept_resolves_a_band_ambiguous_stream(self):
        # Related non-team titles score into the ambiguous band; a prior
        # accept auto-attaches the pairing.
        masters = ["Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET"]
        stream = SecondaryStream(
            name="IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
            group_id=20, stream_id=202, provider_id=7,
        )
        baseline = resolve_event_sync(_config(), masters, [stream], now=NOW)
        assert baseline.resolved[0].disposition == DISPOSITION_AMBIGUOUS

        decisions = ReviewDecisions(accepted=frozenset({
            _key(7, stream.name, masters[0]),
        }))
        resolution = resolve_event_sync(
            _config(), masters, [stream], now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == masters[0]
        assert resolved.attach_source == ATTACH_SOURCE_REVIEW_QUEUE

    def test_accept_survives_stream_id_churn(self):
        # THE keying acceptance criterion: after a simulated refresh the
        # stream carries a brand-new id — the fingerprint-keyed decision
        # still applies because identity is content, never IDs.
        decisions = ReviewDecisions(accepted=frozenset({
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[1]),
        }))
        refreshed = SecondaryStream(
            name=CONTESTED_STREAM.name, group_id=20,
            stream_id=999_999, provider="BoxProvider", provider_id=7,
        )
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [refreshed],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.attach_source == ATTACH_SOURCE_REVIEW_QUEUE

    def test_accept_never_resurrects_a_reject_band_candidate(self):
        # Hard team-token conflict → reject band. An accept of that pairing
        # must NOT override the matcher's hard reject (precision rail).
        masters = ["Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"]
        stream = SecondaryStream(
            name="WNBA TV 02: Sparks vs. Liberty @ 11 Jul 06:00 PM ET",
            group_id=20, stream_id=203, provider_id=7,
        )
        decisions = ReviewDecisions(accepted=frozenset({
            _key(7, stream.name, masters[0]),
        }))
        resolution = resolve_event_sync(
            _config(), masters, [stream], now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.attach_source is None

    def test_decision_scoped_to_provider(self):
        # The same provider string from a DIFFERENT provider is a different
        # fingerprint — the decision must not bleed across accounts.
        decisions = ReviewDecisions(accepted=frozenset({
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[1]),
        }))
        other_provider = SecondaryStream(
            name=CONTESTED_STREAM.name, group_id=20,
            stream_id=204, provider_id=8,
        )
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [other_provider],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS


class TestRejectedDecisions:
    def test_reject_collapses_a_contest_to_the_survivor(self):
        # Operator rejected the PRELIMS pairing: the contest collapses and
        # the main card attaches via the ordinary threshold path.
        decisions = ReviewDecisions(rejected=frozenset({
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[1]),
        }))
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.best.master_name == CONTESTED_MASTERS[0]
        assert resolved.attach_source == ATTACH_SOURCE_THRESHOLD
        assert resolved.rejected_suppressed == 1

    def test_reject_suppresses_even_a_threshold_attach(self):
        # An explicit operator "no" outranks score drift: the lone winning
        # candidate, once rejected, can never attach again.
        masters = ["PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET"]
        decisions = ReviewDecisions(rejected=frozenset({
            _key(7, CONTESTED_STREAM.name, masters[0]),
        }))
        resolution = resolve_event_sync(
            _config(), masters, [CONTESTED_STREAM],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.rejected_suppressed == 1
        assert resolved.review_candidates == ()

    def test_reject_prevents_reenqueue_of_the_pairing(self):
        # Rejecting BOTH contested pairings: nothing left to ask — the
        # stream is unmatched and review_candidates stays empty (the queue
        # must not refill with answered questions).
        decisions = ReviewDecisions(rejected=frozenset({
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[0]),
            _key(7, CONTESTED_STREAM.name, CONTESTED_MASTERS[1]),
        }))
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.rejected_suppressed == 2
        assert resolved.review_candidates == ()


class TestReviewCandidates:
    def test_ambiguous_stream_exposes_enqueue_eligible_pairings(self):
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM], now=NOW,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS
        assert resolved.ambiguous_reason == AMBIGUOUS_REASON_CONTESTED
        names = [c.master_name for c in resolved.review_candidates]
        assert set(names) == set(CONTESTED_MASTERS)
        # Best-first, deterministic.
        assert resolved.review_candidates[0].band == BAND_ATTACH

    def test_would_attach_stream_has_no_review_candidates(self):
        masters = ["PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET"]
        resolution = resolve_event_sync(
            _config(), masters, [CONTESTED_STREAM], now=NOW,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.attach_source == ATTACH_SOURCE_THRESHOLD
        assert resolved.review_candidates == ()

    def test_empty_decisions_match_no_decisions(self):
        with_none = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM], now=NOW,
        )
        with_empty = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [CONTESTED_STREAM],
            now=NOW, decisions=EMPTY_DECISIONS,
        )
        assert with_none == with_empty

    def test_unknown_provider_stream_matches_sentinel_decision(self):
        # A stream with no resolvable account id keys on the sentinel; a
        # decision recorded under the sentinel re-applies to it.
        no_provider = SecondaryStream(
            name=CONTESTED_STREAM.name, group_id=20, stream_id=205,
        )
        decisions = ReviewDecisions(accepted=frozenset({
            _key(None, no_provider.name, CONTESTED_MASTERS[1]),
        }))
        resolution = resolve_event_sync(
            _config(), CONTESTED_MASTERS, [no_provider],
            now=NOW, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.attach_source == ATTACH_SOURCE_REVIEW_QUEUE


# ---------------------------------------------------------------------------
# Dateless fingerprint churn fix (bead enhancedchannelmanager-t6bin).
#
# Under assume_current_date the parser SYNTHESIZES the date from "now"
# (the three _ASSUME_DATE_PATTERNS variants). A key that embeds that
# fabricated date churns at midnight, so accepts/rejects for a recurring
# dateless slot never carried forward and the jqwfq rail re-demoted the
# same slot daily. Fix: such parses key on
# ``<cleaned title>|dateless|<LOCAL clock HH:MM><standard tz offset>``.
# The STANDARD (non-DST) offset of the parse timezone is used so the key
# does not flip twice a year at DST transitions; the local clock time is
# what the provider name literally says, so it is DST-stable too.
# ---------------------------------------------------------------------------

NOW_WINTER = pytz.timezone(DEFAULT_EVENT_TIMEZONE).localize(
    datetime(2026, 1, 15, 12, 0, 0)
)

DATELESS_AMPM = "Boxing 05: FURY vs HALL 6PM"
DATELESS_24H = "MOTOR: GP Qualifying 19:00"
DATELESS_TIME_FIRST = "LIVE EVENT 05 - 4:15pm Zenith Racing Series"


def _parse_dateless(name: str, now=NOW, event_timezone=DEFAULT_EVENT_TIMEZONE):
    return parse_event_name(
        name, None, now=now, event_timezone=event_timezone,
        assume_current_date=True,
    )


class TestDatelessEventKeys:
    def test_ampm_key_exact_format(self):
        parsed = _parse_dateless(DATELESS_AMPM)
        assert parsed.matched_pattern == "dateless-title-time-ampm"
        assert master_event_key(parsed) == "fury vs hall|dateless|18:00-05:00"

    def test_24h_key_exact_format(self):
        parsed = _parse_dateless(DATELESS_24H)
        assert parsed.matched_pattern == "dateless-title-time-24h"
        assert master_event_key(parsed) \
            == "motor gp qualifying|dateless|19:00-05:00"

    def test_time_first_key_exact_format(self):
        parsed = _parse_dateless(DATELESS_TIME_FIRST)
        assert parsed.matched_pattern == "dateless-time-first"
        assert master_event_key(parsed) \
            == "zenith racing series|dateless|16:15-05:00"

    def test_synthesized_pattern_names_constant_covers_exactly_the_three(self):
        # The review module keys off this constant; it must track the
        # matcher's _ASSUME_DATE_PATTERNS by construction.
        assert SYNTHESIZED_DATE_PATTERN_NAMES == frozenset({
            "dateless-title-time-ampm",
            "dateless-title-time-24h",
            "dateless-time-first",
        })

    def test_key_stable_across_days(self):
        # THE bug: the same recurring slot name must mint the SAME key
        # tomorrow — the synthesized date never enters the key.
        day1 = _parse_dateless(DATELESS_AMPM, now=NOW)
        day2 = _parse_dateless(DATELESS_AMPM, now=NOW + timedelta(days=1))
        assert day1.start.date() != day2.start.date()
        assert master_event_key(day1) == master_event_key(day2)

    def test_key_stable_across_dst_transition(self):
        # Same slot name parsed on an EDT date and an EST date → SAME key.
        # (Standard offset, not the datetime's actual offset — actual
        # offsets differ: -04:00 summer vs -05:00 winter.)
        summer = _parse_dateless(DATELESS_AMPM, now=NOW)
        winter = _parse_dateless(DATELESS_AMPM, now=NOW_WINTER)
        assert summer.start.utcoffset() != winter.start.utcoffset()
        assert master_event_key(summer) == master_event_key(winter)

    def test_keys_distinct_by_clock_time(self):
        # Same title at two different clock times = two different slots.
        six = _parse_dateless("Boxing 05: FURY vs HALL 6PM")
        nine = _parse_dateless("Boxing 05: FURY vs HALL 9PM")
        assert master_event_key(six) != master_event_key(nine)

    def test_keys_distinct_by_title(self):
        a = _parse_dateless("Boxing 05: FURY vs HALL 6PM")
        b = _parse_dateless("Boxing 05: FURY vs WILDER 6PM")
        assert master_event_key(a) != master_event_key(b)

    def test_utc_rule_timezone_offset_is_plus_zero(self):
        parsed = _parse_dateless(
            DATELESS_24H, now=NOW.astimezone(pytz.utc), event_timezone="UTC",
        )
        assert master_event_key(parsed) \
            == "motor gp qualifying|dateless|19:00+00:00"

    def test_dateless_parse_without_start_still_yields_none(self):
        # Title-only fallback (no time captured) must stay unkeyable even
        # under assume_current_date.
        parsed = _parse_dateless("Peacock 40: NO EVENT")
        assert parsed.start is None
        assert master_event_key(parsed) is None


class TestDatedKeyRegression:
    """Dated parses must keep BYTE-IDENTICAL keys to the pre-t6bin format.

    Stored review rows for dated events must keep matching freshly minted
    keys across the upgrade — no migration, no re-asks for dated slots.
    Exact strings pinned deliberately.
    """

    def test_dated_keys_byte_identical(self):
        pins = {
            CONTESTED_MASTERS[0]:
                "fury vs. usyk|2026-07-12T00:00:00+00:00",
            CONTESTED_MASTERS[1]:
                "fury vs. usyk prelims|2026-07-12T00:00:00+00:00",
            "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET":
                "imsa ctmp qualifying|2026-07-11T19:55:00+00:00",
        }
        for name, expected in pins.items():
            parsed = parse_event_name(name, None, now=NOW)
            assert parsed.matched_pattern not in SYNTHESIZED_DATE_PATTERN_NAMES
            assert master_event_key(parsed) == expected

    def test_dated_key_unchanged_even_when_flag_is_on(self):
        # assume_current_date=True with a FULLY dated name: the dateless
        # variants never ran, so the key stays the dated shape.
        parsed = parse_event_name(
            CONTESTED_MASTERS[0], None, now=NOW, assume_current_date=True,
        )
        assert master_event_key(parsed) \
            == "fury vs. usyk|2026-07-12T00:00:00+00:00"

    def test_dated_keys_distinct_by_date(self):
        a = parse_event_name(
            "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET", None, now=NOW,
        )
        b = parse_event_name(
            "PPV 01: Fury vs. Usyk @ 12 Jul 08:00 PM ET", None, now=NOW,
        )
        assert master_event_key(a) != master_event_key(b)


class TestRecurringDatelessSlotCarryForward:
    """End-to-end: decisions on a recurring dateless slot survive midnight.

    Day 1: the jqwfq rail demotes the stale-suspect dateless stream to
    ambiguous and the pairing is enqueued pending. The operator answers.
    Day 2 (``now`` advanced 24h, identical names): the decision re-applies
    because the fingerprint no longer embeds the synthesized date, and the
    store's dedup treats the day-2 fingerprint as already answered — the
    queue does not refill. Day-boundary behavior is proven HERE with a
    mocked clock (``now``), not in live verification.
    """

    MASTER = "PPV 01: FURY vs HALL 6PM"
    STREAM_NAME = "Boxing 05: FURY vs HALL 6PM"
    DAY2 = NOW + timedelta(days=1)

    def _rule_config(self):
        return _config(
            assume_current_date=True,
            demote_stale_dateless=True,
        )

    def _stream(self):
        return SecondaryStream(
            name=self.STREAM_NAME, group_id=20, stream_id=301,
            provider="PPVProvider", provider_id=7,
            name_seen_before_today=True,
        )

    def _payloads(self, resolved) -> list[dict]:
        """Mirror the executor's enqueue payload construction."""
        shash = stream_name_hash(resolved.stream.name)
        provider_id = (
            resolved.stream.provider_id
            if resolved.stream.provider_id is not None
            else PROVIDER_ID_UNKNOWN
        )
        return [
            {
                "provider_id": provider_id,
                "stream_name_hash": shash,
                "event_key": master_event_key(c.parsed),
                "evidence": {},
            }
            for c in resolved.review_candidates
        ]

    def _day1_enqueue(self, test_session, rule_id: int = 1):
        from models import ChannelPipelineRule
        from services.event_sync_review_store import (
            enqueue_review_candidates,
        )
        rule = ChannelPipelineRule(
            id=rule_id, name="Event Sync", conditions="[]", actions="[]",
        )
        test_session.add(rule)
        test_session.commit()

        resolution = resolve_event_sync(
            self._rule_config(), [self.MASTER], [self._stream()], now=NOW,
        )
        (resolved,) = resolution.resolved
        assert resolved.disposition == DISPOSITION_AMBIGUOUS
        assert resolved.ambiguous_reason \
            == AMBIGUOUS_REASON_STALE_DATELESS_NAME
        payloads = self._payloads(resolved)
        assert len(payloads) == 1
        counts = enqueue_review_candidates(test_session, rule_id, payloads)
        assert counts == {
            "enqueued": 1, "refreshed": 0, "already_answered": 0,
        }
        return payloads

    def _answer(self, test_session, status: str):
        from models import EventSyncReview
        row = test_session.query(EventSyncReview).one()
        row.status = status
        test_session.commit()

    def test_accept_carries_forward_across_the_day_boundary(
        self, test_session
    ):
        from models import EventSyncReview
        from services.event_sync_review_store import (
            enqueue_review_candidates,
            load_review_decisions,
        )
        self._day1_enqueue(test_session)
        self._answer(test_session, "accepted")

        decisions = load_review_decisions(test_session, 1)
        resolution = resolve_event_sync(
            self._rule_config(), [self.MASTER], [self._stream()],
            now=self.DAY2, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        # The accept outranks the rail: attached via the review queue, no
        # re-demote, nothing left to enqueue.
        assert resolved.disposition == DISPOSITION_WOULD_ATTACH
        assert resolved.attach_source == ATTACH_SOURCE_REVIEW_QUEUE
        assert resolved.review_candidates == ()

        # Belt-and-braces: even a direct day-2 enqueue of the same pairing
        # hits the already_answered path — zero new pending rows.
        day2_resolution = resolve_event_sync(
            self._rule_config(), [self.MASTER], [self._stream()],
            now=self.DAY2,
        )
        day2_payloads = self._payloads(day2_resolution.resolved[0])
        counts = enqueue_review_candidates(test_session, 1, day2_payloads)
        assert counts == {
            "enqueued": 0, "refreshed": 0, "already_answered": 1,
        }
        pending = test_session.query(EventSyncReview).filter(
            EventSyncReview.status == "pending"
        ).count()
        assert pending == 0

    def test_reject_carries_forward_across_the_day_boundary(
        self, test_session
    ):
        from models import EventSyncReview
        from services.event_sync_review_store import (
            enqueue_review_candidates,
            load_review_decisions,
        )
        self._day1_enqueue(test_session)
        self._answer(test_session, "rejected")

        decisions = load_review_decisions(test_session, 1)
        resolution = resolve_event_sync(
            self._rule_config(), [self.MASTER], [self._stream()],
            now=self.DAY2, decisions=decisions,
        )
        (resolved,) = resolution.resolved
        # The reject suppresses the lone candidate before classification.
        assert resolved.disposition == DISPOSITION_UNMATCHED
        assert resolved.rejected_suppressed == 1
        assert resolved.review_candidates == ()

        day2_resolution = resolve_event_sync(
            self._rule_config(), [self.MASTER], [self._stream()],
            now=self.DAY2,
        )
        day2_payloads = self._payloads(day2_resolution.resolved[0])
        counts = enqueue_review_candidates(test_session, 1, day2_payloads)
        assert counts == {
            "enqueued": 0, "refreshed": 0, "already_answered": 1,
        }
        pending = test_session.query(EventSyncReview).filter(
            EventSyncReview.status == "pending"
        ).count()
        assert pending == 0
