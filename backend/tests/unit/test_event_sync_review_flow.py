"""Event Sync review-queue run integration (bead ti939.3.2).

Pins the bead's acceptance criteria end-to-end through the executor + the
engine attach phase + the store, against a real (in-memory) DB:

* **Enqueue instead of silently skipping** — ambiguous-band matches
  (band AND contested) from a live run land as ``pending``
  ``event_sync_reviews`` rows, fingerprint-keyed; the payloads' identity
  fields carry NO channel/stream IDs (those live only inside the
  display-only evidence snapshot).
* **Decisions persist across simulated refreshes** — a refresh is
  simulated by re-running with brand-new stream ids; an accepted pairing
  auto-attaches (``queue_attached``), a rejected pairing stays suppressed.
* **The queue does not duplicate answered (or pending) items** — re-runs
  refresh the existing row; accepted/rejected rows never re-enter.
* **Journal distinction** — queue-driven attaches carry
  ``attach_source="review_queue"`` in their provenance; threshold attaches
  carry ``"threshold"``.
* **Dry runs enqueue nothing** (they report ``review_would_enqueue``).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

import database
from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from models import EventSyncReview
from services.event_sync_resolver import SecondaryStream
from services.event_sync_review import (
    ATTACH_SOURCE_REVIEW_QUEUE,
    ATTACH_SOURCE_THRESHOLD,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_REJECTED,
    ReviewDecisions,
    stream_name_hash,
)

EXECUTION_ID = 88

MASTER_IMSA = "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET"
STREAM_IMSA_AMBIG = "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET"
MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
CONTESTED_MASTER_A = "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET"
CONTESTED_MASTER_B = "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET"
CONTESTED_STREAM = "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET"


def _config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "max_attach_per_run": 100,
        "enabled": True,
    }
    config.update(overrides)
    return config


def _master_channel(cid: int, name: str, *, streams: list | None = None) -> dict:
    return {
        "id": cid, "name": name, "channel_group_id": 10,
        "auto_created": True, "streams": list(streams or []),
    }


def _executor(channels: list):
    client = MagicMock()
    client.update_channel = AsyncMock(return_value={})
    executor = ActionExecutor(
        client, existing_channels=channels, existing_groups=[],
        execution_id=EXECUTION_ID,
    )
    return executor, client


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExecutorReviewSurface:
    """execute_event_sync_rule surfaces fingerprint-keyed payloads and
    honors decisions threaded in by the engine."""

    def test_ambiguous_stream_surfaces_fingerprint_payloads(self):
        executor, client = _executor([_master_channel(101, MASTER_IMSA)])
        streams = [SecondaryStream(
            name=STREAM_IMSA_AMBIG, group_id=20, stream_id=7002,
            provider="ProvB", provider_id=1,
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams,
            ExecutionContext(dry_run=False),
        ))

        assert summary["ambiguous_skipped"] == 1
        payloads = summary["review_candidates"]
        assert len(payloads) == 1
        p = payloads[0]
        # SECURITY: identity fields are content fingerprints, never ids.
        assert set(p) == {"provider_id", "stream_name_hash", "event_key",
                          "evidence"}
        assert p["provider_id"] == 1
        assert p["stream_name_hash"] == stream_name_hash(STREAM_IMSA_AMBIG)
        assert "|" in p["event_key"]  # "<cleaned title>|<UTC ISO start>"
        # Evidence is the display snapshot: raw names + parsed identities +
        # per-candidate verdicts + SNAPSHOT ids.
        ev = p["evidence"]
        assert ev["stream_name"] == STREAM_IMSA_AMBIG
        assert ev["master_channel_name"] == MASTER_IMSA
        assert ev["master_channel_id"] == 101
        assert ev["stream_id"] == 7002
        assert ev["band"] == "ambiguous"
        assert ev["ambiguous_reason"] == "top_candidate_ambiguous_band"
        assert "score" in ev and "team_verdict" in ev \
            and "time_delta_minutes" in ev

    def test_contested_stream_surfaces_one_payload_per_pairing(self):
        executor, _client = _executor([
            _master_channel(100, CONTESTED_MASTER_A),
            _master_channel(101, CONTESTED_MASTER_B),
        ])
        streams = [SecondaryStream(
            name=CONTESTED_STREAM, group_id=20, stream_id=7001,
            provider="ProvB", provider_id=1,
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams,
            ExecutionContext(dry_run=False),
        ))
        assert summary["contested_skipped"] == 1
        payloads = summary["review_candidates"]
        assert len(payloads) == 2
        assert len({p["event_key"] for p in payloads}) == 2
        assert {p["evidence"]["master_channel_name"] for p in payloads} \
            == {CONTESTED_MASTER_A, CONTESTED_MASTER_B}

    def test_accepted_decision_attaches_with_queue_provenance(self):
        from services.event_sync_matcher import parse_event_name
        from services.event_sync_review import pairing_key

        executor, client = _executor([_master_channel(101, MASTER_IMSA)])
        decisions = ReviewDecisions(accepted=frozenset({
            pairing_key(1, STREAM_IMSA_AMBIG,
                        parse_event_name(MASTER_IMSA, None)),
        }))
        streams = [SecondaryStream(
            name=STREAM_IMSA_AMBIG, group_id=20, stream_id=7002,
            provider="ProvB", provider_id=1,
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams,
            ExecutionContext(dry_run=False), decisions=decisions,
        ))

        assert summary["attached"] == 1
        assert summary["queue_attached"] == 1
        assert summary["ambiguous_skipped"] == 0
        assert summary["review_candidates"] == []
        client.update_channel.assert_awaited_once_with(
            101, {"streams": [7002]}
        )
        # Journal-distinction contract: queue-driven attach provenance.
        match = summary["attach_entries"][0]["match"]
        assert match["attach_source"] == ATTACH_SOURCE_REVIEW_QUEUE

    def test_threshold_attach_carries_threshold_provenance(self):
        executor, _client = _executor(
            [_master_channel(100, MASTER_MERCURY)]
        )
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001,
            provider="ProvB", provider_id=1,
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams,
            ExecutionContext(dry_run=False),
        ))
        assert summary["attached"] == 1
        assert summary["queue_attached"] == 0
        match = summary["attach_entries"][0]["match"]
        assert match["attach_source"] == ATTACH_SOURCE_THRESHOLD

    def test_rejected_decision_suppresses_pairing(self):
        from services.event_sync_matcher import parse_event_name
        from services.event_sync_review import pairing_key

        executor, client = _executor([_master_channel(101, MASTER_IMSA)])
        decisions = ReviewDecisions(rejected=frozenset({
            pairing_key(1, STREAM_IMSA_AMBIG,
                        parse_event_name(MASTER_IMSA, None)),
        }))
        streams = [SecondaryStream(
            name=STREAM_IMSA_AMBIG, group_id=20, stream_id=7002,
            provider="ProvB", provider_id=1,
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams,
            ExecutionContext(dry_run=False), decisions=decisions,
        ))
        assert summary["attached"] == 0
        assert summary["ambiguous_skipped"] == 0
        assert summary["unmatched"] == 1
        assert summary["rejected_suppressed"] == 1
        assert summary["review_candidates"] == []
        client.update_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# Engine phase + real store (in-memory DB): the acceptance criteria.
# ---------------------------------------------------------------------------


def _event_rule(rid: int = 1, config: dict | None = None) -> MagicMock:
    rule = MagicMock(id=rid)
    rule.name = "Event Rule"
    rule.get_event_sync_config.return_value = (
        _config() if config is None else config
    )
    return rule


def _results() -> dict:
    return {
        "streams_merged": 0,
        "streams_skipped": 0,
        "modified_entities": [],
        "execution_log": [],
        "dry_run_results": [],
        "rule_match_counts": {},
    }


def _engine_with_client(channels: list, secondary_batch: list):
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 1, "name": "ProvB"}]
    )
    client._channel_group_name_for_id = AsyncMock(return_value="EVENTS B")
    client.get_streams = AsyncMock(return_value={
        "count": len(secondary_batch), "results": secondary_batch,
        "next": None,
    })
    client.update_channel = AsyncMock(return_value={})
    engine = ChannelPipelineEngine(client)
    executor = ActionExecutor(
        client, existing_channels=channels, existing_groups=[],
        execution_id=EXECUTION_ID,
    )
    return engine, executor, client


@pytest.fixture()
def db_session_local(test_engine, monkeypatch):
    """Point ``database.get_session()`` at the in-memory test engine so the
    engine phase's store calls (decision load + enqueue) hit a real DB.

    Also seeds the ``auto_creation_rules`` row the review rows FK onto
    (SQLite FK enforcement is ON via the global connect listener)."""
    from models import ChannelPipelineRule

    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    db = TestSessionLocal()
    try:
        db.add(ChannelPipelineRule(
            id=1, name="Event Rule", conditions="[]", actions="[]",
        ))
        db.commit()
    finally:
        db.close()
    return TestSessionLocal


def _run_phase(engine, executor, *, dry_run: bool = False):
    results = _results()
    _run(engine._run_event_sync_rules(
        [_event_rule()], executor, results, dry_run=dry_run,
        triggered_by="manual", channels_touched_ids=set(),
    ))
    return results


class TestEnginePhaseQueuePersistence:
    def test_live_run_enqueues_and_rerun_does_not_duplicate(
        self, db_session_local
    ):
        channels = [_master_channel(101, MASTER_IMSA)]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine, executor, _client = _engine_with_client(channels, batch)

        results = _run_phase(engine, executor)
        summary = results["event_sync"][0]
        assert summary["review_enqueued"] == 1
        assert summary["review_refreshed"] == 0
        assert "1 queued for review" in summary["summary_line"]

        db = db_session_local()
        try:
            rows = db.query(EventSyncReview).all()
            assert len(rows) == 1
            assert rows[0].status == "pending"
            assert rows[0].provider_id == 1
            assert rows[0].stream_name_hash \
                == stream_name_hash(STREAM_IMSA_AMBIG)
            first_seen = rows[0].last_seen_at
        finally:
            db.close()

        # Simulated refresh: SAME provider string, brand-new stream id.
        batch2 = [{"id": 9999, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine2, executor2, _c2 = _engine_with_client(channels, batch2)
        results2 = _run_phase(engine2, executor2)
        summary2 = results2["event_sync"][0]
        assert summary2["review_enqueued"] == 0
        assert summary2["review_refreshed"] == 1

        db = db_session_local()
        try:
            rows = db.query(EventSyncReview).all()
            assert len(rows) == 1  # refreshed, never duplicated
            assert rows[0].last_seen_at >= first_seen
        finally:
            db.close()

    def test_accepted_decision_survives_refresh_and_auto_attaches(
        self, db_session_local
    ):
        channels = [_master_channel(101, MASTER_IMSA)]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine, executor, _client = _engine_with_client(channels, batch)
        _run_phase(engine, executor)

        # Operator accepts (state flip — the router's semantics).
        db = db_session_local()
        try:
            row = db.query(EventSyncReview).one()
            row.status = REVIEW_STATUS_ACCEPTED
            row.resolved_at = row.last_seen_at
            row.resolution_source = "operator"
            db.commit()
        finally:
            db.close()

        # Simulated refresh: new stream id. The fingerprint-keyed decision
        # re-applies — the stream attaches, nothing re-enqueues.
        batch2 = [{"id": 8888, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine2, executor2, client2 = _engine_with_client(channels, batch2)
        results2 = _run_phase(engine2, executor2)
        summary2 = results2["event_sync"][0]

        assert summary2["attached"] == 1
        assert summary2["queue_attached"] == 1
        assert summary2["ambiguous_skipped"] == 0
        assert summary2["review_enqueued"] == 0
        assert "1 via review queue" in summary2["summary_line"]
        client2.update_channel.assert_awaited_once_with(
            101, {"streams": [8888]}
        )

        db = db_session_local()
        try:
            assert db.query(EventSyncReview).count() == 1  # no refill
        finally:
            db.close()

    def test_rejected_decision_suppresses_without_reasking(
        self, db_session_local
    ):
        channels = [_master_channel(101, MASTER_IMSA)]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine, executor, _client = _engine_with_client(channels, batch)
        _run_phase(engine, executor)

        db = db_session_local()
        try:
            row = db.query(EventSyncReview).one()
            row.status = REVIEW_STATUS_REJECTED
            row.resolved_at = row.last_seen_at
            row.resolution_source = "operator"
            db.commit()
        finally:
            db.close()

        batch2 = [{"id": 8888, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine2, executor2, client2 = _engine_with_client(channels, batch2)
        results2 = _run_phase(engine2, executor2)
        summary2 = results2["event_sync"][0]

        assert summary2["attached"] == 0
        assert summary2["unmatched"] == 1
        assert summary2["rejected_suppressed"] == 1
        assert summary2["review_enqueued"] == 0
        client2.update_channel.assert_not_awaited()

        db = db_session_local()
        try:
            assert db.query(EventSyncReview).count() == 1  # never re-asked
        finally:
            db.close()

    def test_dry_run_enqueues_nothing(self, db_session_local):
        channels = [_master_channel(101, MASTER_IMSA)]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine, executor, _client = _engine_with_client(channels, batch)

        results = _run_phase(engine, executor, dry_run=True)
        summary = results["event_sync"][0]
        assert summary["review_enqueued"] == 0
        assert summary["review_would_enqueue"] == 1
        assert "1 would queue for review" in summary["summary_line"]

        db = db_session_local()
        try:
            assert db.query(EventSyncReview).count() == 0
        finally:
            db.close()
