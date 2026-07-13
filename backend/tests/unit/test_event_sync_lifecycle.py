"""Event Sync lifecycle integration tests (bead enhancedchannelmanager-ti939.2.2).

Full-lifecycle runs of the Phase 1B attach path against a STATEFUL mocked
Dispatcharr (``tests.event_sync_fixtures.FakeDispatcharrState``) that encodes
the verified Dispatcharr behaviors — UUID-preserving in-place refreshes,
foreign-stream survival, master-scoped deletion — as fixture semantics.

The seven required scenarios, named so a failure reads as a behavior
statement:

1. **Idempotency** — a second run on unchanged data performs ZERO mutations.
2. **Refresh survival** — attachments survive a master auto-sync refresh
   (in-place update, same ids); the re-run does no duplicate work.
3. **Event ends** — the master channel is deleted by Dispatcharr; the next
   run completes clean and never re-attaches the orphaned secondary stream
   anywhere.
4. **Refresh-ordering race** — a secondary stream that appears before its
   master channel materializes converges on the NEXT run (convergence, not
   immediacy — accepted at home-lab tier).
5. **Slot rename** — a master renamed in place ("TBD vs TBD" → real
   matchup, same id) re-matches under the new name; a stream attached under
   the stale name STAYS attached (ECM never detaches — asserted and
   documented here).
6. **Dry-run parity** — the preview endpoint's decisions equal the actual
   run's decisions on identical fixtures (frozen time, shared corpus).
7. **Manual-run-only by default** — the unattended watermark task never
   executes event_sync rules that lack the explicit auto_run opt-in
   (ti939.3.1: absent flag == false) and never mutates Dispatcharr.

Phase 2 (bead ti939.3.1 + ixujz) adds the opt-in scenarios: an
auto_run=true rule attaches from the watermark task with the SAME journal
provenance and summary line as a manual run; a tripped circuit breaker
blocks the unattended chain (and reset restores it); pre-flight failures
and cap overages notify; the refresh-ordering race converges across
unattended runs too.

Every scenario also rides two standing canaries from the fixtures module:
event_sync never creates/deletes channels and never toggles Dispatcharr
group settings.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineRule
from tests.event_sync_fixtures import (
    FakeDispatcharrState,
    GROUP_NAMES,
    MASTER_GROUP_ID,
    SECONDARY_A,
    SECONDARY_STREAMS,
    assert_never_created_or_deleted_channels,
    assert_never_touched_group_settings,
    event_sync_config,
    live_master_channels,
    make_stateful_client,
)

# The corpus fixture the single-event scenarios revolve around.
MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
SECONDARY_GROUP_NAME = GROUP_NAMES[SECONDARY_A]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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


def _add_event_rule(session_factory, config: dict | None = None,
                    run_on_refresh: bool = False) -> int:
    session = session_factory()
    try:
        rule = ChannelPipelineRule(
            name="Event Rule",
            enabled=True,
            priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            run_on_refresh=run_on_refresh,
            event_sync_config=json.dumps(
                event_sync_config() if config is None else config
            ),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.id
    finally:
        session.close()


def _manual_run(client, session_factory, dry_run: bool = False):
    """One manually-triggered pipeline run; returns (result, journal entries).

    A FRESH engine per run — stateless recompute is the design (nothing may
    carry over between runs except Dispatcharr's own state).
    """
    engine = ChannelPipelineEngine(client)
    with patch("channel_pipeline_engine.get_session",
               side_effect=session_factory), \
         patch("journal.log_entries") as mock_log_entries:
        result = _run(engine.run_pipeline(dry_run=dry_run, triggered_by="manual"))
    entries = [
        e for call in mock_log_entries.call_args_list
        for e in call.kwargs.get("entries", [])
    ]
    return result, entries


def _mercury_state(master_streams: list | None = None) -> FakeDispatcharrState:
    """One master channel + one matching secondary stream."""
    return FakeDispatcharrState(
        channels=[{
            "id": 100, "name": MASTER_MERCURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": list(master_streams or [9001]),
        }],
        secondary_streams={SECONDARY_GROUP_NAME: [
            {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
        ]},
    )


def _config_secondary_a(**overrides) -> dict:
    return event_sync_config(secondary_group_ids=[SECONDARY_A], **overrides)


class TestIdempotency:
    def test_second_run_on_unchanged_data_performs_zero_mutations(
        self, db_session_factory
    ):
        _add_event_rule(db_session_factory, _config_secondary_a())
        state = _mercury_state()
        client = make_stateful_client(state)

        first, first_journal = _manual_run(client, db_session_factory)
        assert first["success"] is True
        assert first["streams_merged"] == 1
        assert first["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]
        assert len(state.update_channel_calls) == 1
        assert len(first_journal) == 1

        second, second_journal = _manual_run(client, db_session_factory)
        assert second["success"] is True
        # ZERO mutations: no PATCHes beyond run 1, no journal entries, the
        # attach shows up as the idempotent already-attached no-op.
        assert len(state.update_channel_calls) == 1
        assert second_journal == []
        assert second["streams_merged"] == 0
        assert second["event_sync"][0]["attached"] == 0
        assert second["event_sync"][0]["already_attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


class TestRefreshSurvival:
    def test_attachments_survive_master_refresh_and_rerun_does_no_duplicate_work(
        self, db_session_factory
    ):
        _add_event_rule(db_session_factory, _config_secondary_a())
        state = _mercury_state()
        client = make_stateful_client(state)

        first, _ = _manual_run(client, db_session_factory)
        assert first["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        # Master auto-sync refresh: SAME id, name tweaked in place, and the
        # foreign (ECM-attached) stream 7001 SURVIVES — the verified
        # Dispatcharr behaviors, encoded by master_refresh().
        state.master_refresh(renames={
            100: "Peacock 14: Mercury vs Aces @ 11 Jul 06:00 PM ET",
        })
        assert state.stream_ids_of(100) == [9001, 7001]

        second, second_journal = _manual_run(client, db_session_factory)
        assert second["success"] is True
        # No duplicate work: the attachment is still present, so the re-run
        # is a pure no-op — no new PATCH, no journal entry, no duplicate id.
        assert len(state.update_channel_calls) == 1
        assert second_journal == []
        assert second["event_sync"][0]["already_attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


class TestEventEnds:
    def test_master_deletion_leaves_next_run_clean_and_never_reattaches_elsewhere(
        self, db_session_factory
    ):
        _add_event_rule(db_session_factory, event_sync_config())
        # Two masters so there is somewhere the orphan COULD be wrongly
        # re-attached; only the Mercury stream is in play.
        state = FakeDispatcharrState(
            channels=live_master_channels(),
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        first, _ = _manual_run(client, db_session_factory)
        assert first["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(55) == [7001]

        # Event over: the master provider dropped the stream, Dispatcharr
        # deleted the channel (cascade detaches the secondary stream). The
        # provider still lists the secondary stream for a while.
        state.end_event(55)

        second, second_journal = _manual_run(client, db_session_factory)
        assert second["success"] is True
        # Clean completion, no errors, no writes: the orphaned secondary
        # stream lands in unmatched and is NOT re-attached to any other
        # master (the remaining masters' stream lists are untouched).
        assert second["event_sync"][0]["attach_errors"] == 0
        assert second["event_sync"][0]["attached"] == 0
        assert second["event_sync"][0]["unmatched"] == 1
        assert second_journal == []
        assert len(state.update_channel_calls) == 1  # run 1's attach only
        assert state.stream_ids_of(56) == []
        assert state.stream_ids_of(57) == []

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


class TestRefreshOrderingRace:
    def test_stream_arriving_before_master_converges_on_the_next_run(
        self, db_session_factory
    ):
        _add_event_rule(db_session_factory, _config_secondary_a())
        # Run 1: the secondary provider published the event FIRST — no
        # master channel exists yet (Dispatcharr has not materialized it).
        state = FakeDispatcharrState(
            channels=[],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        first, first_journal = _manual_run(client, db_session_factory)
        assert first["success"] is True
        # Unmatched THIS run — never guessed, never queued, zero writes.
        assert first["event_sync"][0]["attached"] == 0
        assert first["event_sync"][0]["unmatched"] == 1
        assert first_journal == []
        assert state.update_channel_calls == []

        # Dispatcharr's next master refresh materializes the channel.
        state.add_master({
            "id": 100, "name": MASTER_MERCURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": [9001],
        })

        second, _ = _manual_run(client, db_session_factory)
        # Convergence, not immediacy: the very next run attaches.
        assert second["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


class TestSlotRename:
    """Master renamed IN PLACE — "TBD vs TBD" becomes the real matchup with
    the same channel id (the verified UUID-preservation behavior)."""

    MASTER_TBD = "Peacock 14: TBD vs TBD @ 11 Jul 06:00 PM ET"
    STREAM_TBD = "WNBA TV 01: TBD vs TBD @ 11 Jul 06:00 PM ET"

    def _attached_under_stale_name(self, db_session_factory):
        """Common setup: run 1 attaches the placeholder-named stream, then
        Dispatcharr renames the master in place."""
        _add_event_rule(db_session_factory, _config_secondary_a())
        state = FakeDispatcharrState(
            channels=[{
                "id": 100, "name": self.MASTER_TBD,
                "channel_group_id": MASTER_GROUP_ID,
                "auto_created": True, "streams": [9001],
            }],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": self.STREAM_TBD, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        first, _ = _manual_run(client, db_session_factory)
        assert first["event_sync"][0]["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        # Slot resolves: same id, real matchup name, streams preserved.
        state.master_refresh(renames={100: MASTER_MERCURY})
        return state, client

    def test_rematch_works_against_the_new_name_after_in_place_rename(
        self, db_session_factory
    ):
        state, client = self._attached_under_stale_name(db_session_factory)
        # The secondary provider renamed its stream too (same stream id).
        state.secondary_streams[SECONDARY_GROUP_NAME] = [
            {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
        ]

        second, second_journal = _manual_run(client, db_session_factory)
        assert second["success"] is True
        # Re-match against the NEW name finds the same master; the stream is
        # already on it, so the run is an idempotent no-op — no duplicate.
        assert second["event_sync"][0]["already_attached"] == 1
        assert second["event_sync"][0]["attached"] == 0
        assert second_journal == []
        assert state.stream_ids_of(100) == [9001, 7001]

    def test_stream_attached_under_the_stale_name_stays_attached(
        self, db_session_factory
    ):
        """DOCUMENTED CURRENT BEHAVIOR (assert-and-document, bead
        ti939.2.2 scenario 5): when the secondary provider still carries the
        STALE placeholder name after the master's rename, the stream no
        longer matches anything — but ECM NEVER detaches. The attachment
        made under the stale name persists on the (correct) master channel;
        only the operator or a master-channel deletion removes it. The
        stale-named stream simply lands in unmatched from now on."""
        state, client = self._attached_under_stale_name(db_session_factory)
        # Secondary provider has NOT renamed its stream.

        second, second_journal = _manual_run(client, db_session_factory)
        assert second["success"] is True
        assert second["event_sync"][0]["unmatched"] == 1
        assert second["event_sync"][0]["attached"] == 0
        assert second_journal == []
        # The stale-name attachment is UNTOUCHED — still on the master.
        assert state.stream_ids_of(100) == [9001, 7001]
        assert len(state.update_channel_calls) == 1  # run 1's attach only


FROZEN_NOW = pytz.timezone("America/New_York").localize(
    datetime(2026, 7, 11, 12, 0, 0)
)


class TestDryRunParity:
    """Preview endpoint decisions == live run decisions on IDENTICAL
    fixtures (the shared corpus module), with time frozen so year inference
    cannot drift between the two calls."""

    @pytest.mark.asyncio
    async def test_preview_decisions_equal_live_run_decisions_on_identical_fixtures(
        self, async_client, test_session
    ):
        config = event_sync_config()

        # --- Preview side (HTTP endpoint, zero writes) -------------------
        preview_state = FakeDispatcharrState(
            channels=live_master_channels(),
            secondary_streams=SECONDARY_STREAMS,
        )
        preview_client = make_stateful_client(preview_state)
        with patch("routers.channel_pipeline.get_client",
                   return_value=preview_client), \
             patch("services.event_sync_resolver.datetime") as mock_dt:
            mock_dt.now.return_value = FROZEN_NOW
            resp = await async_client.post(
                "/api/channel-pipeline/event-sync-preview",
                json={"event_sync_config": config},
            )
        assert resp.status_code == 200
        preview = resp.json()
        assert preview_state.update_channel_calls == []

        # --- Live run side (engine attach phase, same fixtures) ----------
        rule = ChannelPipelineRule(
            name="Event Rule", enabled=True, priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            event_sync_config=json.dumps(config),
        )
        test_session.add(rule)
        test_session.commit()

        run_state = FakeDispatcharrState(
            channels=live_master_channels(),
            secondary_streams=SECONDARY_STREAMS,
        )
        run_client = make_stateful_client(run_state)
        engine = ChannelPipelineEngine(run_client)
        with patch("channel_pipeline_engine.get_session",
                   return_value=test_session), \
             patch("journal.log_entries"), \
             patch("services.event_sync_resolver.datetime") as mock_dt:
            mock_dt.now.return_value = FROZEN_NOW
            result = await engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            )
        assert result["success"] is True
        run_summary = result["event_sync"][0]

        # --- Decisions must be EQUAL, not merely similar ------------------
        assert preview["summary"]["would_attach"] == run_summary["attached"]
        assert (preview["summary"]["ambiguous_skipped"]
                == run_summary["ambiguous_skipped"])
        assert preview["summary"]["unmatched"] == run_summary["unmatched"]
        assert preview["summary"]["parse_failed"] == run_summary["parse_failed"]
        assert (preview["summary"]["secondary_streams"]
                == run_summary["secondary_streams"])
        assert (preview["summary"]["master_channels_unparsed"]
                == run_summary["master_channels_unparsed"])

        # The exact (stream -> master channel) attach pairs match.
        preview_pairs = {
            (s["stream_id"], s["would_attach_master"]["channel_id"])
            for s in preview["streams"]
            if s["disposition"] == "would_attach"
        }
        run_pairs = {
            (payload["streams"][-1], cid)
            for cid, payload in run_state.update_channel_calls
        }
        assert preview_pairs == run_pairs
        assert len(run_pairs) == run_summary["attached"] == 1


class TestExecutionSummaryPersistence:
    """enhancedchannelmanager-7wuhd: a live event_sync run must persist the
    structured per-rule counters + the pure-event_sync kind flag on the
    execution row, so the executions UI can render an event_sync-aware summary
    (compute -> persist -> serialize, end to end through the real attach path)."""

    @pytest.mark.asyncio
    async def test_pure_event_sync_run_persists_summary_and_flag(
        self, test_session
    ):
        from models import ChannelPipelineExecution

        config = event_sync_config()
        rule = ChannelPipelineRule(
            name="Event Rule", enabled=True, priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            event_sync_config=json.dumps(config),
        )
        test_session.add(rule)
        test_session.commit()

        state = FakeDispatcharrState(
            channels=live_master_channels(),
            secondary_streams=SECONDARY_STREAMS,
        )
        engine = ChannelPipelineEngine(make_stateful_client(state))
        with patch("channel_pipeline_engine.get_session",
                   return_value=test_session), \
             patch("journal.log_entries"), \
             patch("services.event_sync_resolver.datetime") as mock_dt:
            mock_dt.now.return_value = FROZEN_NOW
            result = await engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            )
        assert result["success"] is True
        run_summary = result["event_sync"][0]

        # The persisted execution row (what the executions API reads) must
        # carry the pure-event_sync flag and the structured counters.
        execution = (
            test_session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc())
            .first()
        )
        assert execution is not None
        assert execution.is_event_sync is True, (
            "a run scoped to only event_sync rules must persist is_event_sync=True"
        )
        persisted = execution.get_event_sync_summary()
        assert len(persisted) == 1
        # Counters survive the round-trip and match the live run summary.
        for key in ("secondary_streams", "attached", "already_attached",
                    "ambiguous_skipped", "unmatched", "parse_failed"):
            assert persisted[0][key] == run_summary[key]
        # Heavy review_candidates payload is stripped from the persisted copy.
        assert "review_candidates" not in persisted[0]

        # to_dict (the API serialization) exposes both fields.
        d = execution.to_dict()
        assert d["is_event_sync"] is True
        assert d["event_sync_summary"] == persisted


class TestManualRunOnly:
    def test_unattended_watermark_task_never_executes_event_sync_rules(
        self, db_session_factory
    ):
        """The full unattended path — ChannelPipelineTask.execute() with a
        REAL engine and live-looking data, and the event_sync rule even
        (mis)configured with run_on_refresh=True: nothing is fetched,
        nothing is attached, Dispatcharr state is untouched."""
        import os
        from tasks.channel_pipeline import ChannelPipelineTask

        _add_event_rule(
            db_session_factory, _config_secondary_a(), run_on_refresh=True
        )
        state = _mercury_state()
        client = make_stateful_client(state)
        engine = ChannelPipelineEngine(client)

        settings = MagicMock(
            auto_creation_run_on_refresh_disabled=False,
            last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
            last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
        )

        os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        with patch("tasks.channel_pipeline.get_settings", return_value=settings), \
             patch("tasks.channel_pipeline.save_settings"), \
             patch("services.notification_service.create_notification_internal",
                   new=AsyncMock()), \
             patch("channel_pipeline_engine.get_channel_pipeline_engine",
                   return_value=engine), \
             patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("database.get_session", side_effect=db_session_factory), \
             patch("tasks.channel_pipeline.get_client", return_value=client), \
             patch("journal.log_entry"), \
             patch("journal.log_entries"):
            task = ChannelPipelineTask()
            task._enabled = True
            result = _run(task.execute())

        assert result.success is True
        # ZERO event_sync activity from the unattended path.
        client.get_streams.assert_not_awaited()
        assert state.update_channel_calls == []
        assert state.stream_ids_of(100) == [9001]
        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


# =============================================================================
# Phase 2 opt-in auto-run (beads ti939.3.1 + ixujz)
# =============================================================================


def _watermark_run(client, session_factory, *, breaker_tripped: bool = False,
                   refresh_at: str = "2026-01-02T00:00:00+00:00",
                   consumed_at: str = "2026-01-01T00:00:00+00:00"):
    """One full ChannelPipelineTask.execute() tick with a REAL engine.

    Returns (TaskResult, journal entries, notification mock). A FRESH engine
    per run — stateless recompute, same as _manual_run.
    """
    import os
    from tasks.channel_pipeline import ChannelPipelineTask

    engine = ChannelPipelineEngine(client)
    settings = MagicMock(
        auto_creation_run_on_refresh_disabled=breaker_tripped,
        last_m3u_refresh_completed_at=refresh_at,
        last_auto_creation_consumed_refresh_at=consumed_at,
    )
    notify = AsyncMock()

    os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
    with patch("tasks.channel_pipeline.get_settings", return_value=settings), \
         patch("tasks.channel_pipeline.save_settings"), \
         patch("services.notification_service.create_notification_internal",
               new=notify), \
         patch("channel_pipeline_engine.get_channel_pipeline_engine",
               return_value=engine), \
         patch("channel_pipeline_engine.get_session",
               side_effect=session_factory), \
         patch("database.get_session", side_effect=session_factory), \
         patch("tasks.channel_pipeline.get_client", return_value=client), \
         patch("journal.log_entry"), \
         patch("journal.log_entries") as mock_log_entries:
        task = ChannelPipelineTask()
        task._enabled = True
        result = _run(task.execute())
    entries = [
        e for call in mock_log_entries.call_args_list
        for e in call.kwargs.get("entries", [])
    ]
    return result, entries, notify


def _latest_execution_summary_line(session_factory) -> str | None:
    """The event_sync summary line persisted on the newest execution record."""
    from models import ChannelPipelineExecution

    session = session_factory()
    try:
        execution = (
            session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc())
            .first()
        )
        if execution is None:
            return None
        for entry in execution.get_execution_log() or []:
            for action in entry.get("actions_executed", []):
                if action.get("type") == "event_sync_summary":
                    return action["description"]
        return None
    finally:
        session.close()


class TestAutoRunOptIn:
    """ti939.3.1: the full unattended path with the explicit opt-in."""

    def test_watermark_task_attaches_with_journal_and_summary_parity(
        self, db_session_factory
    ):
        """An opted-in rule attaches from the watermark task, and the run is
        indistinguishable from a manual run on the audit surfaces: the SAME
        journal provenance (category event_sync, batch_id = execution id,
        names+ids+score/band/delta/verdict) and the SAME summary line — the
        3 AM safety net."""
        _add_event_rule(
            db_session_factory, _config_secondary_a(auto_run=True)
        )
        state = _mercury_state()
        client = make_stateful_client(state)

        result, journal_entries, _ = _watermark_run(
            client, db_session_factory
        )

        assert result.success is True
        assert state.stream_ids_of(100) == [9001, 7001]
        assert len(state.update_channel_calls) == 1

        # --- Journal parity with an identical MANUAL run ------------------
        manual_state = _mercury_state()
        manual_client = make_stateful_client(manual_state)
        manual_session_factory = db_session_factory  # same rule set
        # Fresh DB not needed: the manual run below re-runs the same rule
        # against a fresh Dispatcharr state, producing execution id 2.
        manual_result, manual_entries = _manual_run(
            manual_client, manual_session_factory
        )

        assert len(journal_entries) == 1
        assert len(manual_entries) == 1
        auto_entry = dict(journal_entries[0])
        manual_entry = dict(manual_entries[0])
        # batch_id is the (different) execution id — provenance, not drift.
        # Pop into locals BEFORE asserting (CodeQL py/side-effect-in-assert):
        # the pops are load-bearing for the field-for-field equality below,
        # which must compare batch_id-less dicts.
        auto_batch_id = auto_entry.pop("batch_id")
        manual_batch_id = manual_entry.pop("batch_id")
        assert auto_batch_id != manual_batch_id
        assert auto_entry == manual_entry
        assert journal_entries[0]["category"] == "event_sync"
        match = journal_entries[0]["after_value"]["match"]
        assert match["secondary_stream_name"] == STREAM_MERCURY
        assert match["master_channel_name"] == MASTER_MERCURY
        assert match["band"] == "attach"
        assert "score" in match and "time_delta_minutes" in match
        assert "team_verdict" in match

        # --- Summary-line parity -------------------------------------------
        auto_line = _latest_execution_summary_line(db_session_factory)
        manual_line = manual_result["event_sync"][0]["summary_line"]
        assert auto_line is not None
        # The unattended run's line is checked in full shape, then compared.
        assert auto_line.startswith("event_sync: 1 attached")
        assert manual_line.startswith("event_sync: 1 attached")

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)

    def test_tripped_breaker_blocks_unattended_run_and_reset_restores_it(
        self, db_session_factory
    ):
        """ixujz end-to-end: breaker tripped -> the watermark tick performs
        ZERO event_sync activity; breaker cleared -> the next tick attaches."""
        _add_event_rule(
            db_session_factory, _config_secondary_a(auto_run=True)
        )
        state = _mercury_state()
        client = make_stateful_client(state)

        tripped_result, _, _ = _watermark_run(
            client, db_session_factory, breaker_tripped=True
        )
        assert tripped_result.success is True
        assert tripped_result.details.get("skipped") is True
        assert tripped_result.details.get("reason") == "circuit_breaker"
        client.get_streams.assert_not_awaited()
        assert state.update_channel_calls == []
        assert state.stream_ids_of(100) == [9001]

        # Operator clears the breaker (POST /reset-circuit-breaker); the
        # next tick with an unconsumed watermark runs and attaches.
        reset_result, _, _ = _watermark_run(
            client, db_session_factory, breaker_tripped=False
        )
        assert reset_result.success is True
        assert state.stream_ids_of(100) == [9001, 7001]

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)

    def test_unattended_preflight_failure_notifies_and_skips(
        self, db_session_factory
    ):
        """Master auto-sync OFF at watermark time: the rule is skipped, and
        the failure surfaces as a warning notification (never silence)."""
        _add_event_rule(
            db_session_factory, _config_secondary_a(auto_run=True)
        )
        state = _mercury_state()
        client = make_stateful_client(state)
        client.get_all_m3u_group_settings = AsyncMock(return_value={
            MASTER_GROUP_ID: {"auto_channel_sync": False},
            SECONDARY_A: {"auto_channel_sync": False},
        })

        result, _, notify = _watermark_run(client, db_session_factory)

        assert result.success is True
        assert state.update_channel_calls == []
        assert state.stream_ids_of(100) == [9001]
        preflight_calls = [
            c.kwargs for c in notify.call_args_list
            if c.kwargs.get("title") == (
                "Event Sync: Pre-flight failed (rule skipped)")
        ]
        assert len(preflight_calls) == 1
        assert preflight_calls[0]["notification_type"] == "warning"
        assert preflight_calls[0]["send_alerts"] is True
        assert "auto_channel_sync" in preflight_calls[0]["message"]

    def test_unattended_cap_overage_notifies(self, db_session_factory):
        """Attach-cap overage during an unattended run raises the warning
        notification with the overage count."""
        _add_event_rule(
            db_session_factory,
            _config_secondary_a(auto_run=True, max_attach_per_run=1),
        )
        state = FakeDispatcharrState(
            channels=[
                {"id": 100, "name": MASTER_MERCURY,
                 "channel_group_id": MASTER_GROUP_ID,
                 "auto_created": True, "streams": [9001]},
                {"id": 101,
                 "name": "Peacock 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
                 "channel_group_id": MASTER_GROUP_ID,
                 "auto_created": True, "streams": [9002]},
            ],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
                {"id": 7002,
                 "name": "WNBA TV 02: Sparks vs. Storm @ 11 Jul 07:00 PM ET",
                 "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        result, _, notify = _watermark_run(client, db_session_factory)

        assert result.success is True
        # Exactly one attach happened (the cap), one deferred.
        assert len(state.update_channel_calls) == 1
        cap_calls = [
            c.kwargs for c in notify.call_args_list
            if c.kwargs.get("title") == "Event Sync: Attach cap reached"
        ]
        assert len(cap_calls) == 1
        assert cap_calls[0]["notification_type"] == "warning"
        assert cap_calls[0]["send_alerts"] is True
        assert "1" in cap_calls[0]["message"]

    def test_refresh_ordering_race_converges_across_unattended_runs(
        self, db_session_factory
    ):
        """The documented timing note, now on the unattended path: a
        watermark run that precedes master-channel materialization attaches
        nothing (zero writes, never guesses); the NEXT refresh's run
        converges."""
        _add_event_rule(
            db_session_factory, _config_secondary_a(auto_run=True)
        )
        state = FakeDispatcharrState(
            channels=[],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)

        first, first_journal, _ = _watermark_run(client, db_session_factory)
        assert first.success is True
        assert state.update_channel_calls == []
        assert first_journal == []

        # Dispatcharr's next master refresh materializes the channel, and a
        # NEW refresh watermark arrives.
        state.add_master({
            "id": 100, "name": MASTER_MERCURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": [9001],
        })

        second, _, _ = _watermark_run(
            client, db_session_factory,
            refresh_at="2026-01-03T00:00:00+00:00",
            consumed_at="2026-01-02T00:00:00+00:00",
        )
        assert second.success is True
        assert state.stream_ids_of(100) == [9001, 7001]

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)
