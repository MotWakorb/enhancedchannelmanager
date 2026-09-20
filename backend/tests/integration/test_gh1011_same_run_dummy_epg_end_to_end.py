"""GH #1011 / PR #1012: same-run dummy-EPG deferral, end to end.

Every scenario runs the real ``ChannelPipelineEngine.run_pipeline`` (rules
and dummy-EPG profiles in SQLite, the real ``ActionExecutor``, the real Pass 5
``_refresh_dummy_epg_and_retry``) against a stateful in-memory Dispatcharr.
Only the XMLTV regeneration and the source-refresh wait are doubled, exactly
as the existing Pass 5 harness (``test_event_sync_dummy_epg.py``) doubles
them: regeneration adds one entry per channel this run created, on the source
that serves the channel's group, which is what the real generator does once
Pass 5 has added the group to the profile.

Matrix items from the PR #1012 review, one class each:

1. a planned prepare -> commit cannot persist ``completed`` while the
   assignment was only simulated;
2. a later context-changing action does not redirect the Pass 5 PATCH;
3. two dummy profiles keep separate SQLite group membership and equally
   named channels receive their own source's guide entry;
4. duplicate-name provenance survives real create/select processing;
5. a terminal deferred failure is attributed to its rule in the persisted
   selected-rule outcome;
6. an exhausted retry is terminal at the persisted log/status boundary.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import channel_pipeline_engine as engine_module
from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineExecution, ChannelPipelineRule, DummyEPGProfile

GROUP_A, GROUP_B = 1644, 1645
PROFILE_A, PROFILE_B = 7, 8
SOURCE_A, SOURCE_B = 41, 42
SOURCE_FOR_GROUP = {GROUP_A: SOURCE_A, GROUP_B: SOURCE_B}
PROFILE_FOR_SOURCE = {SOURCE_A: PROFILE_A, SOURCE_B: PROFILE_B}


class Dispatcharr:
    """Stateful in-memory Dispatcharr for standard (create/assign) rules."""

    def __init__(self, streams: list[dict], stale_entries: list[dict] | None = None):
        self.channels: dict[int, dict] = {}
        self.streams = [dict(s) for s in streams]
        self.epg_entries: list[dict] = list(stale_entries or [])
        self.update_calls: list[tuple[int, dict]] = []
        self.next_channel_id = 5000
        self.next_entry_id = 500
        self.pre_existing: set[int] = set()
        self.client = self._build()

    def _build(self) -> MagicMock:
        client = MagicMock()

        async def get_channels(page=1, page_size=100, **_):
            rows = [copy.deepcopy(c) for c in self.channels.values()]
            return {"count": len(rows), "next": None, "results": rows}

        async def get_channel(channel_id):
            return copy.deepcopy(self.channels[channel_id])

        async def get_streams(page=1, page_size=1000, m3u_account=None, **_):
            rows = [copy.deepcopy(s) for s in self.streams if s["m3u_account"] == m3u_account]
            return {"count": len(rows), "next": None, "results": rows}

        async def create_channel(data):
            cid, self.next_channel_id = self.next_channel_id, self.next_channel_id + 1
            row = {"id": cid, "streams": [], **copy.deepcopy(data)}
            self.channels[cid] = row
            return copy.deepcopy(row)

        async def update_channel(channel_id, payload):
            self.update_calls.append((channel_id, copy.deepcopy(payload)))
            self.channels[channel_id].update(copy.deepcopy(payload))
            return copy.deepcopy(self.channels[channel_id])

        async def get_epg_data():
            return [dict(e) for e in self.epg_entries]

        client.get_channels = AsyncMock(side_effect=get_channels)
        client.get_channel = AsyncMock(side_effect=get_channel)
        client.get_channel_groups = AsyncMock(return_value=[
            {"id": GROUP_A, "name": "Snooker A"}, {"id": GROUP_B, "name": "Snooker B"},
        ])
        client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 1, "name": "Provider A"}, {"id": 2, "name": "Provider B"},
        ])
        client.get_streams = AsyncMock(side_effect=get_streams)
        client.create_channel = AsyncMock(side_effect=create_channel)
        client.update_channel = AsyncMock(side_effect=update_channel)
        client.delete_channel = AsyncMock()
        client.get_channel_profiles = AsyncMock(return_value=[])
        client.get_epg_data = AsyncMock(side_effect=get_epg_data)
        client.get_epg_sources = AsyncMock(return_value=[
            {"id": SOURCE_A, "name": "Dummy A", "url": f"http://ecm/api/dummy-epg/xmltv/{PROFILE_A}"},
            {"id": SOURCE_B, "name": "Dummy B", "url": f"http://ecm/api/dummy-epg/xmltv/{PROFILE_B}"},
        ])
        return client

    def regenerate(self, *, cover_created: bool = True):
        """What Pass 5's XMLTV regeneration does once the profile covers the
        group: one entry per channel created this run, on the group's source."""
        async def _regenerate():
            if not cover_created:
                return 0
            added = 0
            for channel in self.channels.values():
                if channel["id"] in self.pre_existing:
                    continue
                source = SOURCE_FOR_GROUP.get(channel.get("channel_group_id"))
                if source is None:
                    continue
                if any(e["name"] == channel["name"] and e["epg_source"] == source for e in self.epg_entries):
                    continue
                eid, self.next_entry_id = self.next_entry_id, self.next_entry_id + 1
                self.epg_entries.append({
                    "id": eid, "tvg_id": f"ecm-{eid}", "name": channel["name"], "epg_source": source,
                })
                added += 1
            return added
        return AsyncMock(side_effect=_regenerate)

    def epg_patches(self, channel_id: int) -> list[dict]:
        return [p for cid, p in self.update_calls if cid == channel_id and "epg_data_id" in p]


def stale(source: int) -> list[dict]:
    """Two stale entries for ``source``: a populated-but-not-covering dummy
    source. Two, not one, because a single-entry source takes the executor's
    single-entry fallback and would assign directly instead of deferring."""
    base = 400 + source * 10
    return [
        {"id": base, "tvg_id": f"ecm-{base}", "name": "Old Event One", "epg_source": source},
        {"id": base + 1, "tvg_id": f"ecm-{base + 1}", "name": "Old Event Two", "epg_source": source},
    ]


def stream(stream_id: int, name: str, account: int, group: int) -> dict:
    return {"id": stream_id, "name": name, "m3u_account": account, "channel_group": group}


@pytest.fixture
def db(test_engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        session.query(ChannelPipelineRule).delete()
        session.query(ChannelPipelineExecution).delete()
        session.query(DummyEPGProfile).delete()
        session.add_all([
            DummyEPGProfile(id=PROFILE_A, name="Profile A", enabled=True),
            DummyEPGProfile(id=PROFILE_B, name="Profile B", enabled=True),
        ])
        session.commit()
    finally:
        session.close()
    return SessionLocal


def add_rule(SessionLocal, *, name: str, actions: list[dict], account: int | None = None,
             group: int = GROUP_A, priority: int = 0) -> int:
    session = SessionLocal()
    try:
        rule = ChannelPipelineRule(
            name=name, enabled=True, priority=priority, m3u_account_id=account,
            target_group_id=group, run_on_refresh=False, orphan_action="none",
            conditions=json.dumps([{"type": "always"}]), actions=json.dumps(actions),
        )
        session.add(rule)
        session.commit()
        return rule.id
    finally:
        session.close()


def profile_groups(SessionLocal, profile_id: int) -> list:
    session = SessionLocal()
    try:
        return sorted(session.get(DummyEPGProfile, profile_id).get_channel_group_ids())
    finally:
        session.close()


def latest_execution(SessionLocal) -> ChannelPipelineExecution:
    session = SessionLocal()
    try:
        return session.query(ChannelPipelineExecution).order_by(ChannelPipelineExecution.id.desc()).first()
    finally:
        session.close()


def run_patches(SessionLocal, regenerate, wait_refresh):
    task_cls = MagicMock()
    task_cls.return_value._regenerate_xmltv = regenerate
    return [
        patch.object(engine_module, "get_session", SessionLocal),
        patch("database.get_session", SessionLocal),
        patch("selected_pipeline_rules.get_session", SessionLocal),
        patch("tasks.dummy_epg_refresh.DummyEPGRefreshTask", task_cls),
        patch("tasks.dummy_epg_refresh.wait_for_epg_source_refresh", wait_refresh),
        patch("journal.log_entries"), patch("journal.log_entry"),
    ]


async def direct_run(dispatcharr: Dispatcharr, SessionLocal, *, regenerate=None, **kwargs) -> dict:
    regenerate = regenerate or dispatcharr.regenerate()
    wait_refresh = AsyncMock()
    patches = run_patches(SessionLocal, regenerate, wait_refresh)
    for p in patches:
        p.start()
    try:
        result = await ChannelPipelineEngine(dispatcharr.client).run_pipeline(
            dry_run=False, triggered_by=kwargs.pop("triggered_by", "manual"), **kwargs,
        )
    finally:
        for p in reversed(patches):
            p.stop()
    result["_regenerate"] = regenerate
    result["_wait_refresh"] = wait_refresh
    return result


def pass5_retry_entries(log: list[dict]) -> list[dict]:
    return [
        action for entry in log if str(entry.get("stream_name", "")).startswith("[Pass 5 Retry]")
        for action in entry.get("actions_executed", [])
    ]


CREATE_A = {"type": "create_channel", "name_template": "{stream_name}", "group_id": GROUP_A}
CREATE_B = {"type": "create_channel", "name_template": "{stream_name}", "group_id": GROUP_B}
ASSIGN_A = {"type": "assign_epg", "epg_id": SOURCE_A}
ASSIGN_B = {"type": "assign_epg", "epg_id": SOURCE_B}


# ===========================================================================
# 1. Planned prepare -> commit: no green execution for a simulated assignment
# ===========================================================================


class TestPlannedCommitDoesNotPersistAnOmittedAssignment:

    async def _prepare_and_commit(self, async_client, SessionLocal, dispatcharr: Dispatcharr, rule_id: int):
        from routers import channel_pipeline as router
        from services import mutation_plan_store as store

        live_engine = ChannelPipelineEngine(dispatcharr.client)
        regenerate = dispatcharr.regenerate()
        patches = run_patches(SessionLocal, regenerate, AsyncMock()) + [
            patch.object(router, "get_session", SessionLocal),
            patch.object(router, "_ensure_engine", AsyncMock(return_value=live_engine)),
            patch.object(store, "mutation_plan_store", store.MutationPlanStore()),
        ]
        for p in patches:
            p.start()
        try:
            prepared = await router._materialize_pipeline_plan(router.RunPipelineRequest(rule_ids=[rule_id]))
            response = await async_client.post("/api/auto-creation/run/commit", json={
                "plan_id": prepared["plan_id"], "plan_hash": prepared["plan_hash"], "phase": "execute",
            })
        finally:
            for p in reversed(patches):
                p.stop()
        return prepared, response, regenerate

    @pytest.mark.asyncio
    async def test_planned_commit_ends_completed_with_errors_and_names_the_reason(
        self, async_client, test_engine, db,
    ):
        rule_id = add_rule(db, name="Slots", actions=[CREATE_A, ASSIGN_A])
        dispatcharr = Dispatcharr([stream(201, "Snooker", 1, GROUP_A)], stale_entries=stale(SOURCE_A))

        prepared, response, regenerate = await self._prepare_and_commit(async_client, db, dispatcharr, rule_id)

        assert response.status_code == 202, response.text
        execution_id = response.json()["execution_id"]
        # The commit really created the channel...
        created = [c for c in dispatcharr.channels.values() if c["name"] == "Snooker"]
        assert len(created) == 1
        # ...and really did NOT assign the guide data: no refresh, no PATCH.
        assert "epg_data_id" not in created[0]
        assert dispatcharr.epg_patches(created[0]["id"]) == []
        regenerate.assert_not_awaited()

        session = db()
        try:
            execution = session.get(ChannelPipelineExecution, execution_id)
        finally:
            session.close()
        assert execution.status == "completed_with_errors"
        assert "planned run cannot regenerate and refresh the dummy EPG" in (execution.error_message or "")
        log = execution.get_execution_log()
        # No simulated Pass 5 work is recorded as if it had been done.
        assert not any(str(e.get("stream_name", "")).startswith("[Pass 5") for e in log)
        assert not any(
            "Would refresh" in (a.get("description") or "") or "Would retry" in (a.get("description") or "")
            for e in log for a in e.get("actions_executed", [])
        )
        failed = [
            a for e in log for a in e.get("actions_executed", [])
            if a.get("type") == "assign_epg" and a.get("success") is False
        ]
        assert len(failed) == 1
        assert "planned run cannot regenerate" in failed[0]["error"]

    @pytest.mark.asyncio
    async def test_direct_run_control_assigns_after_pass5(self, test_engine, db):
        add_rule(db, name="Slots", actions=[CREATE_A, ASSIGN_A])
        dispatcharr = Dispatcharr([stream(201, "Snooker", 1, GROUP_A)], stale_entries=stale(SOURCE_A))

        result = await direct_run(dispatcharr, db)

        assert result["status"] == "completed", result.get("failed_actions")
        assert result["failed_action_count"] == 0
        created = next(c for c in dispatcharr.channels.values() if c["name"] == "Snooker")
        assert created["epg_data_id"] == 500
        result["_regenerate"].assert_awaited_once()
        result["_wait_refresh"].assert_awaited_once()
        assert latest_execution(db).status == "completed"
        assert profile_groups(db, PROFILE_A) == [GROUP_A]


# ===========================================================================
# 2. A later context-changing action does not redirect the Pass 5 PATCH
# ===========================================================================


class TestDeferredTargetIsPinned:

    @pytest.mark.asyncio
    async def test_pass5_patches_the_original_channel_not_the_later_one(self, test_engine, db):
        add_rule(db, name="Slots", actions=[
            CREATE_A, ASSIGN_A,
            {"type": "create_channel", "name_template": "{stream_name} Backup", "group_id": GROUP_A},
        ])
        dispatcharr = Dispatcharr([stream(201, "Snooker", 1, GROUP_A)], stale_entries=stale(SOURCE_A))

        result = await direct_run(dispatcharr, db)

        by_name = {c["name"]: c for c in dispatcharr.channels.values()}
        original, later = by_name["Snooker"], by_name["Snooker Backup"]
        assert original["id"] < later["id"]
        assert result["status"] == "completed", result.get("failed_actions")
        # The deferred assignment landed on the channel it was queued for.
        assert dispatcharr.epg_patches(original["id"]) == [{"epg_data_id": 500}]
        assert dispatcharr.epg_patches(later["id"]) == []
        assert "epg_data_id" not in later
        assert original["epg_data_id"] == 500
        retries = pass5_retry_entries(result["execution_log"])
        assert [r["entity_id"] for r in retries] == [original["id"]]
        assert retries[0]["success"] is True
        # Persisted log agrees.
        persisted = pass5_retry_entries(latest_execution(db).get_execution_log())
        assert [r["entity_id"] for r in persisted] == [original["id"]]


# ===========================================================================
# 3. Separate profiles, separate SQLite membership, separate guide identity
# ===========================================================================


class TestProfileIsolation:

    @pytest.mark.asyncio
    async def test_each_profile_gains_only_its_own_group_and_same_named_channels_get_their_own_entry(
        self, test_engine, db,
    ):
        add_rule(db, name="Rule A", account=1, group=GROUP_A, actions=[CREATE_A, ASSIGN_A], priority=0)
        add_rule(db, name="Rule B", account=2, group=GROUP_B, actions=[CREATE_B, ASSIGN_B], priority=1)
        dispatcharr = Dispatcharr(
            [stream(201, "Snooker", 1, GROUP_A), stream(301, "Snooker", 2, GROUP_B)],
            stale_entries=[*stale(SOURCE_A), *stale(SOURCE_B)],
        )

        result = await direct_run(dispatcharr, db)

        assert result["status"] == "completed", result.get("failed_actions")
        assert result["channels_created"] == 2
        # Persisted profile membership stays isolated.
        assert profile_groups(db, PROFILE_A) == [GROUP_A]
        assert profile_groups(db, PROFILE_B) == [GROUP_B]
        # Each equally named channel carries ITS source's entry.
        by_group = {c["channel_group_id"]: c for c in dispatcharr.channels.values()}
        entry_a = next(e for e in dispatcharr.epg_entries if e["name"] == "Snooker" and e["epg_source"] == SOURCE_A)
        entry_b = next(e for e in dispatcharr.epg_entries if e["name"] == "Snooker" and e["epg_source"] == SOURCE_B)
        assert entry_a["id"] != entry_b["id"]
        assert by_group[GROUP_A]["epg_data_id"] == entry_a["id"]
        assert by_group[GROUP_B]["epg_data_id"] == entry_b["id"]


# ===========================================================================
# 4. Duplicate-name provenance through real create/select processing
# ===========================================================================


class TestDuplicateNameProvenance:

    @pytest.mark.asyncio
    async def test_reselecting_the_earlier_same_named_channel_keeps_same_run_eligibility(
        self, test_engine, db,
    ):
        # Rule A (provider 1) creates "Snooker" in group A; Rule B (provider 2)
        # creates "Snooker" in group B, which replaces the name-keyed cache
        # entry; a second provider-1 stream then SELECTS the group-A channel
        # again through create_channel's if_exists path and assigns EPG.
        add_rule(db, name="Rule A", account=1, group=GROUP_A, priority=0, actions=[
            {**CREATE_A, "if_exists": "skip"}, ASSIGN_A,
        ])
        add_rule(db, name="Rule B", account=2, group=GROUP_B, priority=1, actions=[CREATE_B, ASSIGN_B])
        dispatcharr = Dispatcharr(
            [
                stream(201, "Snooker", 1, GROUP_A),
                stream(301, "Snooker", 2, GROUP_B),
                stream(202, "Snooker", 1, GROUP_A),
            ],
            stale_entries=[*stale(SOURCE_A), *stale(SOURCE_B)],
        )

        result = await direct_run(dispatcharr, db)

        assert result["failed_actions"] == [], result["failed_actions"]
        assert result["status"] == "completed"
        assert result["channels_created"] == 2
        by_group = {c["channel_group_id"]: c for c in dispatcharr.channels.values()}
        assert "epg_data_id" in by_group[GROUP_A] and "epg_data_id" in by_group[GROUP_B]
        # The third stream's assign_epg was deferred (same-run channel), not
        # hard-failed as "pre-existing": no failed assign anywhere in the log.
        hard_failures = [
            a for e in result["execution_log"] for a in e.get("actions_executed", [])
            if a.get("type") == "assign_epg" and a.get("success") is False
        ]
        assert hard_failures == []
        assert latest_execution(db).status == "completed"


# ===========================================================================
# 5 and 6. Terminal retry: persisted status, per-rule outcome, no promise
# ===========================================================================


class TestTerminalRetryIsPersistedAndAttributed:

    @pytest.mark.asyncio
    async def test_selected_run_persists_the_rule_outcome_and_a_terminal_failed_action(
        self, test_engine, db,
    ):
        rule_id = add_rule(db, name="Slots", actions=[CREATE_A, ASSIGN_A])
        dispatcharr = Dispatcharr([stream(201, "Snooker", 1, GROUP_A)], stale_entries=stale(SOURCE_A))

        # Regeneration does NOT end up covering the channel: the retry must be terminal.
        result = await direct_run(
            dispatcharr, db, regenerate=dispatcharr.regenerate(cover_created=False),
            triggered_by="scheduled_selected", rule_ids=[rule_id], require_all_rule_ids=True,
        )

        assert result["status"] == "completed_with_errors"
        assert result["failed_action_count"] == 1
        failure = result["failed_actions"][0]
        assert failure["rule_id"] == rule_id and failure["rule_name"] == "Slots"
        assert failure["action_type"] == "assign_epg"
        assert "even after the dummy EPG was regenerated and the source refreshed" in failure["error"]

        execution = latest_execution(db)
        assert execution.status == "completed_with_errors"
        assert "even after the dummy EPG was regenerated" in (execution.error_message or "")
        state = execution.get_selected_rule_outcome_state()
        assert state["integrity"] == "valid"
        outcome = next(o for o in state["outcomes"] if o["rule_id"] == rule_id)
        assert outcome["status"] == "completed_with_errors"
        assert outcome["error_count"] == 1

        # Item 6 at the persisted boundary: the retry entry is a failure with
        # the terminal reason, and nothing after the first-pass deferral
        # promises more work.
        log = execution.get_execution_log()
        retries = pass5_retry_entries(log)
        assert len(retries) == 1
        assert retries[0]["success"] is False
        assert "even after the dummy EPG was regenerated" in retries[0]["error"]
        pass5_index = next(i for i, e in enumerate(log) if str(e.get("stream_name", "")).startswith("[Pass 5"))
        later_promises = [
            a for e in log[pass5_index:] for a in e.get("actions_executed", [])
            if "Deferred: will assign EPG" in (a.get("description") or "")
        ]
        assert later_promises == []
        summary = [
            a for e in log if e.get("stream_name") == "[Pass 5] Summary"
            for a in e.get("actions_executed", [])
        ]
        assert summary and summary[0]["success"] is False
        # Upstream never received guide data for the channel.
        created = next(c for c in dispatcharr.channels.values() if c["name"] == "Snooker")
        assert dispatcharr.epg_patches(created["id"]) == []
