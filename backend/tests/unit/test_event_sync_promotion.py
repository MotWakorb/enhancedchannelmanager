"""Event Sync unmatched-stream promotion (bead ti939.4.1, epic ti939 Phase 3).

The ONE sanctioned exception to "ECM never creates channels", exercised
end to end against the stateful fixture Dispatcharr:

* **AC-1 flag-off regression** — a rule without ``promote_unmatched`` is
  byte-identical everywhere: no ``promotion`` key on the summary, no
  channel creation, Pass 4 stays hard-bypassed (``managed_channel_ids``
  never populated).
* **AC-2 live promotion** — each complete-identity unmatched stream gets
  a channel in the target group, its stream attached, and a journal row
  with ``kind="event_sync_promote"`` content-fingerprint provenance.
* **AC-3 idempotence** — an immediate re-run creates zero channels and
  attaches zero duplicates; same-run cross-provider streams sharing an
  event key share ONE channel.
* **AC-4 dateless identity (t6bin parity)** — a synthesized-date parse
  promotes to a channel with NO date in name/identity; the re-run after
  simulated midnight adopts the same channel.
* **AC-5 reconciliation lifecycle** — stream gone from the playlist →
  next run's Pass 4 deletes the promoted channel per orphan_action;
  masters are provably never in the managed set; first-run-populate
  protection holds; a fetch-failure run makes NO delete observation.
* **AC-6 self-healing** — the event appearing in the master group
  attaches the stream to the master AND reconciles the promoted
  duplicate away in the SAME run.
* **AC-7 cap overage** — creation stops, WARNs, and a
  ``event_sync_promote_capped`` entry lands in event_sync_warnings.
* **AC-10 rollback** — a promotion run rolls back through the standard
  snapshot path: created channels deleted, master stream lists restored.
* **AC-11 exclusion interaction (pinned)** — an ``excluded_by_operator``
  stream still promotes: exclusions block ATTACH to a specific master,
  not promotion.
* **Renumber no-op** — Pass 4 cleanup for an event_sync rule never
  triggers channel renumbering (no create_channel action → no starting
  number), verified rather than built.

Plan-helper unit tests (clustering, naming, cap determinism) live at the
top; they are the same helper the preview endpoint calls, so dry-run
parity is by construction (and re-checked in
tests/routers/test_event_sync_preview.py).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from channel_pipeline_engine import ChannelPipelineEngine
from channel_pipeline_executor import ActionExecutor
from models import ChannelPipelineRule
from services.event_sync_matcher import ParsedEvent, StreamMatchResult
from services.event_sync_promote import (
    DEFAULT_MAX_PROMOTE_PER_RUN,
    PROMOTE_ACTION_ATTACH_EXISTING,
    PROMOTE_ACTION_CREATE,
    build_promotion_plan,
    promoted_channel_name,
)
from services.event_sync_resolver import (
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_EXCLUDED,
    DISPOSITION_PARSE_FAILED,
    DISPOSITION_UNMATCHED,
    ResolvedStream,
    SecondaryStream,
)
from services.event_sync_review import master_event_key
from tests.event_sync_fixtures import (
    FakeDispatcharrState,
    GROUP_NAMES,
    MASTER_GROUP_ID,
    SECONDARY_A,
    SECONDARY_B,
    assert_never_touched_group_settings,
    event_sync_config,
    make_promote_client,
)

EASTERN = pytz.timezone("America/New_York")
FROZEN_NOW = EASTERN.localize(datetime(2026, 7, 11, 12, 0, 0))

PROMOTE_GROUP_ID = 40

MASTER_MERCURY = "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_MERCURY = "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
STREAM_FURY = "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET"
STREAM_FURY_ALT = "FightBox 02: Fury vs. Usyk @ 11 Jul 11:00 PM ET"
MASTER_FURY = "Peacock 99: Fury vs. Usyk @ 11 Jul 11:00 PM ET"
STREAM_TYSON = "DAZN 06: Tyson vs. Paul @ 11 Jul 09:00 PM ET"

SECONDARY_A_NAME = GROUP_NAMES[SECONDARY_A]
SECONDARY_B_NAME = GROUP_NAMES[SECONDARY_B]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# =========================================================================
# Plan-helper unit tests (pure — the same helper preview + run call).
# =========================================================================


def _parsed(title, start, matched_pattern="slot-title-at-datetime"):
    return ParsedEvent(
        raw_name=f"{title} raw", title=title, start=start, teams=None,
        matched_pattern=matched_pattern,
    )


def _resolved(name, disposition, parsed, provider_id=1, stream_id=1,
              group_id=SECONDARY_A, provider="Prov"):
    return ResolvedStream(
        stream=SecondaryStream(
            name=name, group_id=group_id, stream_id=stream_id,
            provider=provider, provider_id=provider_id,
        ),
        result=StreamMatchResult(stream_name=name, parsed=parsed),
        disposition=disposition,
        best=None,
    )


def _promote_config(**overrides):
    config = event_sync_config(
        secondary_group_ids=[SECONDARY_A, SECONDARY_B],
        promote_unmatched=True,
        promote_target_group_id=PROMOTE_GROUP_ID,
        max_promote_per_run=DEFAULT_MAX_PROMOTE_PER_RUN,
    )
    config.update(overrides)
    return config


START = EASTERN.localize(datetime(2026, 7, 11, 23, 0, 0))


class TestPromotionPlan:
    def test_cross_provider_streams_sharing_event_key_form_one_unit(self):
        """PO decision 2: exact-event-key clustering, any provider. The
        cleaner forgives case/whitespace (LOCALS mode), so two providers'
        spellings of the same title cluster."""
        rows = [
            _resolved(STREAM_FURY, DISPOSITION_UNMATCHED,
                      _parsed("Fury vs. Usyk", START),
                      provider_id=2, stream_id=301),
            _resolved(STREAM_FURY_ALT, DISPOSITION_UNMATCHED,
                      _parsed("FURY  VS.  USYK", START),
                      provider_id=3, stream_id=555),
        ]
        plan = build_promotion_plan(_promote_config(), rows, {})
        assert len(plan.units) == 1
        unit = plan.units[0]
        assert len(unit.rows) == 2
        assert unit.action == PROMOTE_ACTION_CREATE
        # Deterministic within-unit order: (provider_id, stream_id).
        assert [r.stream.stream_id for r in unit.rows] == [301, 555]

    def test_clustering_is_exact_key_no_fuzzy(self):
        """PO decision 2, the other half: EXACT key only. 'vs.' and 'vs'
        survive the cleaner as distinct titles, so they mint two units —
        promotion never fuzzy-clusters."""
        rows = [
            _resolved(STREAM_FURY, DISPOSITION_UNMATCHED,
                      _parsed("Fury vs. Usyk", START), stream_id=301),
            _resolved(STREAM_FURY_ALT, DISPOSITION_UNMATCHED,
                      _parsed("Fury vs Usyk", START), stream_id=555),
        ]
        plan = build_promotion_plan(_promote_config(), rows, {})
        assert len(plan.units) == 2

    def test_distinct_event_keys_form_distinct_units_sorted_by_key(self):
        rows = [
            _resolved(STREAM_TYSON, DISPOSITION_UNMATCHED,
                      _parsed("Tyson vs. Paul",
                              EASTERN.localize(datetime(2026, 7, 11, 21, 0))),
                      stream_id=302),
            _resolved(STREAM_FURY, DISPOSITION_UNMATCHED,
                      _parsed("Fury vs. Usyk", START), stream_id=301),
        ]
        plan = build_promotion_plan(_promote_config(), rows, {})
        assert len(plan.units) == 2
        assert [u.event_key for u in plan.units] == sorted(
            u.event_key for u in plan.units
        )

    def test_only_unmatched_and_excluded_are_promotable(self):
        """AC-11 pin: excluded_by_operator IS promotable (exclusions block
        the attach to one master, not promotion); ambiguous and
        parse_failed are NOT."""
        rows = [
            _resolved("a", DISPOSITION_UNMATCHED,
                      _parsed("Event A", START), stream_id=1),
            _resolved("b", DISPOSITION_EXCLUDED,
                      _parsed("Event B", START), stream_id=2),
            _resolved("c", DISPOSITION_AMBIGUOUS,
                      _parsed("Event C", START), stream_id=3),
            _resolved("d", DISPOSITION_PARSE_FAILED,
                      ParsedEvent(raw_name="d", title=None, start=None,
                                  teams=None, matched_pattern=None),
                      stream_id=4),
        ]
        plan = build_promotion_plan(_promote_config(), rows, {})
        promoted_titles = {
            u.rows[0].result.parsed.title for u in plan.units
        }
        assert promoted_titles == {"Event A", "Event B"}

    def test_incomplete_identity_is_not_promotable(self):
        """A parsed title with no start (or vice versa) has no event key —
        it can neither name a channel nor be recognized next run."""
        rows = [
            _resolved("x", DISPOSITION_UNMATCHED,
                      ParsedEvent(raw_name="x", title="Some Event",
                                  start=None, teams=None,
                                  matched_pattern=None)),
        ]
        plan = build_promotion_plan(_promote_config(), rows, {})
        assert plan.units == ()

    def test_existing_name_in_target_group_plans_adoption(self):
        rows = [_resolved(STREAM_FURY, DISPOSITION_UNMATCHED,
                          _parsed("Fury vs. Usyk", START), stream_id=301)]
        name = promoted_channel_name(_parsed("Fury vs. Usyk", START))
        plan = build_promotion_plan(
            _promote_config(), rows, {name.lower(): 900}
        )
        assert plan.units[0].action == PROMOTE_ACTION_ATTACH_EXISTING
        assert plan.units[0].existing_channel_id == 900

    def test_cap_applies_to_creations_only_and_is_deterministic(self):
        cfg = _promote_config(max_promote_per_run=1)
        rows = [
            _resolved("a", DISPOSITION_UNMATCHED,
                      _parsed("Alpha Event", START), stream_id=1),
            _resolved("b", DISPOSITION_UNMATCHED,
                      _parsed("Beta Event", START), stream_id=2),
            _resolved("c", DISPOSITION_UNMATCHED,
                      _parsed("Gamma Event", START), stream_id=3),
        ]
        # 'Beta Event' already exists — adoption is cap-exempt.
        beta_name = promoted_channel_name(_parsed("Beta Event", START))
        plan = build_promotion_plan(cfg, rows, {beta_name.lower(): 901})
        assert plan.cap == 1
        assert plan.capped is True
        assert plan.cap_overage == 1
        realized = {u.rows[0].result.parsed.title: u.action
                    for u in plan.units}
        # Key order: alpha < beta < gamma — alpha takes the single create
        # slot, beta adopts (exempt), gamma is deferred.
        assert realized == {
            "Alpha Event": PROMOTE_ACTION_CREATE,
            "Beta Event": PROMOTE_ACTION_ATTACH_EXISTING,
        }
        assert [u.rows[0].result.parsed.title
                for u in plan.capped_units] == ["Gamma Event"]


class TestPromotedChannelName:
    def test_dated_name_carries_local_date_and_clock(self):
        name = promoted_channel_name(_parsed("Fury vs. Usyk", START))
        assert name == "Fury Vs. Usyk @ Jul 11 11:00 PM"

    def test_dateless_name_has_no_date_component(self):
        """AC-4 half 1: a synthesized-date parse must NEVER leak its
        fabricated date into the name."""
        parsed = _parsed(
            "Fury vs Hall", EASTERN.localize(datetime(2026, 7, 11, 18, 0)),
            matched_pattern="dateless-title-time-ampm",
        )
        name = promoted_channel_name(parsed)
        assert name == "Fury Vs Hall @ 06:00 PM"
        assert "11" not in name and "Jul" not in name

    def test_dateless_name_and_key_stable_across_midnight(self):
        """AC-4 half 2 (t6bin parity at the identity layer): the same
        dateless slot parsed on two different days derives the SAME key
        and the SAME name — so the re-run adopts, never duplicates."""
        day1 = _parsed(
            "Fury vs Hall", EASTERN.localize(datetime(2026, 7, 11, 18, 0)),
            matched_pattern="dateless-title-time-ampm",
        )
        day2 = _parsed(
            "Fury vs Hall", EASTERN.localize(datetime(2026, 7, 12, 18, 0)),
            matched_pattern="dateless-title-time-ampm",
        )
        assert master_event_key(day1) == master_event_key(day2)
        assert promoted_channel_name(day1) == promoted_channel_name(day2)
        # And the plan adopts when the day-1 channel exists.
        rows = [_resolved("FURY vs HALL 6PM", DISPOSITION_UNMATCHED, day2)]
        plan = build_promotion_plan(
            _promote_config(),
            rows,
            {promoted_channel_name(day1).lower(): 902},
        )
        assert plan.units[0].action == PROMOTE_ACTION_ATTACH_EXISTING


# =========================================================================
# Engine/executor integration against the stateful fixture Dispatcharr.
# =========================================================================


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


def _add_rule(session_factory, config) -> int:
    session = session_factory()
    try:
        rule = ChannelPipelineRule(
            name="Event Rule", enabled=True, priority=0,
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


def _manual_run(client, session_factory, dry_run=False, now=FROZEN_NOW):
    engine = ChannelPipelineEngine(client)
    with patch("channel_pipeline_engine.get_session",
               side_effect=session_factory), \
         patch("journal.log_entries") as mock_log_entries, \
         patch("services.event_sync_resolver.datetime") as mock_dt:
        mock_dt.now.return_value = now
        result = _run(engine.run_pipeline(dry_run=dry_run,
                                          triggered_by="manual"))
    entries = [
        e for call in mock_log_entries.call_args_list
        for e in call.kwargs.get("entries", [])
    ]
    return result, entries


def _latest_warnings(session_factory) -> list[dict]:
    """Persisted run warnings — run_pipeline pops event_sync_warnings from
    the API response and persists them on the execution row."""
    from models import ChannelPipelineExecution

    session = session_factory()
    try:
        execution = (
            session.query(ChannelPipelineExecution)
            .order_by(ChannelPipelineExecution.id.desc())
            .first()
        )
        return execution.get_warnings() if execution else []
    finally:
        session.close()


def _managed_ids(session_factory, rule_id) -> list[int]:
    session = session_factory()
    try:
        rule = session.get(ChannelPipelineRule, rule_id)
        return rule.get_managed_channel_ids()
    finally:
        session.close()


def _promote_state() -> FakeDispatcharrState:
    """One attachable master event + one unmatched secondary-only event."""
    return FakeDispatcharrState(
        channels=[{
            "id": 100, "name": MASTER_MERCURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": [9001],
        }],
        secondary_streams={
            SECONDARY_A_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ],
            SECONDARY_B_NAME: [
                {"id": 7301, "name": STREAM_FURY, "m3u_account": 2},
            ],
        },
    )


FURY_CHANNEL_NAME = "Fury Vs. Usyk @ Jul 11 11:00 PM"


class TestFlagOffRegression:
    """AC-1: absent flag → byte-identical behavior everywhere."""

    def test_no_promotion_key_no_creation_no_pass4(self, db_session_factory):
        rule_id = _add_rule(db_session_factory, event_sync_config(
            secondary_group_ids=[SECONDARY_A, SECONDARY_B],
        ))
        state = _promote_state()
        client = make_promote_client(state)

        result, _ = _manual_run(client, db_session_factory)
        assert result["success"] is True
        summary = result["event_sync"][0]
        assert "promotion" not in summary
        assert "promoted" not in summary["summary_line"]
        client.create_channel.assert_not_awaited()
        client.delete_channel.assert_not_awaited()
        assert result["channels_created"] == 0
        assert result["created_entities"] == []
        # Pass 4 stays hard-bypassed: the managed set is never populated.
        assert _managed_ids(db_session_factory, rule_id) == []
        assert_never_touched_group_settings(client)


class TestLivePromotion:
    """AC-2 + managed-set invariant + journal provenance."""

    def test_unmatched_stream_gets_channel_stream_and_journal_row(
        self, db_session_factory
    ):
        rule_id = _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        result, journal_entries = _manual_run(client, db_session_factory)
        assert result["success"] is True
        summary = result["event_sync"][0]

        # The attach path is untouched: Mercury still attaches to master.
        assert summary["attached"] == 1
        assert state.stream_ids_of(100) == [9001, 7001]

        # The unmatched Fury event got its OWN channel in the target group.
        promo = summary["promotion"]
        assert promo["promoted_created"] == 1
        assert promo["streams_attached"] == 1
        assert promo["attach_errors"] == 0
        created = [c for cid, c in state.channels.items() if cid >= 900]
        assert len(created) == 1
        channel = created[0]
        assert channel["name"] == FURY_CHANNEL_NAME
        assert channel["channel_group_id"] == PROMOTE_GROUP_ID
        assert channel["streams"] == [7301]
        assert result["channels_created"] == 1
        assert {e["type"]: e["id"] for e in result["created_entities"]} \
            == {"channel": channel["id"]}

        # Journal: category event_sync, fingerprint provenance, IDs
        # display-only alongside names.
        promote_rows = [
            e for e in journal_entries
            if (e.get("after_value") or {}).get("match", {}).get("kind")
            == "event_sync_promote"
        ]
        assert len(promote_rows) == 1
        row = promote_rows[0]
        assert row["category"] == "event_sync"
        assert row["action_type"] == "merge_stream"
        match = row["after_value"]["match"]
        assert match["provider_id"] == 2
        assert len(match["stream_name_hash"]) == 64
        assert "|" in match["event_key"]
        assert match["secondary_stream_name"] == STREAM_FURY
        assert match["promoted_channel_name"] == FURY_CHANNEL_NAME

        # Managed set: promoted channel ONLY — the master is provably
        # absent (dedicated invariant assertion).
        managed = _managed_ids(db_session_factory, rule_id)
        assert managed == [channel["id"]]
        assert 100 not in managed
        assert_never_touched_group_settings(client)

    def test_summary_line_carries_promotion_counts(self, db_session_factory):
        _add_rule(db_session_factory, _promote_config())
        client = make_promote_client(_promote_state())
        result, _ = _manual_run(client, db_session_factory)
        line = result["event_sync"][0]["summary_line"]
        assert "1 promoted, 0 promoted-adopted" in line


class TestIdempotence:
    """AC-3: re-run creates nothing; cross-provider same-key streams share
    one channel."""

    def test_rerun_creates_zero_and_attaches_zero(self, db_session_factory):
        _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        first, _ = _manual_run(client, db_session_factory)
        assert first["event_sync"][0]["promotion"]["promoted_created"] == 1
        patch_count_after_first = len(state.update_channel_calls)

        second, second_journal = _manual_run(client, db_session_factory)
        promo2 = second["event_sync"][0]["promotion"]
        assert promo2["promoted_created"] == 0
        assert promo2["promoted_adopted"] == 1
        assert promo2["streams_attached"] == 0
        assert promo2["already_attached"] == 1
        assert client.create_channel.await_count == 1
        # No new PATCHes, no merge-journal rows on the re-run (the manual
        # adoption audit row is the only journal artifact).
        assert len(state.update_channel_calls) == patch_count_after_first
        assert [e for e in second_journal
                if e.get("action_type") == "merge_stream"] == []
        assert second["channels_created"] == 0

    def test_rerun_adoption_rides_on_the_managed_ledger(
        self, db_session_factory
    ):
        """GH #801 / bead 0ippw: what makes the re-run adopt.

        This path used to force ``allow_manual_channel_merge=True`` because
        the promoted channel reloads from Dispatcharr without the in-run
        ``auto_created`` marker (the fake client reproduces that: it stores
        the create payload, which never carries the key). The override is
        gone; adoption now rides on the persisted ``managed_channel_ids``
        ledger, which ``_is_manual_channel`` consults.

        Pinning the two facts the removal depends on: run 1 registers the
        promoted channel in the ledger, and run 2's executor is handed it.
        Neuter either and the idempotence test above goes red.
        """
        rule_id = _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        first, _ = _manual_run(client, db_session_factory)
        promoted_id = first["event_sync"][0]["promotion"]["channel_ids"][0]
        assert _managed_ids(db_session_factory, rule_id) == [promoted_id]
        # The reloaded row carries no provenance marker of its own.
        assert "auto_created" not in state.channels[promoted_id]

        with patch(
            "channel_pipeline_engine.ActionExecutor", wraps=ActionExecutor
        ) as executor_cls:
            _manual_run(client, db_session_factory)

        assert promoted_id in executor_cls.call_args.kwargs["managed_channel_ids"]

    def test_cross_provider_streams_share_one_channel(
        self, db_session_factory
    ):
        _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        # Second provider carries the SAME event under its own name shape.
        state.secondary_streams[SECONDARY_A_NAME].append(
            {"id": 7002, "name": STREAM_FURY_ALT, "m3u_account": 1},
        )
        client = make_promote_client(state)

        result, _ = _manual_run(client, db_session_factory)
        promo = result["event_sync"][0]["promotion"]
        assert promo["units"] == 1
        assert promo["promoted_created"] == 1
        assert promo["streams_attached"] == 2
        assert client.create_channel.await_count == 1
        created_id = promo["channel_ids"][0]
        assert sorted(state.stream_ids_of(created_id)) == [7002, 7301]


class TestDatelessPromotion:
    """AC-4 integration: dateless promote + midnight re-run adoption."""

    def _dateless_state(self):
        return FakeDispatcharrState(
            channels=[{
                "id": 100, "name": MASTER_MERCURY,
                "channel_group_id": MASTER_GROUP_ID,
                "auto_created": True, "streams": [9001],
            }],
            secondary_streams={SECONDARY_B_NAME: [
                {"id": 7400, "name": "DAZN 07: FURY vs HALL 6PM",
                 "m3u_account": 2},
            ]},
        )

    def test_dateless_promotes_without_date_and_readopts_after_midnight(
        self, db_session_factory
    ):
        _add_rule(db_session_factory, _promote_config(
            secondary_group_ids=[SECONDARY_B],
            assume_current_date=True,
        ))
        state = self._dateless_state()
        client = make_promote_client(state)

        first, _ = _manual_run(client, db_session_factory, now=FROZEN_NOW)
        promo1 = first["event_sync"][0]["promotion"]
        assert promo1["promoted_created"] == 1
        created_id = promo1["channel_ids"][0]
        name = state.channels[created_id]["name"]
        # No date component — identity/name never carry a synthesized date.
        assert name == "Fury Vs Hall @ 06:00 PM"

        # Simulated midnight: same playlist, next day.
        second, _ = _manual_run(
            client, db_session_factory, now=FROZEN_NOW + timedelta(days=1)
        )
        promo2 = second["event_sync"][0]["promotion"]
        assert promo2["promoted_created"] == 0
        assert promo2["promoted_adopted"] == 1
        assert promo2["already_attached"] == 1
        assert client.create_channel.await_count == 1
        client.delete_channel.assert_not_awaited()
        # Still exactly one promoted channel, same id.
        assert created_id in state.channels


class TestReconciliationLifecycle:
    """AC-5: reconciliation-driven deletion (PO decision 1) + protections."""

    def test_stream_gone_next_run_deletes_promoted_channel(
        self, db_session_factory
    ):
        rule_id = _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        first, _ = _manual_run(client, db_session_factory)
        created_id = first["event_sync"][0]["promotion"]["channel_ids"][0]
        # First-run-populate protection: managed set populated, nothing
        # deleted on the run that first saw the channel.
        assert _managed_ids(db_session_factory, rule_id) == [created_id]
        assert state.deleted_channel_ids == []

        # The provider drops the event from the playlist.
        state.secondary_streams[SECONDARY_B_NAME] = []

        second, _ = _manual_run(client, db_session_factory)
        assert second["success"] is True
        # Pass 4 deleted the promoted channel — and ONLY it. The
        # Dispatcharr-owned master survives (managed-set invariant).
        assert state.deleted_channel_ids == [created_id]
        assert 100 in state.channels
        assert _managed_ids(db_session_factory, rule_id) == []
        assert second["channels_removed"] == 1
        # Renumbering after cleanup is a natural no-op for event_sync rules
        # (no create_channel action → no starting number). Verified, not
        # built.
        client.assign_channel_numbers.assert_not_called()

    def test_fetch_failure_makes_no_delete_observation(
        self, db_session_factory
    ):
        """A transient secondary-fetch failure must never mass-delete
        promoted channels: the rule made no observation this run, so Pass 4
        skips it entirely."""
        rule_id = _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)
        first, _ = _manual_run(client, db_session_factory)
        created_id = first["event_sync"][0]["promotion"]["channel_ids"][0]

        client.get_streams = AsyncMock(
            side_effect=RuntimeError("provider 503")
        )
        second, _ = _manual_run(client, db_session_factory)
        assert second["success"] is True
        assert any(
            w["type"] == "event_sync_fetch_failed"
            for w in _latest_warnings(db_session_factory)
        )
        assert state.deleted_channel_ids == []
        assert created_id in state.channels
        # The managed set is untouched — the channel is still owned.
        assert _managed_ids(db_session_factory, rule_id) == [created_id]


class TestSelfHealing:
    """AC-6: master appears → stream attaches to master AND the promoted
    duplicate reconciles away in the SAME run."""

    def test_master_appearing_reattaches_and_deletes_promoted_duplicate(
        self, db_session_factory
    ):
        _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        first, _ = _manual_run(client, db_session_factory)
        created_id = first["event_sync"][0]["promotion"]["channel_ids"][0]

        # Dispatcharr materializes the event in the master group.
        state.add_master({
            "id": 120, "name": MASTER_FURY,
            "channel_group_id": MASTER_GROUP_ID,
            "auto_created": True, "streams": [9002],
        })

        second, _ = _manual_run(client, db_session_factory)
        summary = second["event_sync"][0]
        # The stream now attaches to the MASTER (promoted channels are not
        # matcher candidates — PO decision 2).
        assert summary["attached"] == 1
        assert 7301 in state.stream_ids_of(120)
        # And the promoted duplicate is gone, same run.
        assert state.deleted_channel_ids == [created_id]
        assert summary["promotion"]["units"] == 0


class TestPromotionCap:
    """AC-7: overage stops creation, WARNs, event_sync_warnings entry."""

    def test_cap_overage_warns_and_defers(self, db_session_factory):
        _add_rule(db_session_factory, _promote_config(max_promote_per_run=1))
        state = _promote_state()
        state.secondary_streams[SECONDARY_B_NAME].append(
            {"id": 7302, "name": STREAM_TYSON, "m3u_account": 2},
        )
        client = make_promote_client(state)

        result, _ = _manual_run(client, db_session_factory)
        promo = result["event_sync"][0]["promotion"]
        assert promo["promoted_created"] == 1
        assert promo["capped"] is True
        assert promo["cap_overage"] == 1
        assert client.create_channel.await_count == 1
        warnings = [w for w in _latest_warnings(db_session_factory)
                    if w["type"] == "event_sync_promote_capped"]
        assert len(warnings) == 1
        assert warnings[0]["cap"] == 1
        assert warnings[0]["overage"] == 1
        assert "promotion cap" in result["event_sync"][0]["summary_line"]


class TestDryRunParity:
    """AC-8 (engine side): a pipeline dry-run computes the same promotion
    counts as the live run and creates NOTHING. (Preview-endpoint parity
    is covered in tests/routers/test_event_sync_preview.py.)"""

    def test_dry_run_creates_nothing_and_predicts_live_counts(
        self, db_session_factory
    ):
        _add_rule(db_session_factory, _promote_config())
        dry_state = _promote_state()
        dry_client = make_promote_client(dry_state)
        dry, _ = _manual_run(dry_client, db_session_factory, dry_run=True)
        dry_promo = dry["event_sync"][0]["promotion"]
        dry_client.create_channel.assert_not_awaited()
        assert dry_state.update_channel_calls == []
        assert dry_state.deleted_channel_ids == []

        live_state = _promote_state()
        live_client = make_promote_client(live_state)
        live, _ = _manual_run(live_client, db_session_factory)
        live_promo = live["event_sync"][0]["promotion"]

        for key in ("units", "promoted_created", "promoted_adopted",
                    "streams_attached", "capped", "cap_overage"):
            assert dry_promo[key] == live_promo[key], key


class TestRollback:
    """AC-10: rollback of a promotion run is defined — the standard
    snapshot path deletes the run-created channels and restores the master
    stream lists."""

    def test_confirmed_rollback_deletes_promoted_and_restores_master(
        self, db_session_factory
    ):
        _add_rule(db_session_factory, _promote_config())
        state = _promote_state()
        client = make_promote_client(state)

        engine = ChannelPipelineEngine(client)
        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("journal.log_entries"), \
             patch("services.event_sync_resolver.datetime") as mock_dt:
            mock_dt.now.return_value = FROZEN_NOW
            result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            ))
        assert result["success"] is True
        created_id = result["event_sync"][0]["promotion"]["channel_ids"][0]
        execution_id = result["execution_id"]
        assert state.stream_ids_of(100) == [9001, 7001]

        # Without confirm: refused (snapshot present).
        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            refused = _run(engine.rollback_execution(execution_id))
        assert refused["success"] is False
        assert refused["requires_confirm"] is True

        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory):
            rolled = _run(engine.rollback_execution(
                execution_id, confirm=True
            ))
        assert rolled["success"] is True
        # The promoted channel is deleted; the master survives with its
        # pre-run stream list.
        assert created_id not in state.channels
        assert created_id in state.deleted_channel_ids
        assert 100 in state.channels
        assert state.stream_ids_of(100) == [9001]


class TestExclusionInteraction:
    """AC-11 integration: an operator exclusion suppresses the ATTACH but
    the stream still promotes (pinned semantics; unit-level pin in
    TestPromotionPlan)."""

    def test_excluded_pairing_stream_is_promoted(self, db_session_factory):
        from services.event_sync_review import (
            pairing_key,
        )
        from services.event_sync_matcher import parse_event_name

        # Exclude the (Mercury stream ↔ Mercury master) pairing, so the
        # stream's ONLY viable pairing is operator-suppressed.
        parsed_master = parse_event_name(MASTER_MERCURY, None, now=FROZEN_NOW)
        fp = pairing_key(1, STREAM_MERCURY, parsed_master)
        assert fp is not None

        _add_rule(db_session_factory, _promote_config(
            secondary_group_ids=[SECONDARY_A],
        ))
        state = FakeDispatcharrState(
            channels=[{
                "id": 100, "name": MASTER_MERCURY,
                "channel_group_id": MASTER_GROUP_ID,
                "auto_created": True, "streams": [9001],
            }],
            secondary_streams={SECONDARY_A_NAME: [
                {"id": 7001, "name": STREAM_MERCURY, "m3u_account": 1},
            ]},
        )
        client = make_promote_client(state)

        engine = ChannelPipelineEngine(client)
        with patch("channel_pipeline_engine.get_session",
                   side_effect=db_session_factory), \
             patch("journal.log_entries"), \
             patch("services.event_sync_exclusion_store.load_exclusion_keys",
                   return_value=frozenset({fp})), \
             patch("services.event_sync_resolver.datetime") as mock_dt:
            mock_dt.now.return_value = FROZEN_NOW
            result = _run(engine.run_pipeline(
                dry_run=False, triggered_by="manual"
            ))

        summary = result["event_sync"][0]
        assert summary["excluded_by_operator"] == 1
        assert summary["attached"] == 0
        # The exclusion blocked the ATTACH — but not the promotion.
        promo = summary["promotion"]
        assert promo["promoted_created"] == 1
        created_id = promo["channel_ids"][0]
        assert state.stream_ids_of(created_id) == [7001]
        # The master was never touched.
        assert state.stream_ids_of(100) == [9001]


class TestDummyEpgCoversPromoted:
    """AC-9 (executor layer): the rule's dummy EPG profile covers the
    promotion target group; foreign EPG is never clobbered; promotion-less
    configs keep the master-only filter."""

    def _executor(self, channels):
        from channel_pipeline_executor import ActionExecutor

        client = AsyncMock()
        executor = ActionExecutor(
            client,
            existing_channels=channels,
            existing_groups=[],
            epg_data=[
                {"id": 501, "epg_source": 61, "tvg_id": "dummy.1"},
            ],
            epg_sources=[
                {"id": 61, "url": "http://ecm/api/dummy-epg/xmltv/9"},
            ],
        )
        return executor, client

    def test_promoted_channel_gets_profile_and_foreign_epg_kept(self):
        from channel_pipeline_executor import ExecutionContext

        channels = [
            {"id": 100, "name": "Master", "channel_group_id": MASTER_GROUP_ID,
             "epg_data_id": None, "streams": []},
            {"id": 900, "name": FURY_CHANNEL_NAME,
             "channel_group_id": PROMOTE_GROUP_ID,
             "epg_data_id": None, "streams": [7301]},
            {"id": 901, "name": "Promoted With Foreign EPG",
             "channel_group_id": PROMOTE_GROUP_ID,
             "epg_data_id": 777, "streams": [7302]},
            {"id": 902, "name": "Elsewhere", "channel_group_id": 55,
             "epg_data_id": None, "streams": []},
        ]
        executor, _ = self._executor(channels)
        exec_ctx = ExecutionContext(dry_run=True)
        summary = _run(executor.assign_event_sync_dummy_epg(
            1, "Event Rule",
            _promote_config(dummy_epg_profile_id=9),
            exec_ctx,
        ))
        entries = {e["entity_id"] for e in summary["assign_entries"]}
        # Master + bare promoted channel are assigned; the foreign-EPG
        # promoted channel is skipped; the unrelated group is untouched.
        assert entries == {100, 900}
        assert summary["skipped_foreign_epg"] == 1

    def test_promotionless_config_keeps_master_only_filter(self):
        from channel_pipeline_executor import ExecutionContext

        channels = [
            {"id": 100, "name": "Master", "channel_group_id": MASTER_GROUP_ID,
             "epg_data_id": None, "streams": []},
            {"id": 900, "name": FURY_CHANNEL_NAME,
             "channel_group_id": PROMOTE_GROUP_ID,
             "epg_data_id": None, "streams": []},
        ]
        executor, _ = self._executor(channels)
        exec_ctx = ExecutionContext(dry_run=True)
        config = event_sync_config(
            secondary_group_ids=[SECONDARY_A],
            dummy_epg_profile_id=9,
        )
        summary = _run(executor.assign_event_sync_dummy_epg(
            1, "Event Rule", config, exec_ctx,
        ))
        entries = {e["entity_id"] for e in summary["assign_entries"]}
        assert entries == {100}


class TestEventSyncAssignChannelProfile:
    """GH #720 / y3m6o.1 Finding 4 (0152): a configured assign_channel_profile
    action on an event_sync rule takes effect via the REAL production path
    (run_pipeline -> _run_event_sync_rules -> execute_event_sync_rule ->
    promotion, then _apply_event_sync_profile_action), applying exclusive
    channel-profile membership to the channels the rule touched this run.

    This is the GENUINE production-path regression the 0151 pass faked: it
    invokes execute_event_sync_rule against real secondary streams, not the
    synthetic no-streams path that manually called the generic action.
    """

    def _add_profile_rule(self, session_factory, config, profile_ids):
        session = session_factory()
        try:
            rule = ChannelPipelineRule(
                name="Event Rule", enabled=True, priority=0,
                conditions=json.dumps([{"type": "always"}]),
                actions=json.dumps([
                    {"type": "assign_channel_profile",
                     "channel_profile_ids": profile_ids},
                ]),
                event_sync_config=json.dumps(config),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            return rule.id
        finally:
            session.close()

    def test_promoted_channel_gets_exclusive_profile_membership(
        self, db_session_factory
    ):
        self._add_profile_rule(db_session_factory, _promote_config(), [1])
        state = _promote_state()
        client = make_promote_client(state)
        client.get_channel_profiles = AsyncMock(
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}]
        )
        client.update_profile_channel = AsyncMock()

        result, _ = _manual_run(client, db_session_factory)

        # The production path promoted the unmatched Fury event to a NEW channel.
        promoted = [cid for cid in state.channels if cid >= 900]
        assert len(promoted) == 1
        promoted_id = promoted[0]

        # #720: the promoted (new) channel — which Dispatcharr auto-joins to ALL
        # profiles — is reconciled to EXACTLY profile 1. The diff-aware reconcile
        # (y3m6o.1 review follow-up) DISABLES the unselected profiles 2 and 3 and
        # SKIPS the redundant enable of the already-auto-joined profile 1, so the
        # end state is exactly {1}. This proves the rule's assign_channel_profile
        # action actually executed on the event_sync path, not enable-only/no-op.
        per_channel: dict[int, dict[int, bool]] = {}
        for c in client.update_profile_channel.call_args_list:
            pid, channel_id, body = c.args[0], c.args[1], c.args[2]
            per_channel.setdefault(channel_id, {})[pid] = body["enabled"]
        assert per_channel.get(promoted_id) == {2: False, 3: False}

        # The rule's event_sync summary records the profile step, and the run is
        # a clean success (all profile writes landed).
        summary = result["event_sync"][0]
        assert summary["assign_channel_profile"]["succeeded"] >= 1
        assert result["success"] is True

    def test_no_profile_action_makes_no_profile_writes(self, db_session_factory):
        """Control: an event_sync rule WITHOUT an assign_channel_profile action
        performs no profile writes — byte-identical to pre-feature behavior."""
        _add_rule(db_session_factory, _promote_config())  # actions = [skip]
        client = make_promote_client(_promote_state())
        client.update_profile_channel = AsyncMock()

        _manual_run(client, db_session_factory)

        client.update_profile_channel.assert_not_called()
