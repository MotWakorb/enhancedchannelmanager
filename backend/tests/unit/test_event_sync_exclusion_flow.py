"""Event Sync operator-exclusion run integration (bead ti939.3.5).

Pins the bead's acceptance criteria end-to-end through the store, the
executor and the engine attach phase, against a real (in-memory) DB:

* **Store round-trip** — an ``event_sync_exclusions`` row loads back as
  the exact in-memory pairing key the resolver filters on, scoped to its
  rule.
* **Forced-out disposition** — a live run reports an excluded pairing as
  ``excluded_by_operator`` (never ``would_attach``, never enqueued) and
  performs ZERO Dispatcharr writes for it.
* **Churn survival** — re-running with a brand-new stream id (simulated
  provider refresh) keeps the pairing excluded: fingerprints carry no
  stream/channel ids, so this holds by construction (and by this test).
* **Precedence** — an exclusion outranks a prior review-queue ACCEPT for
  the same fingerprint; the two never both apply.
* **Removal restores matching** — deleting the row makes the pairing
  attach again on the next run (the operator's undo).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

import database
from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from models import EventSyncExclusion, EventSyncReview
from services.event_sync_exclusion_store import load_exclusion_keys
from services.event_sync_matcher import parse_event_name
from services.event_sync_review import (
    REVIEW_STATUS_ACCEPTED,
    master_event_key,
    pairing_key,
    stream_name_hash,
)

EXECUTION_ID = 99

MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
MASTER_IMSA = "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET"
STREAM_IMSA_AMBIG = "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET"


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


def _master_channel(cid: int, name: str) -> dict:
    return {
        "id": cid, "name": name, "channel_group_id": 10,
        "auto_created": True, "streams": [],
    }


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _mercury_key(provider_id: int = 1) -> tuple:
    return pairing_key(
        provider_id, STREAM_MERCURY, parse_event_name(MASTER_MERCURY, None)
    )


def _exclusion_row(rule_id: int = 1, provider_id: int = 1,
                   stream_name: str = STREAM_MERCURY,
                   master_name: str = MASTER_MERCURY) -> EventSyncExclusion:
    return EventSyncExclusion(
        rule_id=rule_id,
        provider_id=provider_id,
        stream_name_hash=stream_name_hash(stream_name),
        event_key=master_event_key(parse_event_name(master_name, None)),
        created_at=1_752_800_000_000,
        evidence="{}",
    )


@pytest.fixture()
def db_session_local(test_engine, monkeypatch):
    """Point ``database.get_session()`` at the in-memory test engine and
    seed the rule row the exclusion FK requires (same fixture shape as
    tests/unit/test_event_sync_review_flow.py)."""
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
        db.add(ChannelPipelineRule(
            id=2, name="Other Rule", conditions="[]", actions="[]",
        ))
        db.commit()
    finally:
        db.close()
    return TestSessionLocal


class TestExclusionStoreRoundTrip:
    def test_row_loads_back_as_the_resolver_pairing_key(
        self, db_session_local
    ):
        db = db_session_local()
        try:
            db.add(_exclusion_row())
            db.commit()
            keys = load_exclusion_keys(db, 1)
        finally:
            db.close()
        assert keys == frozenset({_mercury_key()})

    def test_load_is_rule_scoped(self, db_session_local):
        db = db_session_local()
        try:
            db.add(_exclusion_row(rule_id=2))
            db.commit()
            assert load_exclusion_keys(db, 1) == frozenset()
            assert load_exclusion_keys(db, 2) == frozenset({_mercury_key()})
        finally:
            db.close()

    def test_unique_fingerprint_index_blocks_duplicates(
        self, db_session_local
    ):
        from sqlalchemy.exc import IntegrityError

        db = db_session_local()
        try:
            db.add(_exclusion_row())
            db.commit()
            db.add(_exclusion_row())
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        finally:
            db.close()


class TestExecutorExcludedDisposition:
    def _executor(self):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        executor = ActionExecutor(
            client,
            existing_channels=[_master_channel(100, MASTER_MERCURY)],
            existing_groups=[], execution_id=EXECUTION_ID,
        )
        return executor, client

    def _stream(self, stream_id: int = 7001):
        from services.event_sync_resolver import SecondaryStream

        return SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=stream_id,
            provider="ProvB", provider_id=1,
        )

    def test_excluded_pairing_is_counted_not_attached(self):
        executor, client = self._executor()
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), [self._stream()],
            ExecutionContext(dry_run=False),
            exclusions=frozenset({_mercury_key()}),
        ))
        assert summary["excluded_by_operator"] == 1
        assert summary["excluded_suppressed"] == 1
        assert summary["attached"] == 0
        assert summary["unmatched"] == 0
        assert summary["attach_errors"] == 0
        assert summary["review_candidates"] == []
        client.update_channel.assert_not_awaited()

    def test_exclusion_outranks_accept_decision(self):
        # PRECEDENCE: accept + exclusion for the same fingerprint never
        # both apply — the run must NOT attach.
        from services.event_sync_review import ReviewDecisions

        executor, client = self._executor()
        key = _mercury_key()
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), [self._stream()],
            ExecutionContext(dry_run=False),
            decisions=ReviewDecisions(accepted=frozenset({key})),
            exclusions=frozenset({key}),
        ))
        assert summary["excluded_by_operator"] == 1
        assert summary["attached"] == 0
        assert summary["queue_attached"] == 0
        client.update_channel.assert_not_awaited()

    def test_no_exclusions_attaches_as_before(self):
        executor, client = self._executor()
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), [self._stream()],
            ExecutionContext(dry_run=False),
        ))
        assert summary["attached"] == 1
        assert summary["excluded_by_operator"] == 0
        assert summary["excluded_suppressed"] == 0


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


def _run_phase(engine, executor, *, dry_run: bool = False):
    results = _results()
    _run(engine._run_event_sync_rules(
        [_event_rule()], executor, results, dry_run=dry_run,
        triggered_by="manual", channels_touched_ids=set(),
    ))
    return results


class TestEnginePhaseExclusions:
    CHANNELS = None  # set per-test (list is mutated by attach paths)

    def test_exclusion_suppresses_live_run_and_survives_churn(
        self, db_session_local
    ):
        db = db_session_local()
        try:
            db.add(_exclusion_row())
            db.commit()
        finally:
            db.close()

        channels = [_master_channel(100, MASTER_MERCURY)]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _run_phase(engine, executor)
        summary = results["event_sync"][0]

        assert summary["excluded_by_operator"] == 1
        assert summary["attached"] == 0
        assert "1 excluded by operator" in summary["summary_line"]
        client.update_channel.assert_not_awaited()

        # Simulated provider refresh: SAME provider string + event
        # identity, brand-new stream id — still excluded (the acceptance
        # criterion: survives refreshes and stream-ID churn).
        batch2 = [{"id": 424242, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine2, executor2, client2 = _engine_with_client(channels, batch2)
        summary2 = _run_phase(engine2, executor2)["event_sync"][0]
        assert summary2["excluded_by_operator"] == 1
        assert summary2["attached"] == 0
        client2.update_channel.assert_not_awaited()

    def test_excluded_pairing_never_enqueues_for_review(
        self, db_session_local
    ):
        # An ambiguous-band pairing under exclusion must not re-enter the
        # review queue — the standing order already answered it.
        db = db_session_local()
        try:
            db.add(_exclusion_row(
                stream_name=STREAM_IMSA_AMBIG, master_name=MASTER_IMSA,
            ))
            db.commit()
        finally:
            db.close()

        channels = [_master_channel(101, MASTER_IMSA)]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        engine, executor, _client = _engine_with_client(channels, batch)
        summary = _run_phase(engine, executor)["event_sync"][0]

        assert summary["excluded_by_operator"] == 1
        assert summary["ambiguous_skipped"] == 0
        assert summary["review_enqueued"] == 0
        db = db_session_local()
        try:
            assert db.query(EventSyncReview).count() == 0
        finally:
            db.close()

    def test_exclusion_outranks_stored_accept(self, db_session_local):
        # A stored ACCEPT decision and an exclusion for the same
        # fingerprint: the run suppresses (exclusion wins).
        db = db_session_local()
        try:
            db.add(EventSyncReview(
                rule_id=1, provider_id=1,
                stream_name_hash=stream_name_hash(STREAM_MERCURY),
                event_key=master_event_key(
                    parse_event_name(MASTER_MERCURY, None)
                ),
                status=REVIEW_STATUS_ACCEPTED,
                created_at=1, last_seen_at=1, evidence="{}",
            ))
            db.add(_exclusion_row())
            db.commit()
        finally:
            db.close()

        channels = [_master_channel(100, MASTER_MERCURY)]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        summary = _run_phase(engine, executor)["event_sync"][0]

        assert summary["excluded_by_operator"] == 1
        assert summary["attached"] == 0
        assert summary["queue_attached"] == 0
        client.update_channel.assert_not_awaited()

    def test_removal_restores_matching(self, db_session_local):
        # DELETE is the undo: with the row gone the pairing attaches again
        # on the next run (nothing is remembered outside the table).
        db = db_session_local()
        try:
            db.add(_exclusion_row())
            db.commit()
        finally:
            db.close()

        channels = [_master_channel(100, MASTER_MERCURY)]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        summary = _run_phase(engine, executor)["event_sync"][0]
        assert summary["excluded_by_operator"] == 1
        client.update_channel.assert_not_awaited()

        db = db_session_local()
        try:
            db.query(EventSyncExclusion).delete()
            db.commit()
        finally:
            db.close()

        engine2, executor2, client2 = _engine_with_client(channels, batch)
        summary2 = _run_phase(engine2, executor2)["event_sync"][0]
        assert summary2["excluded_by_operator"] == 0
        assert summary2["attached"] == 1
        client2.update_channel.assert_awaited_once_with(
            100, {"streams": [7001]}
        )
