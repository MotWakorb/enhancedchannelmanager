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
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_executor import ActionExecutor, ExecutionContext
from config import DispatcharrSettings
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

    def test_attach_failure_aggregates_into_failed_actions(self):
        """y3m6o.1 review (Finding 1): an event_sync attach that FAILS must be
        recorded in results['failed_actions'] so run finalization sets
        completed_with_errors instead of green. Drives the production
        execute_event_sync_rule attach path with a failing update_channel."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.update_channel = AsyncMock(side_effect=RuntimeError("attach boom"))
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        s = results["event_sync"][0]
        assert s["attach_errors"] >= 1
        failed = results.get("failed_actions", [])
        assert any(fa["action_type"] == "event_sync_attach" for fa in failed)
        # One aggregated failure entry per attach error (honest count).
        assert len(failed) == s["attach_errors"]

    def test_clean_attach_records_no_failed_actions(self):
        """Control: a successful attach records nothing in failed_actions."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        assert results["event_sync"][0]["attach_errors"] == 0
        assert not results.get("failed_actions")

    def test_unattended_trigger_refused_defense_in_depth(self):
        """Even if a caller bypasses run_pipeline's gate, the phase refuses
        unattended triggers for rules WITHOUT the auto_run opt-in — nothing
        is fetched, nothing is attached (ti939.3.1: absent flag == false)."""
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

    def test_stream_with_name_but_no_id_is_never_attached(self):
        """PR #616 review (bead ti939.2.2): the secondary-stream fetch has an
        id guard beside the name guard — a stream that would otherwise match
        but has NO id must not append None to a master's stream list."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [
            {"name": STREAM_MERCURY, "m3u_account": 1},  # no "id" key
            {"id": None, "name": STREAM_MERCURY, "m3u_account": 1},
        ]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        client.update_channel.assert_not_awaited()
        assert results["event_sync"][0]["secondary_streams"] == 0

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


GROUP_SETTINGS_OK = {
    10: {"auto_channel_sync": True},
    20: {"auto_channel_sync": False},
}

GROUP_SETTINGS_MASTER_OFF = {
    10: {"auto_channel_sync": False},
    20: {"auto_channel_sync": False},
}


def _breaker_clear():
    return patch("tasks.channel_pipeline._run_on_refresh_suppressed",
                 return_value=(False, ""))


class TestEnginePhasePreRefresh:
    """bead y8yby: optional pre-refresh of the rule's M3U providers before a
    MANUAL run (live Run AND dry-run Test). Never on the unattended auto-run
    path. Best-effort — a failed refresh warns but the run still proceeds.
    """

    @staticmethod
    def _refresh_instance(*, success_count=1, failed_count=0, errors=None):
        inst = MagicMock()
        inst.update_config = MagicMock()
        result = MagicMock()
        result.success_count = success_count
        result.failed_count = failed_count
        result.details = {"errors": errors or []}
        inst.execute = AsyncMock(return_value=result)
        return inst

    def test_manual_run_with_flag_triggers_provider_refresh(self):
        """Flag on + manual run → M3URefreshTask fires with exactly the rule's
        provider-scoped account ids, BEFORE the attach still happens."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        config = _config(
            master={"group_id": 10, "m3u_account_id": 3},
            secondary=[{"group_id": 20, "m3u_account_id": 7}],
            refresh_providers_before_run=True,
        )
        results = _results()
        inst = self._refresh_instance(success_count=2)

        with patch("tasks.m3u_refresh.M3URefreshTask",
                   return_value=inst) as mock_cls:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=False, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        # Refresh fired with exactly the rule's provider accounts (deduped,
        # sorted); no whole-group resolution was needed.
        mock_cls.assert_called_once()
        inst.execute.assert_awaited_once()
        passed = inst.update_config.call_args[0][0]
        assert passed["account_ids"] == [3, 7]
        client.get_m3u_group_settings_by_provider.assert_not_called()
        # The attach still ran after the refresh.
        assert results["event_sync"][0]["attached"] == 1
        client.update_channel.assert_awaited_once()

    def test_whole_group_scopes_resolve_all_backing_providers(self):
        """Whole-group scopes (m3u_account_id None) resolve to EVERY provider
        account carrying that channel group via the by-provider settings."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_m3u_group_settings_by_provider = AsyncMock(return_value={
            (3, 10): {}, (5, 10): {}, (7, 20): {},
        })
        config = _config(refresh_providers_before_run=True)  # flat → whole-group
        results = _results()
        inst = self._refresh_instance()

        with patch("tasks.m3u_refresh.M3URefreshTask", return_value=inst):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=False, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        passed = inst.update_config.call_args[0][0]
        assert passed["account_ids"] == [3, 5, 7]

    def test_manual_run_without_flag_never_refreshes(self):
        """Flag off (absent → false) → no refresh; the run is unchanged."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        with patch("tasks.m3u_refresh.M3URefreshTask") as mock_cls:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config())], executor, results,
                dry_run=False, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        mock_cls.assert_not_called()
        assert results["event_sync"][0]["attached"] == 1

    def test_dry_run_test_with_flag_still_refreshes(self):
        """PO decision: the flag applies to the dry-run Test too — so a Test
        with the flag on triggers a real refresh (Test is not zero-write)."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        config = _config(
            master={"group_id": 10, "m3u_account_id": 3},
            secondary=[{"group_id": 20, "m3u_account_id": 3}],
            refresh_providers_before_run=True,
        )
        results = _results()
        inst = self._refresh_instance()

        with patch("tasks.m3u_refresh.M3URefreshTask", return_value=inst):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=True, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        inst.execute.assert_awaited_once()
        # Dry run writes nothing itself (the attach is simulated).
        client.update_channel.assert_not_awaited()

    def test_failed_refresh_warns_but_run_proceeds(self):
        """Best-effort: a failed provider refresh warns but the attach still
        runs against current data (mirrors the watermark partial-success)."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        config = _config(
            master={"group_id": 10, "m3u_account_id": 3},
            secondary=[{"group_id": 20, "m3u_account_id": 7}],
            refresh_providers_before_run=True,
        )
        results = _results()
        inst = self._refresh_instance(
            success_count=1, failed_count=1, errors=["ProvB: boom"],
        )

        with patch("tasks.m3u_refresh.M3URefreshTask", return_value=inst):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=False, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        # Run proceeded: the stream still attached.
        assert results["event_sync"][0]["attached"] == 1
        client.update_channel.assert_awaited_once()
        # A best-effort partial-failure warning was recorded.
        partial = [
            w for w in results["event_sync_warnings"]
            if w["type"] == "event_sync_prerefresh_partial"
        ]
        assert len(partial) == 1
        assert partial[0]["failed"] == 1

    def test_wholesale_refresh_exception_warns_but_run_proceeds(self):
        """If the refresh raises entirely, the run still proceeds (best-effort);
        a event_sync_prerefresh_failed warning is recorded."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        config = _config(
            master={"group_id": 10, "m3u_account_id": 3},
            secondary=[{"group_id": 20, "m3u_account_id": 7}],
            refresh_providers_before_run=True,
        )
        results = _results()
        inst = MagicMock()
        inst.update_config = MagicMock()
        inst.execute = AsyncMock(side_effect=RuntimeError("dispatcharr down"))

        with patch("tasks.m3u_refresh.M3URefreshTask", return_value=inst):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=False, triggered_by="manual",
                channels_touched_ids=set(),
            ))

        assert results["event_sync"][0]["attached"] == 1
        failed = [
            w for w in results["event_sync_warnings"]
            if w["type"] == "event_sync_prerefresh_failed"
        ]
        assert len(failed) == 1

    def test_auto_run_path_never_prerefreshes(self):
        """The unattended auto-run path must NOT pre-refresh even when the rule
        carries the flag — pre-refreshing after a refresh would be circular."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )
        config = _config(auto_run=True, refresh_providers_before_run=True)
        results = _results()

        with _breaker_clear(), \
                patch("tasks.m3u_refresh.M3URefreshTask") as mock_cls:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=config)], executor, results,
                dry_run=False, triggered_by="m3u_refresh",
                channels_touched_ids=set(),
            ))

        # Auto-run attached, but NO pre-refresh task was constructed.
        assert results["event_sync"][0]["attached"] == 1
        mock_cls.assert_not_called()


class TestEnginePhaseAutoRun:
    """ti939.3.1 + ixujz: the attach phase on the UNATTENDED watermark
    trigger — per-rule opt-in, circuit-breaker gate, and pre-flight.

    Each defense layer is pinned individually by driving the phase directly
    (the task-query and run_pipeline layers have their own tests)."""

    def test_opted_in_rule_runs_unattended_when_breaker_clear(self):
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )
        results = _results()

        with _breaker_clear():
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))

        assert results["event_sync"][0]["attached"] == 1
        client.update_channel.assert_awaited_once()

    def test_mixed_rules_only_opted_in_rule_runs_unattended(self):
        """Surgical relaxation: in one unattended phase call, the opted-in
        rule runs and the non-opted rule is refused."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )
        results = _results()

        with _breaker_clear():
            _run(engine._run_event_sync_rules(
                [
                    _event_rule(rid=1, name="Opted",
                                config=_config(auto_run=True)),
                    _event_rule(rid=2, name="Not Opted"),
                ],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))

        assert len(results["event_sync"]) == 1
        assert results["event_sync"][0]["rule_id"] == 1

    def test_tripped_breaker_blocks_unattended_opted_in_rule(self):
        """ixujz: a tripped circuit breaker gates the event_sync auto-run
        chain — the phase refuses BEFORE any fetch or write, and the refusal
        is a persisted run warning, never silence."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )
        results = _results()

        with patch("tasks.channel_pipeline._run_on_refresh_suppressed",
                   return_value=(True, "circuit_breaker")):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))

        client.get_streams.assert_not_awaited()
        client.update_channel.assert_not_awaited()
        warnings = results["event_sync_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["type"] == "event_sync_auto_run_suppressed"
        assert warnings[0]["reason"] == "circuit_breaker"

    def test_breaker_reset_restores_unattended_runs(self):
        """The reset path: tripped -> blocked, cleared -> the SAME call
        attaches again (manual reset is the only recovery, and it works)."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )

        with patch("tasks.channel_pipeline._run_on_refresh_suppressed",
                   return_value=(True, "circuit_breaker")):
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, _results(), dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))
        client.update_channel.assert_not_awaited()

        results = _results()
        with _breaker_clear():
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))
        assert results["event_sync"][0]["attached"] == 1
        client.update_channel.assert_awaited_once()

    def test_tripped_breaker_never_gates_manual_runs(self):
        """Manual stays the recovery surface: the breaker check is applied
        ONLY to unattended triggers."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        results = _results()

        with patch("tasks.channel_pipeline._run_on_refresh_suppressed",
                   return_value=(True, "circuit_breaker")) as mock_suppressed:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="manual", channels_touched_ids=set(),
            ))

        mock_suppressed.assert_not_called()
        assert results["event_sync"][0]["attached"] == 1

    def test_unattended_preflight_failure_skips_rule_with_warning(self):
        """Master auto-sync OFF during an unattended run: the rule is
        skipped LOUDLY (persisted warning type event_sync_preflight_failed
        -> notification at the task layer), nothing is fetched or written."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_MASTER_OFF
        )
        results = _results()

        with _breaker_clear():
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))

        client.get_streams.assert_not_awaited()
        client.update_channel.assert_not_awaited()
        assert results["event_sync"] == []
        warnings = results["event_sync_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["type"] == "event_sync_preflight_failed"
        assert "auto_channel_sync" in warnings[0]["message"]

    def test_unattended_preflight_error_fails_closed(self):
        """A pre-flight FETCH failure on an unattended run skips the rule
        (fail-closed: no unattended writes under an unverifiable config)."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            side_effect=RuntimeError("dispatcharr down")
        )
        results = _results()

        with _breaker_clear():
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))

        client.update_channel.assert_not_awaited()
        assert results["event_sync"] == []
        assert results["event_sync_warnings"][0]["type"] == (
            "event_sync_preflight_failed"
        )

    def test_manual_runs_do_not_preflight(self):
        """Manual behavior is unchanged: no pre-flight GATE on the manual
        path (the operator previews; preview carries the pre-flight). Proven
        by attaching even though the master group's auto_channel_sync is OFF
        — a state the pre-flight would fail-closed on. (Group settings ARE
        read once for Channel Group Override resolution — bead override — a
        read-only lookup, not a gate.)"""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1}]
        engine, executor, client = _engine_with_client(channels, batch)
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_MASTER_OFF
        )
        results = _results()

        _run(engine._run_event_sync_rules(
            [_event_rule()], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
        ))

        # Attached despite master auto-sync OFF => the manual path never
        # pre-flight-gates (the settings read was override resolution only).
        assert results["event_sync"][0]["attached"] == 1


class TestUnattendedGatesEnqueueNothing:
    """bead odnnf: a gated unattended run (tripped circuit breaker OR failed
    pre-flight) must enqueue NOTHING to the review queue — pinned directly by
    asserting enqueue_review_candidates is never called, not merely inferred
    from the attach path being skipped. Uses an AMBIGUOUS stream so a run that
    reached the enqueue step WOULD enqueue."""

    def _ambiguous_setup(self):
        channels = [
            _master_channel(100, MASTER_MERCURY, streams=[9001]),
            _master_channel(101, MASTER_IMSA, streams=[9002]),
        ]
        batch = [{"id": 7002, "name": STREAM_IMSA_AMBIG, "m3u_account": 1}]
        return _engine_with_client(channels, batch)

    def test_tripped_breaker_enqueues_nothing(self):
        engine, executor, client = self._ambiguous_setup()
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_OK
        )
        results = _results()
        with patch("tasks.channel_pipeline._run_on_refresh_suppressed",
                   return_value=(True, "circuit_breaker")), \
             patch("services.event_sync_review_store."
                   "enqueue_review_candidates") as enq:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))
        enq.assert_not_called()

    def test_failed_preflight_enqueues_nothing(self):
        engine, executor, client = self._ambiguous_setup()
        client.get_all_m3u_group_settings = AsyncMock(
            return_value=GROUP_SETTINGS_MASTER_OFF  # master auto-sync OFF
        )
        results = _results()
        with _breaker_clear(), \
             patch("services.event_sync_review_store."
                   "enqueue_review_candidates") as enq:
            _run(engine._run_event_sync_rules(
                [_event_rule(config=_config(auto_run=True))],
                executor, results, dry_run=False,
                triggered_by="m3u_refresh", channels_touched_ids=set(),
            ))
        enq.assert_not_called()
        # The pre-flight failure is surfaced, not silent.
        assert any(
            w["type"] == "event_sync_preflight_failed"
            for w in results["event_sync_warnings"]
        )


class TestProviderScopedFetch:
    """bead jiscc: a provider-scoped secondary fetches only that provider's
    streams (get_streams m3u_account=), so a shared group can supply different
    providers' streams to different rules."""

    def test_provider_scope_passes_m3u_account_filter(self):
        engine, _executor, client = _engine_with_client([], [])
        config = {
            "master_group_id": 10, "secondary_group_ids": [20],
            "secondary": [{"group_id": 20, "m3u_account_id": 7}],
        }
        _run(engine._fetch_event_sync_secondary_streams(config, {1: "ProvB"}))
        assert client.get_streams.call_args_list, "get_streams not called"
        assert client.get_streams.call_args_list[0].kwargs.get(
            "m3u_account") == 7

    def test_whole_group_scope_passes_none_filter(self):
        engine, _executor, client = _engine_with_client([], [])
        config = {"master_group_id": 10, "secondary_group_ids": [20]}
        _run(engine._fetch_event_sync_secondary_streams(config, {1: "ProvB"}))
        assert client.get_streams.call_args_list[0].kwargs.get(
            "m3u_account") is None


class TestParseMasterFromStream:
    """bead parse-from-stream: master identity read from the attached stream,
    so a cleanly-named master channel still matches via its timed stream."""

    def test_flag_reads_identity_from_attached_stream_and_attaches(self):
        # Channel named cleanly (no time); its attached stream 9001 carries
        # the timed event that the secondary matches.
        channels = [_master_channel(100, "Mercury Aces (clean)", streams=[9001])]
        executor, client = _executor(channels)
        client.get_streams_by_ids = AsyncMock(
            return_value=[{"id": 9001, "name": MASTER_MERCURY}]
        )
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(parse_master_from_stream=True),
            streams, exec_ctx,
        ))
        assert summary["attached"] == 1
        client.update_channel.assert_awaited_once_with(
            100, {"streams": [9001, 7001]}
        )
        client.get_streams_by_ids.assert_awaited_once_with([9001])

    def test_without_flag_a_clean_named_master_cannot_match(self):
        # Same clean channel name, flag OFF: the master name has no time, so
        # it is an unparsed master and nothing attaches — proves the flag is
        # what enables the match.
        channels = [_master_channel(100, "Mercury Aces (clean)", streams=[9001])]
        executor, client = _executor(channels)
        streams = [SecondaryStream(
            name=STREAM_MERCURY, group_id=20, stream_id=7001, provider="ProvB",
        )]
        exec_ctx = ExecutionContext(dry_run=False)
        summary = _run(executor.execute_event_sync_rule(
            1, "Event Rule", _config(), streams, exec_ctx,
        ))
        assert summary["attached"] == 0


class TestEventSyncStreamSortBehavior:
    """bead io0tv: event_sync stream_sort_field orders master-channel streams
    at the LIVE surface — the attach phase registers the master + provider
    map, and Pass 3.5's reorder issues update_channel with the
    higher-priority provider's stream FIRST."""

    def _event_rule_with_sort(self, sort_field="provider_order",
                              sort_order="desc"):
        rule = _event_rule()
        rule.stream_sort_field = sort_field
        rule.stream_sort_order = sort_order
        # _reorder_streams_for_rule getattr-probes these; keep them
        # deterministic on the MagicMock rule.
        rule.quality_tie_break_order = "desc"
        rule.quality_m3u_tie_break_enabled = True
        return rule

    def _two_provider_setup(self):
        """Master 100 owns native stream 9001 (provider 1, priority 1); the
        secondary fetch supplies stream 7001 (provider 2, priority 10 — the
        operator's preferred provider)."""
        channels = [_master_channel(100, MASTER_MERCURY, streams=[9001])]
        batch = [{"id": 7001, "name": STREAM_MERCURY, "m3u_account": 2}]

        client = MagicMock()
        client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "ProvA"},
            {"id": 2, "name": "ProvB"},
        ])
        client._channel_group_name_for_id = AsyncMock(return_value="EVENTS B")
        client.get_streams = AsyncMock(return_value={
            "count": len(batch), "results": batch, "next": None,
        })
        client.get_all_m3u_group_settings = AsyncMock(return_value={})
        client.get_streams_by_ids = AsyncMock(return_value=[
            {"id": 9001, "name": "native", "m3u_account": 1},
        ])
        client.update_channel = AsyncMock(return_value={})

        engine = ChannelPipelineEngine(client)
        engine._existing_channels = channels
        executor = ActionExecutor(
            client, existing_channels=channels, existing_groups=[],
            execution_id=EXECUTION_ID,
        )

        settings = MagicMock()
        settings.m3u_account_priorities = {"1": 1, "2": 10}
        return engine, executor, client, settings

    def test_preferred_provider_stream_reordered_to_top(self):
        engine, executor, client, settings = self._two_provider_setup()
        rule = self._event_rule_with_sort("provider_order", "desc")
        results = _results()
        rule_channel_order_streams: dict = {}
        stream_m3u_map: dict = {}

        _run(engine._run_event_sync_rules(
            [rule], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
            rule_channel_order_streams=rule_channel_order_streams,
            stream_m3u_map=stream_m3u_map,
        ))

        # Attach appended the secondary stream at the tail...
        assert client.update_channel.await_args_list[0].args == (
            100, {"streams": [9001, 7001]},
        )
        # ...and registered the master + provider map for Pass 3.5.
        assert rule_channel_order_streams.get(rule.id) == [100]
        assert stream_m3u_map == {7001: 2, 9001: 1}

        # Pass 3.5 exactly as _process_streams invokes it.
        reorder_results = {"execution_log": [], "dry_run_results": []}
        _run(engine._reorder_channel_streams(
            [rule], rule_channel_order_streams, reorder_results,
            dry_run=False, settings=settings, stream_m3u_map=stream_m3u_map,
        ))

        # LIVE surface: the preferred provider's stream (7001, priority 10)
        # is written FIRST on the master channel.
        client.update_channel.assert_awaited_with(
            100, {"streams": [7001, 9001]},
        )

    def test_no_sort_field_keeps_append_order(self):
        """Contrast rail: without stream_sort_field the attach stays
        append-only — Pass 3.5 issues no second update_channel."""
        engine, executor, client, settings = self._two_provider_setup()
        rule = self._event_rule_with_sort(sort_field=None)
        results = _results()
        rule_channel_order_streams: dict = {}
        stream_m3u_map: dict = {}

        _run(engine._run_event_sync_rules(
            [rule], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
            rule_channel_order_streams=rule_channel_order_streams,
            stream_m3u_map=stream_m3u_map,
        ))
        # No sort field -> the master-native get_streams_by_ids enrichment is
        # skipped (no wasted round-trip).
        client.get_streams_by_ids.assert_not_awaited()

        reorder_results = {"execution_log": [], "dry_run_results": []}
        _run(engine._reorder_channel_streams(
            [rule], rule_channel_order_streams, reorder_results,
            dry_run=False, settings=settings, stream_m3u_map=stream_m3u_map,
        ))

        # Only the attach write ever happened — order stays [native, attach].
        assert client.update_channel.await_count == 1
        assert client.update_channel.await_args.args == (
            100, {"streams": [9001, 7001]},
        )

    @pytest.mark.parametrize("strategy", ["priority", "points"])
    def test_smart_sort_heals_already_attached_no_op_run(self, strategy):
        engine, executor, client, _settings = self._two_provider_setup()
        rule = self._event_rule_with_sort("smart_sort", "asc")
        engine._existing_channels[0]["streams"] = [9001, 7001]
        settings_kwargs = {
            "stream_sort_strategy": strategy,
            "m3u_account_priorities": {"1": 1, "2": 10},
        }
        if strategy == "priority":
            settings_kwargs.update(
                stream_sort_priority=["m3u_priority"],
                stream_sort_enabled={"m3u_priority": True},
            )
        else:
            settings_kwargs["stream_sort_point_rules"] = [{
                "criterion": "m3u_priority",
                "operator": "gte",
                "value": 10,
                "points": 20,
            }]
        settings = DispatcharrSettings(**settings_kwargs)
        results = _results()
        rule_channel_order_streams: dict = {}
        stream_m3u_map: dict = {}
        custom_stream_ids: set[int] = set()
        catchup_stream_ids: set[int] = set()
        stream_metadata_known_ids: set[int] = set()

        _run(engine._run_event_sync_rules(
            [rule], executor, results, dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
            rule_channel_order_streams=rule_channel_order_streams,
            stream_m3u_map=stream_m3u_map,
            custom_stream_ids=custom_stream_ids,
            catchup_stream_ids=catchup_stream_ids,
            stream_metadata_known_ids=stream_metadata_known_ids,
            settings=settings,
        ))

        assert rule_channel_order_streams == {rule.id: [100]}
        assert stream_m3u_map == {7001: 2, 9001: 1}
        client.update_channel.reset_mock()

        _run(engine._reorder_channel_streams(
            [rule], rule_channel_order_streams,
            {"execution_log": [], "dry_run_results": []},
            dry_run=False,
            settings=settings,
            stream_m3u_map=stream_m3u_map,
            custom_stream_ids=custom_stream_ids,
            catchup_stream_ids=catchup_stream_ids,
            stream_metadata_known_ids=stream_metadata_known_ids,
        ))

        client.update_channel.assert_awaited_once_with(
            100, {"streams": [7001, 9001]}
        )

    def test_points_probe_only_rules_skip_master_metadata_fetch(self):
        engine, executor, client, _settings = self._two_provider_setup()
        rule = self._event_rule_with_sort("smart_sort", "asc")
        engine._existing_channels[0]["streams"] = [9001, 7001]
        settings = DispatcharrSettings(
            stream_sort_strategy="points",
            stream_sort_point_rules=[{
                "criterion": "resolution",
                "operator": "gte",
                "value": 1080,
                "points": 20,
            }],
        )

        _run(engine._run_event_sync_rules(
            [rule], executor, _results(), dry_run=False,
            triggered_by="manual", channels_touched_ids=set(),
            rule_channel_order_streams={},
            stream_m3u_map={},
            settings=settings,
        ))

        client.get_streams_by_ids.assert_not_awaited()


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


# =============================================================================
# Watermark task auto_run inclusion + unattended notifications (ti939.3.1)
# =============================================================================


def _watermark_settings():
    return MagicMock(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )


def _add_rule(session_factory, *, name: str, run_on_refresh: bool = False,
              enabled: bool = True, es_config: dict | None = None,
              active_from: date | None = None,
              active_until: date | None = None) -> int:
    session = session_factory()
    try:
        rule = ChannelPipelineRule(
            name=name, enabled=enabled, priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            run_on_refresh=run_on_refresh,
            active_from=active_from,
            active_until=active_until,
            event_sync_config=(
                json.dumps(es_config) if es_config is not None else None
            ),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.id
    finally:
        session.close()


def _execute_watermark_task(db_session_factory, engine_result: dict):
    """Run ChannelPipelineTask.execute() against a REAL rules DB and a fake
    engine. Returns (result, fake_engine.run_pipeline mock, notify mock)."""
    import os
    from tasks.channel_pipeline import ChannelPipelineTask

    fake_engine = MagicMock()
    fake_engine.run_pipeline = AsyncMock(return_value=engine_result)
    notify = AsyncMock()

    os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
    with patch("tasks.channel_pipeline.get_settings",
               return_value=_watermark_settings()), \
         patch("tasks.channel_pipeline.save_settings"), \
         patch("services.notification_service.create_notification_internal",
               new=notify), \
         patch("channel_pipeline_engine.get_channel_pipeline_engine",
               return_value=fake_engine), \
         patch("database.get_session", side_effect=db_session_factory), \
         patch("tasks.channel_pipeline.get_client", return_value=MagicMock()), \
         patch("journal.log_entry"):
        task = ChannelPipelineTask()
        task._enabled = True
        result = _run(task.execute())
    return result, fake_engine.run_pipeline, notify


_ENGINE_NOOP_RESULT = {
    "channels_created": 0, "channels_updated": 0,
    "streams_matched": 0, "streams_evaluated": 0,
}


class TestWatermarkTaskAutoRunInclusion:
    """ti939.3.1: the watermark task's rule selection includes event_sync
    rules IF AND ONLY IF their config carries auto_run=true (the narrow
    adjustment of the ti939.2.1 SQL exclusion)."""

    def test_opted_in_event_sync_rule_included_alongside_standard(
        self, db_session_factory
    ):
        standard_id = _add_rule(
            db_session_factory, name="Standard", run_on_refresh=True,
        )
        # run_on_refresh deliberately FALSE: auto_run alone is the opt-in
        # for event_sync rules — they never use the standard-rule flag.
        opted_id = _add_rule(
            db_session_factory, name="Opted",
            es_config=_config(auto_run=True),
        )
        _add_rule(
            db_session_factory, name="Not Opted",
            es_config=_config(auto_run=False),
        )
        _add_rule(  # absent key == false (backward compat)
            db_session_factory, name="Legacy", es_config=_config(),
        )

        result, run_pipeline, _ = _execute_watermark_task(
            db_session_factory, _ENGINE_NOOP_RESULT
        )

        assert result.success is True
        run_pipeline.assert_awaited_once()
        kwargs = run_pipeline.call_args.kwargs
        assert sorted(kwargs["rule_ids"]) == sorted([standard_id, opted_id])
        assert kwargs["triggered_by"] == "m3u_refresh"

    def test_inactive_standard_and_event_sync_rules_are_excluded(
        self, db_session_factory
    ):
        active_standard = _add_rule(
            db_session_factory, name="Active standard", run_on_refresh=True,
        )
        _add_rule(
            db_session_factory, name="Expired standard", run_on_refresh=True,
            active_until=date(2000, 1, 1),
        )
        _add_rule(
            db_session_factory, name="Future event", es_config=_config(auto_run=True),
            active_from=date(2999, 1, 1),
        )
        result, run_pipeline, _ = _execute_watermark_task(
            db_session_factory, _ENGINE_NOOP_RESULT
        )
        assert result.success is True
        run_pipeline.assert_awaited_once()
        assert run_pipeline.call_args.kwargs["rule_ids"] == [active_standard]

    def test_only_date_gated_rules_logs_window_guidance(
        self, db_session_factory, caplog
    ):
        from log_throttle import reset

        reset("date_gated_refresh:auto_creation")
        _add_rule(
            db_session_factory, name="Expired standard", run_on_refresh=True,
            active_until=date(2000, 1, 1),
        )
        with caplog.at_level("INFO"):
            result, run_pipeline, _ = _execute_watermark_task(
                db_session_factory, _ENGINE_NOOP_RESULT
            )
            second, second_run, _ = _execute_watermark_task(
                db_session_factory, _ENGINE_NOOP_RESULT
            )
        run_pipeline.assert_not_awaited()
        second_run.assert_not_awaited()
        assert "active in their UTC date windows" in result.message
        assert "active in their UTC date windows" in second.message
        assert caplog.text.count("outside their active UTC date windows") == 1
        assert "enable run_on_refresh" not in caplog.text

    def test_opted_in_event_sync_rule_alone_fires_the_watermark_run(
        self, db_session_factory
    ):
        """An instance with ONLY an opted-in event_sync rule (no standard
        run_on_refresh rules) still consumes the watermark and runs."""
        opted_id = _add_rule(
            db_session_factory, name="Opted",
            es_config=_config(auto_run=True),
        )

        result, run_pipeline, _ = _execute_watermark_task(
            db_session_factory, _ENGINE_NOOP_RESULT
        )

        assert result.success is True
        run_pipeline.assert_awaited_once()
        assert run_pipeline.call_args.kwargs["rule_ids"] == [opted_id]

    def test_disabled_opted_in_rule_does_not_fire(self, db_session_factory):
        _add_rule(
            db_session_factory, name="Disabled Opted", enabled=False,
            es_config=_config(auto_run=True),
        )

        result, run_pipeline, _ = _execute_watermark_task(
            db_session_factory, _ENGINE_NOOP_RESULT
        )

        assert result.success is True
        run_pipeline.assert_not_awaited()


class TestWatermarkTaskUnattendedNotifications:
    """ti939.3.1: cap overage and pre-flight failures during an UNATTENDED
    run surface via the existing notification channel, never silence. The
    engine persists the warnings on the execution record; the task reads
    them back and notifies."""

    def _execution_with_warnings(self, db_session_factory,
                                 warnings: list) -> int:
        from datetime import datetime as dt
        session = db_session_factory()
        try:
            execution = ChannelPipelineExecution(
                mode="execute", triggered_by="m3u_refresh",
                started_at=dt.utcnow(), status="completed",
            )
            execution.set_warnings(warnings)
            session.add(execution)
            session.commit()
            session.refresh(execution)
            return execution.id
        finally:
            session.close()

    def test_cap_overage_and_preflight_failure_notify(
        self, db_session_factory
    ):
        _add_rule(
            db_session_factory, name="Opted",
            es_config=_config(auto_run=True),
        )
        execution_id = self._execution_with_warnings(db_session_factory, [
            {"type": "event_sync_attach_capped", "rule_id": 1,
             "rule_name": "Opted", "cap": 100, "overage": 3,
             "message": "cap overage message"},
            {"type": "event_sync_preflight_failed", "rule_id": 1,
             "rule_name": "Opted", "failures": [],
             "message": "preflight failure message"},
            # A non-event_sync warning must NOT notify from this path.
            {"type": "disabled_normalization_group", "rule_id": 1,
             "message": "unrelated"},
        ])

        result, _, notify = _execute_watermark_task(
            db_session_factory,
            {**_ENGINE_NOOP_RESULT, "execution_id": execution_id},
        )

        assert result.success is True
        warn_calls = [
            c.kwargs for c in notify.call_args_list
            if c.kwargs.get("notification_type") == "warning"
        ]
        messages = [c["message"] for c in warn_calls]
        assert "cap overage message" in messages
        assert "preflight failure message" in messages
        assert not any("unrelated" in m for m in messages)
        # Unattended misconfigurations must reach the alert channels.
        assert all(c["send_alerts"] is True for c in warn_calls)

    def test_clean_run_emits_no_warning_notifications(
        self, db_session_factory
    ):
        _add_rule(
            db_session_factory, name="Opted",
            es_config=_config(auto_run=True),
        )
        execution_id = self._execution_with_warnings(db_session_factory, [])

        result, _, notify = _execute_watermark_task(
            db_session_factory,
            {**_ENGINE_NOOP_RESULT, "execution_id": execution_id,
             "event_sync": [{"attached": 2}]},
        )

        assert result.success is True
        warn_calls = [
            c.kwargs for c in notify.call_args_list
            if c.kwargs.get("notification_type") == "warning"
        ]
        assert warn_calls == []

    def test_completion_notification_reports_attached_count(
        self, db_session_factory
    ):
        """The completion toast carries the unattended attach count — part
        of the 3 AM safety net alongside the persisted summary line."""
        _add_rule(
            db_session_factory, name="Opted",
            es_config=_config(auto_run=True),
        )
        execution_id = self._execution_with_warnings(db_session_factory, [])

        result, _, notify = _execute_watermark_task(
            db_session_factory,
            {**_ENGINE_NOOP_RESULT, "execution_id": execution_id,
             "event_sync": [{"attached": 2}, {"attached": 1}]},
        )

        assert result.success is True
        titles = [c.kwargs.get("title", "") for c in notify.call_args_list]
        assert any("3" in t and "attached" in t for t in titles)
