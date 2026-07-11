"""
Frozen event-name regression corpus gate (bead enhancedchannelmanager-ti939.1.2).

Runs every labeled pair in ``tests/fixtures/event_sync/matcher_corpus.jsonl``
through ``services.event_sync_matcher.score_pair`` and fails the pytest run
(and therefore CI / merge to dev) when the matcher's band assignments violate
the per-label contract or the aggregate precision/recall floors. Offline —
no network, no Dispatcharr, sub-second.

CORPUS FORMAT (the JSONL file cannot carry comments, so its contract is
documented here):

    One JSON object per line:
        {"name_a": ..., "name_b": ..., "label": ..., "reason": ...}

    * ``name_a`` / ``name_b`` — FULL raw provider strings, including slot
      prefixes ("Peacock 14:", "Fubo Sports Network 07 :") and time
      suffixes ("@ 11 Jul 06:00 PM ET", "@ Jan 17 02:45 PM ET"), so every
      pair exercises parse -> time-block -> score end to end.
    * ``label`` — ground truth, one of:
        - ``same_event``  the two names denote the SAME real-world event
                          (expected band: attach). A pair whose reason is
                          marked "KNOWN RECALL MISS" is a documented matcher
                          limitation: still labeled with the truth, counted
                          against the recall floor rather than asserted.
        - ``not_same``    clearly different events (expected band: reject —
                          asserted per pair; a not_same pair reaching the
                          attach band is an incident-class false positive).
        - ``ambiguous``   a human reviewer could not confidently decide from
                          the names alone (expected band: ambiguous — the
                          operator-review band; never auto-attach).
    * ``reason`` — why the pair is in the corpus / what trap it encodes.

ADD-ONLY POLICY: the corpus is frozen and append-only. Add one pair for
every matcher bug ever found (with the bug's bead ID in ``reason``); NEVER
delete or relabel an existing pair to make the gate pass. If a matcher
change flips an existing pair's band, that is the gate doing its job —
justify the behavior change in review or fix the matcher.

PROVENANCE: seeded 2026-07-11 from the live ECM instance's event-style
groups ("Peacock NN:", "Fubo Sports Network NN :", "NFL Game Pass NN:"
slot-stream names captured via search_streams), with engineered
counterpart names (second-provider spellings) and engineered trap pairs
covering the shapes behind the 1,341-false-positive merge incident. Pairs
whose ``reason`` says "live-captured" carry at least one verbatim live
name.

GATE ASSERTIONS:
    1. Per-label band correctness:
       * every ``not_same`` pair must band ``reject`` (hard, per pair);
       * every ``ambiguous`` pair must band ``ambiguous`` (hard, per pair);
       * ``same_event`` pairs are governed by the recall floor below
         (documented known misses are allowed to fall short of attach).
    2. Attach-band precision >= 0.97 (share of attach-banded pairs whose
       label is same_event).
    3. Attach-band recall >= 0.85 (share of same_event pairs that band
       attach).
    4. Offline and fast: the full corpus must score in under 5 seconds.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
import pytz

from services.event_sync_matcher import (
    BAND_AMBIGUOUS,
    BAND_ATTACH,
    BAND_REJECT,
    DEFAULT_EVENT_TIMEZONE,
    score_pair,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "event_sync" / "matcher_corpus.jsonl"

PRECISION_FLOOR = 0.97
RECALL_FLOOR = 0.85

VALID_LABELS = {"same_event", "not_same", "ambiguous"}

# Deterministic "now" anchoring year inference for the corpus's yearless
# date strings: the live-capture date. Frozen alongside the corpus — do not
# move it without re-validating every pair.
_CORPUS_NOW = pytz.timezone(DEFAULT_EVENT_TIMEZONE).localize(
    datetime(2026, 7, 11, 12, 0, 0)
)


def _load_corpus() -> list[dict]:
    pairs = []
    with CORPUS_PATH.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            entry["_line"] = line_no
            pairs.append(entry)
    return pairs


def _score_corpus(pairs: list[dict]) -> list[tuple[dict, str]]:
    """Score every pair once; returns (entry, band) tuples."""
    return [
        (entry, score_pair(entry["name_a"], entry["name_b"], now=_CORPUS_NOW).band)
        for entry in pairs
    ]


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    pairs = _load_corpus()
    assert 30 <= len(pairs) <= 200, (
        f"corpus has {len(pairs)} pairs; expected the seeded 30-50 "
        "(add-only growth beyond 50 is fine, shrinking is not)"
    )
    return pairs


@pytest.fixture(scope="module")
def scored(corpus) -> list[tuple[dict, str]]:
    return _score_corpus(corpus)


class TestCorpusIntegrity:
    def test_every_line_is_well_formed(self, corpus):
        for entry in corpus:
            missing = {"name_a", "name_b", "label", "reason"} - entry.keys()
            assert not missing, f"line {entry['_line']}: missing fields {missing}"
            assert entry["label"] in VALID_LABELS, (
                f"line {entry['_line']}: bad label {entry['label']!r}"
            )
            assert entry["name_a"].strip() and entry["name_b"].strip()
            assert entry["reason"].strip(), f"line {entry['_line']}: empty reason"

    def test_no_duplicate_pairs(self, corpus):
        seen: dict[tuple[str, str], int] = {}
        for entry in corpus:
            key = (entry["name_a"], entry["name_b"])
            assert key not in seen, (
                f"line {entry['_line']} duplicates line {seen[key]}: {key}"
            )
            seen[key] = entry["_line"]

    def test_all_labels_are_represented(self, corpus):
        labels = {entry["label"] for entry in corpus}
        assert labels == VALID_LABELS


class TestPerLabelBandCorrectness:
    def test_not_same_pairs_never_reach_attach_or_ambiguous(self, scored):
        # A not_same pair in the ATTACH band is an incident-class false
        # positive; in the AMBIGUOUS band it wastes operator review. Both
        # are per-pair hard failures.
        violations = [
            f"line {entry['_line']}: banded {band!r} — {entry['name_a']!r} vs "
            f"{entry['name_b']!r} ({entry['reason']})"
            for entry, band in scored
            if entry["label"] == "not_same" and band != BAND_REJECT
        ]
        assert not violations, "not_same pairs escaped the reject band:\n" + "\n".join(violations)

    def test_ambiguous_pairs_band_ambiguous(self, scored):
        violations = [
            f"line {entry['_line']}: banded {band!r} — {entry['name_a']!r} vs "
            f"{entry['name_b']!r} ({entry['reason']})"
            for entry, band in scored
            if entry["label"] == "ambiguous" and band != BAND_AMBIGUOUS
        ]
        assert not violations, "ambiguous pairs left the review band:\n" + "\n".join(violations)

    def test_same_event_pairs_never_hard_conflict_unless_documented(self, scored):
        # A same_event pair may miss attach (recall floor governs), but an
        # UNDOCUMENTED miss is a silent regression: every miss must carry
        # the "KNOWN RECALL MISS" marker in its reason.
        violations = [
            f"line {entry['_line']}: banded {band!r} without KNOWN RECALL MISS marker — "
            f"{entry['name_a']!r} vs {entry['name_b']!r}"
            for entry, band in scored
            if entry["label"] == "same_event"
            and band != BAND_ATTACH
            and "KNOWN RECALL MISS" not in entry["reason"]
        ]
        assert not violations, (
            "same_event pairs regressed out of the attach band:\n" + "\n".join(violations)
        )


class TestAggregateFloors:
    def test_attach_band_precision_and_recall_floors(self, scored):
        attach = [(entry, band) for entry, band in scored if band == BAND_ATTACH]
        same_event = [entry for entry, _ in scored if entry["label"] == "same_event"]
        true_positives = [entry for entry, _ in attach if entry["label"] == "same_event"]

        assert attach, "no pair reached the attach band — matcher is inert"
        precision = len(true_positives) / len(attach)
        recall = len(true_positives) / len(same_event)

        false_positives = [
            f"line {entry['_line']}: {entry['label']} banded attach — "
            f"{entry['name_a']!r} vs {entry['name_b']!r}"
            for entry, _ in attach
            if entry["label"] != "same_event"
        ]
        assert precision >= PRECISION_FLOOR, (
            f"attach-band precision {precision:.4f} < {PRECISION_FLOOR} "
            f"({len(true_positives)}/{len(attach)}). False positives:\n"
            + "\n".join(false_positives)
        )
        assert recall >= RECALL_FLOOR, (
            f"attach-band recall {recall:.4f} < {RECALL_FLOOR} "
            f"({len(true_positives)}/{len(same_event)})"
        )


class TestGatePerformance:
    def test_corpus_scores_offline_in_under_five_seconds(self, corpus):
        started = time.monotonic()
        _score_corpus(corpus)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0, f"corpus gate took {elapsed:.2f}s (budget 5s)"
