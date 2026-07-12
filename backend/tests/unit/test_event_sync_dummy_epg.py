"""Event Sync dummy EPG auto-assignment (bead enhancedchannelmanager-ti939.3.3).

Full-run behavior of the optional ``dummy_epg_profile_id`` reference in
``event_sync_config``: on every event_sync run (manual AND unattended) the
rule's dummy EPG profile is assigned to master-group channels through the
EXISTING machinery — standard ``assign_epg`` execution against the
Dispatcharr EPG source that serves the profile's XMLTV, with uncovered
channels deferring into the EXISTING Pass 5 refresh-and-retry (which also
auto-adds the master group to the profile's ``channel_group_ids``,
regenerates the XMLTV, refreshes the source, and retries). No parallel
mechanism.

Behavior statements pinned here:

1. **Pass 5 retry path** — a first run against an empty dummy source
   defers every master channel; Pass 5 regenerates + refreshes + retries
   and the channels end the run with the profile's guide data assigned.
2. **Idempotency** — a second run on assigned channels performs ZERO
   further EPG writes (``already_assigned`` no-ops).
3. **Non-clobbering** — a master channel carrying FOREIGN guide data
   (another source / hand-assigned real EPG) is never overwritten.
4. **Steady state** — a channel the source already covers is assigned
   directly, without triggering Pass 5.
5. **Unattended parity** — an ``auto_run`` rule assigns from the
   watermark task exactly like a manual run.
6. **Graceful degradation** — no Dispatcharr source for the profile, or a
   disabled profile, warns and skips ONLY the EPG step; attaches are
   unaffected.

Every scenario rides the standing canaries: event_sync never creates or
deletes channels and never toggles Dispatcharr group settings.
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
from models import ChannelPipelineRule, DummyEPGProfile
from tests.event_sync_fixtures import (
    FakeDispatcharrState,
    GROUP_NAMES,
    MASTER_GROUP_ID,
    SECONDARY_A,
    assert_never_created_or_deleted_channels,
    assert_never_touched_group_settings,
    event_sync_config,
    make_stateful_client,
)

MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
SECONDARY_GROUP_NAME = GROUP_NAMES[SECONDARY_A]

PROFILE_ID = 7
DUMMY_SOURCE_ID = 42


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


def _add_profile(session_factory, enabled: bool = True) -> None:
    session = session_factory()
    try:
        session.add(DummyEPGProfile(
            id=PROFILE_ID, name="Peacock Events EPG", enabled=enabled,
        ))
        session.commit()
    finally:
        session.close()


def _profile_group_ids(session_factory) -> list:
    session = session_factory()
    try:
        profile = session.get(DummyEPGProfile, PROFILE_ID)
        return profile.get_channel_group_ids()
    finally:
        session.close()


def _add_event_rule(session_factory, config: dict) -> int:
    session = session_factory()
    try:
        rule = ChannelPipelineRule(
            name="Event Rule",
            enabled=True,
            priority=0,
            conditions=json.dumps([{"type": "always"}]),
            actions=json.dumps([{"type": "skip"}]),
            event_sync_config=json.dumps(config),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.id
    finally:
        session.close()


def _latest_execution_warnings(session_factory) -> list:
    """Run warnings are POPPED from the result and persisted on the
    execution record (ti939.2.1 surface) — read them back from there."""
    from models import ChannelPipelineExecution

    session = session_factory()
    try:
        execution = (
            session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc())
            .first()
        )
        return (execution.get_warnings() or []) if execution else []
    finally:
        session.close()


def _config(**overrides) -> dict:
    return event_sync_config(
        secondary_group_ids=[SECONDARY_A],
        dummy_epg_profile_id=PROFILE_ID,
        **overrides,
    )


def _mercury_state() -> FakeDispatcharrState:
    return FakeDispatcharrState(
        channels=[{
            "id": 100, "name": MASTER_MERCURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": [9001],
        }],
        secondary_streams={SECONDARY_GROUP_NAME: [
            {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
        ]},
    )


def _dummy_entry(entry_id: int, channel_name: str) -> dict:
    """One EPG data entry as Dispatcharr serves it for the dummy source."""
    return {
        "id": entry_id,
        "tvg_id": f"ecm-{entry_id}",
        "name": channel_name,
        "epg_source": DUMMY_SOURCE_ID,
    }


def _wire_epg(client, initial_entries: list[dict] | None = None,
              source_url: str = f"http://ecm:8000/api/dummy-epg/xmltv/{PROFILE_ID}",
              regenerated_entries: list[dict] | None = None):
    """Wire EPG reads onto the stateful client + a mutable entry store.

    ``regenerated_entries`` land in the store when Pass 5's XMLTV
    regeneration runs — simulating the profile covering the master
    channels after regen + source refresh. Returns (epg_store,
    regenerate_mock, wait_mock) for assertions.
    """
    epg_store: list[dict] = list(initial_entries or [])

    async def _get_epg_data():
        return [dict(e) for e in epg_store]

    client.get_epg_data = AsyncMock(side_effect=_get_epg_data)
    client.get_epg_sources = AsyncMock(return_value=[
        {"id": DUMMY_SOURCE_ID, "name": "ECM Dummy EPG", "url": source_url},
    ])

    regenerate = AsyncMock(side_effect=lambda: epg_store.extend(
        regenerated_entries or []
    ) or len(regenerated_entries or []))
    wait_refresh = AsyncMock()
    return epg_store, regenerate, wait_refresh


def _manual_run(client, session_factory, regenerate, wait_refresh,
                dry_run: bool = False):
    """One manual pipeline run with the Pass 5 dummy-EPG surfaces mocked.

    Patches ``database.get_session`` too: the schema validator's
    dummy_epg_profile_id existence check reads the profile table directly.
    """
    engine = ChannelPipelineEngine(client)
    task_cls = MagicMock()
    task_cls.return_value._regenerate_xmltv = regenerate
    with patch("channel_pipeline_engine.get_session",
               side_effect=session_factory), \
         patch("database.get_session", side_effect=session_factory), \
         patch("tasks.dummy_epg_refresh.DummyEPGRefreshTask", task_cls), \
         patch("tasks.dummy_epg_refresh.wait_for_epg_source_refresh",
               wait_refresh), \
         patch("journal.log_entries"):
        result = _run(engine.run_pipeline(
            dry_run=dry_run, triggered_by="manual"
        ))
    return result


class TestPass5RetryPath:
    """Acceptance: the Pass 5 deferred assign_epg retry path is exercised
    end to end — no parallel mechanism."""

    def test_first_run_defers_then_pass5_assigns_the_profile(
        self, db_session_factory
    ):
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        # Empty source: the profile has never generated XMLTV covering the
        # master group. Pass 5's regeneration produces the entry.
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[],
            regenerated_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        assert result["success"] is True
        # The attach path ran alongside (same run, same rule).
        assert result["event_sync"][0]["attached"] == 1

        # The EPG step deferred (source empty at execution time) ...
        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["profile_id"] == PROFILE_ID
        assert epg_summary["source_id"] == DUMMY_SOURCE_ID
        assert epg_summary["deferred"] == 1
        assert epg_summary["assigned"] == 0

        # ... and Pass 5 did the real work: regenerated the XMLTV,
        # refreshed the source, re-fetched entries, retried the assign.
        regenerate.assert_awaited_once()
        wait_refresh.assert_awaited_once()
        assert state.channels[100]["epg_data_id"] == 501
        # Pass 5 step 1 auto-added the master group to the profile so the
        # regenerated XMLTV covers it (the existing mechanism, reused).
        assert MASTER_GROUP_ID in _profile_group_ids(db_session_factory)
        # The retry rode the EXISTING Pass 5 execution-log surface.
        pass5_entries = [
            e for e in result["execution_log"]
            if str(e.get("stream_name", "")).startswith("[Pass 5")
        ]
        assert pass5_entries, "Pass 5 must appear in the execution log"

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)

    def test_second_run_is_an_idempotent_noop(self, db_session_factory):
        """Already-assigned channels are no-ops: zero further EPG writes."""
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[],
            regenerated_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        first = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )
        assert state.channels[100]["epg_data_id"] == 501
        writes_after_first = len(state.update_channel_calls)

        second = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        assert second["success"] is True
        epg_summary = second["event_sync"][0]["dummy_epg"]
        assert epg_summary["already_assigned"] == 1
        assert epg_summary["assigned"] == 0
        assert epg_summary["deferred"] == 0
        # ZERO further writes and no second Pass 5 cycle.
        assert len(state.update_channel_calls) == writes_after_first
        regenerate.assert_awaited_once()  # run 1 only

        assert_never_touched_group_settings(client)


class TestDirectAssignment:
    def test_channel_covered_by_the_source_is_assigned_without_pass5(
        self, db_session_factory
    ):
        """Steady state: the source already carries the channel's entry —
        a direct standard assign_epg write, no deferral, no Pass 5."""
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        assert result["success"] is True
        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["assigned"] == 1
        assert epg_summary["deferred"] == 0
        assert state.channels[100]["epg_data_id"] == 501
        assert result["channels_updated"] >= 1
        regenerate.assert_not_awaited()

        assert_never_touched_group_settings(client)

    def test_combined_all_profiles_source_is_a_valid_fallback(
        self, db_session_factory
    ):
        """A Dispatcharr source on the combined /api/dummy-epg/xmltv URL
        (no profile id) serves every profile — accepted as fallback."""
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[_dummy_entry(501, MASTER_MERCURY)],
            source_url="http://ecm:8000/api/dummy-epg/xmltv",
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["source_id"] == DUMMY_SOURCE_ID
        assert epg_summary["assigned"] == 1
        assert state.channels[100]["epg_data_id"] == 501


class TestNonClobbering:
    def test_foreign_guide_data_is_never_overwritten(
        self, db_session_factory
    ):
        """A master channel carrying guide data from ANY other source
        (e.g. a hand-assigned real EPG) keeps it."""
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = FakeDispatcharrState(
            channels=[{
                "id": 100, "name": MASTER_MERCURY,
                "channel_group_id": MASTER_GROUP_ID,
                "auto_created": True, "streams": [9001],
                "epg_data_id": 999,  # foreign — not the dummy source's
            }],
            secondary_streams={SECONDARY_GROUP_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["skipped_foreign_epg"] == 1
        assert epg_summary["assigned"] == 0
        assert state.channels[100]["epg_data_id"] == 999
        # The only write this run is the attach — never an EPG overwrite.
        epg_writes = [
            payload for _, payload in state.update_channel_calls
            if "epg_data_id" in payload
        ]
        assert epg_writes == []


class TestUnattendedRuns:
    def test_auto_run_watermark_task_assigns_like_a_manual_run(
        self, db_session_factory
    ):
        """The bead's 'manual AND unattended' requirement: an opted-in
        (auto_run) rule assigns the profile from the watermark task."""
        import os
        from tasks.channel_pipeline import ChannelPipelineTask

        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config(auto_run=True))
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[],
            regenerated_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        engine = ChannelPipelineEngine(client)
        settings = MagicMock(
            auto_creation_run_on_refresh_disabled=False,
            last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
            last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
        )
        task_cls = MagicMock()
        task_cls.return_value._regenerate_xmltv = regenerate

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
             patch("tasks.dummy_epg_refresh.DummyEPGRefreshTask", task_cls), \
             patch("tasks.dummy_epg_refresh.wait_for_epg_source_refresh",
                   wait_refresh), \
             patch("journal.log_entry"), \
             patch("journal.log_entries"):
            task = ChannelPipelineTask()
            task._enabled = True
            result = _run(task.execute())

        assert result.success is True
        # Both the attach AND the guide data landed unattended.
        assert state.stream_ids_of(100) == [9001, 7001]
        assert state.channels[100]["epg_data_id"] == 501
        regenerate.assert_awaited_once()

        assert_never_created_or_deleted_channels(client)
        assert_never_touched_group_settings(client)


class TestGracefulDegradation:
    """EPG-step problems warn and skip ONLY the EPG step — the attach path
    is a safety-relevant feature and must be unaffected."""

    def test_no_dispatcharr_source_for_the_profile_warns_and_still_attaches(
        self, db_session_factory
    ):
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        # A dummy source exists — but for a DIFFERENT profile, and there is
        # no combined source.
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[],
            source_url="http://ecm:8000/api/dummy-epg/xmltv/99",
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        assert result["success"] is True
        assert result["event_sync"][0]["attached"] == 1
        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["source_id"] is None
        warnings = [
            w for w in _latest_execution_warnings(db_session_factory)
            if w["type"] == "event_sync_dummy_epg_no_source"
        ]
        assert len(warnings) == 1
        assert f"/api/dummy-epg/xmltv/{PROFILE_ID}" in warnings[0]["message"]
        assert "epg_data_id" not in state.channels[100]

    def test_disabled_profile_warns_skips_epg_step_and_still_attaches(
        self, db_session_factory
    ):
        """A disabled profile is a run-time warning, never a rule-killing
        validation error — attaches are unaffected."""
        _add_profile(db_session_factory, enabled=False)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client,
            initial_entries=[_dummy_entry(501, MASTER_MERCURY)],
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh
        )

        assert result["success"] is True
        assert result["event_sync"][0]["attached"] == 1
        assert "dummy_epg" not in result["event_sync"][0]
        warnings = [
            w for w in _latest_execution_warnings(db_session_factory)
            if w["type"] == "event_sync_dummy_epg_profile_disabled"
        ]
        assert len(warnings) == 1
        assert "epg_data_id" not in state.channels[100]

    def test_dry_run_defers_reports_and_writes_nothing(
        self, db_session_factory
    ):
        _add_profile(db_session_factory)
        _add_event_rule(db_session_factory, _config())
        state = _mercury_state()
        client = make_stateful_client(state)
        _epg_store, regenerate, wait_refresh = _wire_epg(
            client, initial_entries=[],
        )

        result = _manual_run(
            client, db_session_factory, regenerate, wait_refresh,
            dry_run=True,
        )

        assert result["success"] is True
        epg_summary = result["event_sync"][0]["dummy_epg"]
        assert epg_summary["deferred"] == 1
        # Dry run: no writes, no XMLTV regeneration, no profile edit.
        assert state.update_channel_calls == []
        regenerate.assert_not_awaited()
        assert _profile_group_ids(db_session_factory) == []
        # Pass 5 reported its would-do steps on the dry-run surface.
        assert any(
            str(r.get("stream_name", "")).startswith("[Pass 5")
            for r in result["dry_run_results"]
        )
