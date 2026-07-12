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

from datetime import datetime

import pytz

from services.event_sync_matcher import (
    BAND_ATTACH,
    DEFAULT_EVENT_TIMEZONE,
    parse_event_name,
)
from services.event_sync_resolver import (
    AMBIGUOUS_REASON_CONTESTED,
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
