"""Event Sync Phase 1B attach execution (bead enhancedchannelmanager-ti939.2.1).

Pins the first WRITE path for event_sync:

* **Dry-run parity by construction** — the executor resolves through
  ``services.event_sync_resolver.resolve_event_sync`` (the SAME function the
  preview endpoint calls); the attach set equals the resolver's
  ``would_attach`` set on identical inputs.
* **Band semantics** — attach band attaches; ambiguous (band or contested
  rail) skips + counts; reject/unmatched/parse-failed skip with reason.
* **Idempotency** — a stream already on the master channel is a no-op skip
  (stateless re-runs after every refresh are safe) and never consumes attach
  cap budget.
* **Per-run attach cap** — on overage the executor stops attaching, WARNs,
  and records the overage count; the engine surfaces a run warning + log
  entry.
* **Journal provenance** — one entry per attach: category "event_sync",
  batch_id = execution id, NAMES alongside IDs (secondary stream, provider,
  master channel), score, band, time delta, team-token verdict.
* **Run summary line** — "event_sync: X attached, Y ambiguous skipped, Z
  unmatched, W parse failures" in the execution log and on the structured
  results.
* **Manual-run-only** — the attach phase refuses unattended triggers
  (defense in depth behind run_pipeline's gate), and the watermark task's
  rule query excludes event_sync rules outright.
* **Never** creates/deletes channels or populates managed_channel_ids.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from models import (
    ChannelPipelineExecution,
    ChannelPipelineRule,
    ChannelPipelineSnapshot,
)
from services.event_sync_resolver import SecondaryStream

EXECUTION_ID = 77

# Names follow the resolver-test corpus: masters carry a slot prefix; the
# secondary provider spells the same fixture differently. Year inference uses
# the same "now" for masters and streams inside one resolution, so these are
# stable regardless of wall clock.
MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
MASTER_IMSA = "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
# Corpus-proven ambiguous pairing for MASTER_IMSA (same venue + slot,
# different session).
STREAM_IMSA_AMBIG = "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET"
STREAM_NO_EVENT = "Fubo Sports Network 07 : NO EVENT"
STREAM_UNMATCHED = "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET"


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


def _master_channel(cid: int, name: str, *, group_id: int = 10,
                    streams: list | None = None) -> dict:
    return {
        "id": cid,
        "name": name,
        "channel_group_id": group_id,
        "auto_created": True,
        "streams": list(streams or []),
    }


def _executor(channels: list, execution_id: int | None = EXECUTION_ID):
    client = MagicMock()
    client.update_channel = AsyncMock(return_value={})
    executor = ActionExecutor(
        client, existing_channels=channels, existing_groups=[],
        execution_id=execution_id,
    )
    return executor, client


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExecutorBandSemantics:
    """execute_event_sync_rule honors the resolver's band decisions."""

    def test_all_four_dispositions(self):
        channels = [
            _master_channel(100, MASTER_MERCURY, streams=[9001]),
            _master_channel(101, MASTER_IMSA, streams=[9002]),
        ]
        executor, client = _executor(channels)
        streams = [
            SecondaryStream(name=STREAM_MERCURY, group_id=20,
                            stream_id=7001, provider="ProvB"),
            SecondaryStream(name=STREAM_IMSA_AMBIG, group_id=20,
                            stream_id=7002, provider="ProvB"),
            SecondaryStream(name=STREAM_UNMATCHED, group_id=20,
                            stream_id=7003, provider="ProvB"),
            SecondaryStream(name=STREAM_NO_EVENT, group_id=20,
                            stream_id=7004, provider="ProvB"),
        ]
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, exec_ctx
        ))

        assert summary["attached"] == 1
        assert summary["ambiguous_skipped"] == 1
        assert summary["unmatched"] == 1
        assert summary["parse_failed"] == 1
        assert summary["attach_errors"] == 0
        assert summary["capped"] is False

        # The one attach hit the Mercury master with the stream appended.
        client.update_channel.assert_awaited_once_with(
            100, {"streams": [9001, 7001]}
        )
        # Merge plumbing rode the shared chokepoint.
        assert exec_ctx.streams_merged == 1
        assert exec_ctx.merged_channel_ids == {100}
        # NOTHING was created or deleted — merge internals only.
        assert exec_ctx.created_entities == []

    def test_contested_tie_is_skipped_not_attached(self):
        """The PR #613 scenario end-to-end through the executor: two
        same-fixture masters tie in the attach band — no attach happens."""
        channels = [
            _master_channel(100, "PPV 01: Fury vs. Usyk @ 11 Jul 08:00 PM ET"),
            _master_channel(101, "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET"),
        ]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name="BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            group_id=20, stream_id=7001, provider="ProvB",
        )]
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, exec_ctx
        ))

        assert summary["attached"] == 0
        assert summary["ambiguous_skipped"] == 1
        assert summary["contested_skipped"] == 1
        client.update_channel.assert_not_awaited()

    def test_dry_run_parity_with_resolver(self):
        """The executor's attach set == resolve_event_sync's would_attach
        set on identical inputs — parity is structural, not incidental."""
        from services.event_sync_resolver import (
            DISPOSITION_WOULD_ATTACH,
            resolve_event_sync,
        )

        channels = [
            _master_channel(100, MASTER_MERCURY),
            _master_channel(101, MASTER_IMSA),
        ]
        streams = [
            SecondaryStream(name=STREAM_MERCURY, group_id=20,
                            stream_id=7001, provider="ProvB"),
            SecondaryStream(name=STREAM_IMSA_AMBIG, group_id=20,
                            stream_id=7002, provider="ProvB"),
            SecondaryStream(name=STREAM_NO_EVENT, group_id=20,
                            stream_id=7003, provider="ProvB"),
        ]
        resolution = resolve_event_sync(
            _config(), [MASTER_MERCURY, MASTER_IMSA], streams
        )
        expected_attach_ids = {
            r.stream.stream_id for r in resolution.resolved
            if r.disposition == DISPOSITION_WOULD_ATTACH
        }

        executor, client = _executor(channels)
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, exec_ctx
        ))

        attached_ids = {
            call.args[1]["streams"][-1]
            for call in client.update_channel.await_args_list
        }
        assert attached_ids == expected_attach_ids
        assert summary["attached"] == len(expected_attach_ids)


class TestExecutorIdempotency:
    def test_already_attached_stream_is_noop(self):
        channels = [_master_channel(100, MASTER_MERCURY, streams=[7001])]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, exec_ctx
        ))

        assert summary["attached"] == 0
        assert summary["already_attached"] == 1
        client.update_channel.assert_not_awaited()
        # No journal entry for a no-op.
        assert executor._journal_buffer == []

    def test_rerun_after_attach_is_noop(self):
        """Stateless re-run: first run attaches, second run no-ops."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[])]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        first = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, ExecutionContext(dry_run=False)
        ))
        assert first["attached"] == 1
        # The executor updated its cached channel dict; a second run sees the
        # stream present (mirrors a fresh fetch after the live PATCH).
        second = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, ExecutionContext(dry_run=False)
        ))
        assert second["attached"] == 0
        assert second["already_attached"] == 1
        client.update_channel.assert_awaited_once()


class TestExecutorAttachCap:
    def _three_fixture_setup(self):
        masters = [
            "Peacock 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            "Peacock 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
            "Peacock 03: Fever vs. Wings @ 11 Jul 09:00 PM ET",
        ]
        channels = [
            _master_channel(100 + i, name) for i, name in enumerate(masters)
        ]
        streams = [
            SecondaryStream(name="TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                            group_id=20, stream_id=7001, provider="ProvB"),
            SecondaryStream(name="TV 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
                            group_id=20, stream_id=7002, provider="ProvB"),
            SecondaryStream(name="TV 03: Fever vs. Wings @ 11 Jul 09:00 PM ET",
                            group_id=20, stream_id=7003, provider="ProvB"),
        ]
        return channels, streams

    def test_cap_overage_stops_attaching_and_records_count(self):
        channels, streams = self._three_fixture_setup()
        executor, client = _executor(channels)
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(max_attach_per_run=2), streams, exec_ctx
        ))

        assert summary["attached"] == 2
        assert summary["capped"] is True
        assert summary["cap_overage"] == 1
        assert summary["cap"] == 2
        assert client.update_channel.await_count == 2

    def test_already_attached_noops_do_not_consume_cap(self):
        channels, streams = self._three_fixture_setup()
        # First two fixtures already attached — only the third needs work.
        channels[0]["streams"] = [7001]
        channels[1]["streams"] = [7002]
        executor, client = _executor(channels)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(max_attach_per_run=1), streams,
            ExecutionContext(dry_run=False),
        ))

        assert summary["already_attached"] == 2
        assert summary["attached"] == 1
        assert summary["capped"] is False
        assert summary["cap_overage"] == 0

    def test_already_attached_after_cap_trip_is_not_counted_as_overage(self):
        """An already-attached stream encountered AFTER the cap tripped is
        still a no-op, not overage — a capped re-run must not misreport its
        own prior work as deferred."""
        channels, streams = self._three_fixture_setup()
        # The LAST fixture (resolver orders by name: TV 01, TV 02, TV 03) is
        # already attached; cap=1 trips on the second.
        channels[2]["streams"] = [7003]
        executor, client = _executor(channels)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(max_attach_per_run=1), streams,
            ExecutionContext(dry_run=False),
        ))

        assert summary["attached"] == 1
        assert summary["already_attached"] == 1
        assert summary["capped"] is True
        assert summary["cap_overage"] == 1  # only the genuinely deferred one

    def test_cap_not_applied_in_dry_run(self):
        """A dry run reports the true would-attach size (mirrors the
        created-channel cap precedent: cap active only on live runs)."""
        channels, streams = self._three_fixture_setup()
        executor, client = _executor(channels)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(max_attach_per_run=1), streams,
            ExecutionContext(dry_run=True),
        ))

        assert summary["attached"] == 3
        assert summary["capped"] is False
        client.update_channel.assert_not_awaited()


class TestExecutorJournalProvenance:
    def test_journal_entry_per_attach_is_name_carrying(self):
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        _run(executor.execute_event_sync_rule(
            5, "Event Rule", _config(), streams, ExecutionContext(dry_run=False)
        ))

        assert len(executor._journal_buffer) == 1
        entry = executor._journal_buffer[0]
        assert entry["category"] == "event_sync"
        # action_type stays merge_stream so the journal-driven surgical
        # unmerge (legacy rollback) covers event_sync attaches.
        assert entry["action_type"] == "merge_stream"
        assert entry["batch_id"] == str(EXECUTION_ID)
        assert entry["entity_id"] == 100
        assert entry["entity_name"] == MASTER_MERCURY
        assert entry["before_value"] == {"stream_ids": [9001]}
        assert entry["after_value"]["stream_ids"] == [9001, 7001]
        # NAMES alongside IDs (ADR-008 §D4 lazy resolution precedent).
        match = entry["after_value"]["match"]
        assert match["kind"] == "event_sync"
        assert match["rule_id"] == 5
        assert match["secondary_stream_id"] == 7001
        assert match["secondary_stream_name"] == STREAM_MERCURY
        assert match["provider"] == "ProvB"
        assert match["master_channel_id"] == 100
        assert match["master_channel_name"] == MASTER_MERCURY
        assert match["band"] == "attach"
        assert match["team_verdict"] == "agree"
        assert 0.80 <= match["score"] <= 1.0
        assert match["time_delta_minutes"] == 0.0
        # The description carries the stream NAME, not just its id.
        assert STREAM_MERCURY in entry["description"]

    def test_dry_run_writes_no_journal(self):
        channels = [_master_channel(100, MASTER_MERCURY)]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, ExecutionContext(dry_run=True)
        ))
        assert summary["attached"] == 1  # would-attach
        assert executor._journal_buffer == []
        client.update_channel.assert_not_awaited()


# =============================================================================
# Engine attach phase (_run_event_sync_rules)
# =============================================================================


def _event_rule(rid: int = 1, name: str = "Event Rule",
                config: dict | None = None) -> MagicMock:
    rule = MagicMock(id=rid)
    rule.name = name
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
    client.get_m3u_accounts = AsyncMock(return_value=[{"id": 1, "name": "ProvB"}])
    client._channel_group_name_for_id = AsyncMock(return_value="EVENTS B")
    client.get_streams = AsyncMock(return_value={
        "count": len(secondary_batch), "results": secondary_batch, "next": None,
    })
    client.update_channel = AsyncMock(return_value={})
    engine = ChannelPipelineEngine(client)
    executor = ActionExecutor(
        client, existing_channels=channels, existing_groups=[],
        execution_id=EXECUTION_ID,
    )
    return engine, executor, client


class TestEnginePhase:
    def test_summary_line_and_aggregation(self):
        channels = [
            _master_channel(100, MASTER_MERCURY, streams=[9001]),
            _master_channel(101, MASTER_IMSA),
        ]
        batch = [
            {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            {"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1},
            {"id": 7003, "name": STREAM_UNMATCHED, "m3u_account": 1},
            {"id": 7004, "name": STREAM_NO_EVENT, "m3u_account": 1},
        ]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()
        touched: set = set()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=touched,
        ))

        # THE drift-detector line, in the exact mandated shape.
        summaries = [
            a for e in results["execution_log"]
            for a in e["actions_executed"] if a["type"] == "event_sync_summary"
        ]
        assert len(summaries) == 1
        assert (
            "event_sync: 1 attached, 1 ambiguous skipped, 1 unmatched, "
            "1 parse failures" in summaries[0]["description"]
        )

        # Structured summary on results (rides onto the run result and the
        # execution record via run_pipeline).
        assert len(results["event_sync"]) == 1
        s = results["event_sync"][0]
        assert s["attached"] == 1
        assert s["ambiguous_skipped"] == 1
        assert s["unmatched"] == 1
        assert s["parse_failed"] == 1
        assert "summary_line" in s

        # Merge aggregation mirrors Pass 2.
        assert results["streams_merged"] == 1
        assert touched == {100}
        assert results["rule_match_counts"][1] == 1
        # Per-attach execution-log entry present.
        attach_entries = [
            a for e in results["execution_log"]
            for a in e["actions_executed"] if a["type"] == "event_sync_attach"
        ]
        assert len(attach_entries) == 1
        assert attach_entries[0]["match"]["secondary_stream_name"] == STREAM_MERCURY

    def test_unattended_trigger_refused_defense_in_depth(self):
        """Even if a caller bypasses run_pipeline's gate, the phase refuses
        unattended triggers — nothing is fetched, nothing is attached."""
        channels = [_master_channel(100, MASTER_MERCURY)]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="m3u_refresh", channels_touched_ids=set(),
        ))

        client.update_channel.assert_not_awaited()
        client.get_streams.assert_not_awaited()
        assert results.get("event_sync") is None
        assert results["execution_log"] == []

    def test_cap_overage_warns_on_run_surface(self):
        masters = [
            "Peacock 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
            "Peacock 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
        ]
        channels = [_master_channel(100 + i, m) for i, m in enumerate(masters)]
        batch = [
            {"id": 7001, "name": "TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
             "m3u_account": 1},
            {"id": 7002, "name": "TV 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
             "m3u_account": 1},
        ]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule(config=_config(max_attach_per_run=1))],
            executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        warnings = results["event_sync_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["type"] == "event_sync_attach_capped"
        assert warnings[0]["cap"] == 1
        assert warnings[0]["overage"] == 1
        capped_entries = [
            a for e in results["execution_log"]
            for a in e["actions_executed"] if a["type"] == "event_sync_capped"
        ]
        assert len(capped_entries) == 1
        assert client.update_channel.await_count == 1

    def test_invalid_stored_config_is_skipped_loudly(self):
        engine, executor, client = _engine_with_client([], [])
        results = _results()
        bad_rule = _event_rule(config={"master_group_id": 10})  # no secondaries

        _run(engine._run_event_sync_rules(
            [bad_rule], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        assert results["event_sync"] == []
        assert len(results["event_sync_warnings"]) == 1
        assert results["event_sync_warnings"][0]["type"] == "event_sync_invalid_config"
        client.get_streams.assert_not_awaited()

    def test_config_disabled_is_skipped(self):
        engine, executor, client = _engine_with_client([], [])
        results = _results()
        _run(engine._run_event_sync_rules(
            [_event_rule(config=_config(enabled=False))],
            executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))
        assert results["event_sync"] == []
        assert results["event_sync_warnings"] == []
        client.get_streams.assert_not_awaited()


# =============================================================================
# Full manual run through run_pipeline (execution record + snapshot +
# managed_channel_ids)
# =============================================================================


@pytest.fixture()
def db_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )
    try:
        yield SessionLocal
    finally:
        database.Base.metadata.drop_all(bind=engine)
        engine.dispose()


class TestManualRunPipeline:
    """End-to-end manual live run: only an event_sync rule in scope."""

    def _client(self):
        client = MagicMock()
        master = _master_channel(100, MASTER_MERCURY, streams=[9001])
        manual = {
            "id": 50, "name": "My Manual", "channel_group_id": 3,
            "auto_created": False, "streams": [601],
        }
        other_auto = {
            "id": 60, "name": "Other Auto", "channel_group_id": 4,
            "auto_created": True, "streams": [701],
        }
        client.get_channels = AsyncMock(return_value={
            "count": 3, "results": [master, manual, other_auto],
        })
        client.get_channel_groups = AsyncMock(return_value=[])
        client.get_m3u_accounts = AsyncMock(
            return_value=[{"id": 1, "name": "ProvB"}]
        )
        client._channel_group_name_for_id = AsyncMock(return_value="EVENTS B")
        client.get_streams = AsyncMock(return_value={
            "count": 1,
            "results": [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}],
            "next": None,
        })
        client.update_channel = AsyncMock(return_value={})
        client.get_channel_profiles = AsyncMock(return_value=[])
        return client

    def _add_event_rule(self, session_factory) -> int:
        session = session_factory()
        try:
            rule = ChannelPipelineRule(
                name="Event Rule",
                enabled=True,
                priority=0,
                conditions=json.dumps([{"type": "always"}]),
                actions=json.dumps([{"type": "skip"}]),
                event_sync_config=json.dumps(_config()),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            return rule.id
        finally:
            session.close()

    def test_manual_live_run_attaches_and_records(self, db_session_factory):
        rule_id = self._add_event_rule(db_session_factory)
        client = self._client()
        engine = ChannelPipelineEngine(client)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("journal.log_entries") as mock_log_entries:
            result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            ))

        assert result["success"] is True
        # The attach happened via the merge internals.
        client.update_channel.assert_awaited_once_with(
            100, {"streams": [9001, 7001]}
        )
        assert result["streams_merged"] == 1
        assert result["channels_touched"] == 1
        # Structured summary rode the run result.
        assert result["event_sync"][0]["attached"] == 1
        assert "event_sync: 1 attached" in result["event_sync"][0]["summary_line"]

        # Journal flushed with the event_sync category entry.
        flushed = [e for call in mock_log_entries.call_args_list
                   for e in call.kwargs["entries"]]
        es_entries = [e for e in flushed if e["category"] == "event_sync"]
        assert len(es_entries) == 1
        assert es_entries[0]["batch_id"] == str(result["execution_id"])

        session = db_session_factory()
        try:
            execution = session.query(ChannelPipelineExecution).filter(
                ChannelPipelineExecution.id == result["execution_id"]
            ).first()
            # Summary line persisted on the execution record's log.
            log = json.loads(execution.execution_log)
            summary_entries = [
                a for e in log for a in e.get("actions_executed", [])
                if a.get("type") == "event_sync_summary"
            ]
            assert len(summary_entries) == 1
            assert (
                "event_sync: 1 attached, 0 ambiguous skipped, 0 unmatched, "
                "0 parse failures" in summary_entries[0]["description"]
            )
            assert execution.streams_merged == 1
            assert execution.status == "completed"

            # Snapshot captured BEFORE mutation and includes the event
            # MASTER group's auto-created channel (rollback hook) with its
            # PRE-ATTACH stream list — but not the unrelated auto-created
            # channel (§D3 stays intact for everyone else).
            snapshot = session.query(ChannelPipelineSnapshot).filter(
                ChannelPipelineSnapshot.execution_id == result["execution_id"]
            ).first()
            assert snapshot is not None
            snap_channels = snapshot.get_channels_data()["channels"]
            by_id = {c["id"]: c for c in snap_channels}
            assert 100 in by_id, "event master channel missing from snapshot"
            assert by_id[100]["stream_ids"] == [9001]  # pre-mutation
            assert 50 in by_id  # manual channel still captured
            assert 60 not in by_id  # unrelated auto-created still excluded

            # managed_channel_ids NEVER populated for the event rule.
            rule = session.query(ChannelPipelineRule).filter(
                ChannelPipelineRule.id == rule_id
            ).first()
            assert rule.managed_channel_ids is None
            # Rule stats recorded the run.
            assert rule.last_run_at is not None
            assert rule.match_count == 1
        finally:
            session.close()

    def test_unattended_run_never_attaches(self, db_session_factory):
        """The same rule on the unattended trigger: zero writes."""
        self._add_event_rule(db_session_factory)
        client = self._client()
        engine = ChannelPipelineEngine(client)

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="m3u_refresh"
            ))

        assert result["success"] is True
        assert "manual-run-only" in result["message"]
        client.update_channel.assert_not_awaited()
        client.get_streams.assert_not_awaited()


# =============================================================================
# Unattended watermark task exclusion
# =============================================================================


class TestWatermarkTaskExclusion:
    """tasks/channel_pipeline.py must never hand event_sync rules to the
    unattended post-refresh pipeline — even with run_on_refresh set."""

    def test_event_sync_rules_excluded_from_run_on_refresh_query(
        self, db_session_factory
    ):
        import os
        from tasks.channel_pipeline import ChannelPipelineTask

        session = db_session_factory()
        try:
            standard = ChannelPipelineRule(
                name="Standard", enabled=True, priority=0,
                conditions=json.dumps([{"type": "always"}]),
                actions=json.dumps([{"type": "skip"}]),
                run_on_refresh=True,
            )
            event = ChannelPipelineRule(
                name="Event", enabled=True, priority=1,
                conditions=json.dumps([{"type": "always"}]),
                actions=json.dumps([{"type": "skip"}]),
                run_on_refresh=True,
                event_sync_config=json.dumps(_config()),
            )
            session.add_all([standard, event])
            session.commit()
            standard_id, event_id = standard.id, event.id
        finally:
            session.close()

        settings = MagicMock(
            auto_creation_run_on_refresh_disabled=False,
            last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
            last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
        )
        fake_engine = MagicMock()
        fake_engine.run_pipeline = AsyncMock(return_value={
            "channels_created": 0, "channels_updated": 0,
            "streams_matched": 0, "streams_evaluated": 0,
        })

        os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        with patch("tasks.channel_pipeline.get_settings", return_value=settings), \
             patch("tasks.channel_pipeline.save_settings"), \
             patch("services.notification_service.create_notification_internal",
                   new=AsyncMock()), \
             patch("channel_pipeline_engine.get_channel_pipeline_engine",
                   return_value=fake_engine), \
             patch("database.get_session", side_effect=db_session_factory), \
             patch("tasks.channel_pipeline.get_client",
                   return_value=MagicMock()), \
             patch("journal.log_entry"):
            task = ChannelPipelineTask()
            task._enabled = True
            result = _run(task.execute())

        assert result.success is True
        fake_engine.run_pipeline.assert_awaited_once()
        kwargs = fake_engine.run_pipeline.call_args.kwargs
        # ONLY the standard rule fires from the unattended path; the
        # event_sync rule is excluded by the query filter (belt) — and the
        # engine's triggered_by gate would refuse it anyway (braces).
        assert kwargs["rule_ids"] == [standard_id]
        assert event_id not in kwargs["rule_ids"]
        assert kwargs["triggered_by"] == "m3u_refresh"
