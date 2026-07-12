"""Engine wiring for the event_sync rule kind (bead ti939.1.3).

NO execution path exists in this phase. These tests pin the two wiring
rails:

* **Pass 1/2 exclusion** — event_sync rules never enter per-stream rule
  evaluation; a run scoped only to event_sync rules is an explicit no-op
  with a preview-only message, and a mixed run evaluates only the
  standard rules.
* **Pass 4 hard bypass** — ``_reconcile_orphans`` skips event_sync rules
  entirely: ``managed_channel_ids`` is NEVER populated (stricter than
  orphan_action="none", which still records the managed set) and no
  orphan cleanup can touch Dispatcharr-owned master channels.

Plus the model-level kind helpers and the backward-compat guarantee that
pre-feature rules load unchanged.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineRule


def _event_sync_config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20, 30],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        "enabled": True,
    }
    config.update(overrides)
    return config


def _make_rule(name: str, *, event_sync: bool = False, enabled: bool = True,
               priority: int = 0,
               es_config: dict | None = None) -> ChannelPipelineRule:
    return ChannelPipelineRule(
        name=name,
        enabled=enabled,
        priority=priority,
        conditions=json.dumps([{"type": "always"}]),
        actions=json.dumps([{"type": "skip"}]),
        event_sync_config=(
            json.dumps(es_config if es_config is not None
                       else _event_sync_config())
            if event_sync else None
        ),
    )


class TestModelKindHelpers:
    """ChannelPipelineRule event_sync accessors and kind detection."""

    def test_standard_rule_is_not_event_sync(self):
        rule = _make_rule("Standard")
        assert rule.is_event_sync() is False
        assert rule.get_event_sync_config() is None

    def test_event_sync_rule_kind_and_config_round_trip(self):
        rule = _make_rule("Event", event_sync=True)
        assert rule.is_event_sync() is True
        assert rule.get_event_sync_config() == _event_sync_config()

    def test_set_event_sync_config_and_clear(self):
        rule = _make_rule("Standard")
        rule.set_event_sync_config(_event_sync_config())
        assert rule.is_event_sync() is True
        rule.set_event_sync_config(None)
        assert rule.is_event_sync() is False
        assert rule.event_sync_config is None

    def test_corrupt_config_still_counts_as_event_sync_kind(self):
        """Kind comes from the RAW column — a corrupt config must NOT make
        the rule fall back to running as a standard rule."""
        rule = _make_rule("Event", event_sync=True)
        rule.event_sync_config = "{not-json"
        assert rule.is_event_sync() is True
        assert rule.get_event_sync_config() is None

    def test_non_dict_json_config_parses_to_none(self):
        rule = _make_rule("Event", event_sync=True)
        rule.event_sync_config = json.dumps(["not", "a", "dict"])
        assert rule.is_event_sync() is True
        assert rule.get_event_sync_config() is None

    def test_to_dict_includes_event_sync_config(self):
        rule = _make_rule("Event", event_sync=True)
        assert rule.to_dict()["event_sync_config"] == _event_sync_config()

    def test_backward_compat_pre_feature_rule_to_dict(self):
        """A pre-feature rule row (no config) serializes with a null config
        and is otherwise unchanged."""
        rule = _make_rule("Legacy")
        d = rule.to_dict()
        assert d["event_sync_config"] is None
        assert d["conditions"] == [{"type": "always"}]
        assert d["actions"] == [{"type": "skip"}]


class TestPass12Exclusion:
    """event_sync rules never enter Pass 1/Pass 2 rule evaluation."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.get_m3u_accounts = AsyncMock(return_value=[])
        self.client.get_streams = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)

    def test_only_event_sync_rules_unattended_run_is_noop(self, test_session):
        """ti939.2.1: on an UNATTENDED trigger a run scoped ONLY to
        event_sync rules is an explicit no-op that says WHY (manual-run-only
        phase), not a silent 'no rules' shrug — and never executes them."""
        rule = _make_rule("Event Rule", event_sync=True)
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True, triggered_by="m3u_refresh")
            )

        assert result["success"] is True
        assert "manual-run-only" in result["message"]
        assert result["streams_evaluated"] == 0
        assert result["streams_matched"] == 0

    def test_only_event_sync_rules_scheduled_trigger_is_noop(self, test_session):
        """Deny-by-default: 'scheduled' is not an allowed event_sync trigger."""
        rule = _make_rule("Event Rule", event_sync=True)
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True, triggered_by="scheduled")
            )

        assert result["success"] is True
        assert "manual-run-only" in result["message"]

    def test_no_rules_message_unchanged(self, test_session):
        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True)
            )
        assert result["message"] == "No enabled rules to process"

    def test_mixed_rules_only_standard_rules_are_processed(self, test_session):
        """With one standard + one event_sync rule, _process_streams must
        receive ONLY the standard rule."""
        standard = _make_rule("Standard Rule", priority=0)
        event = _make_rule("Event Rule", event_sync=True, priority=1)
        test_session.add_all([standard, event])
        test_session.commit()

        captured: dict = {}

        async def fake_process_streams(streams, rules, execution, dry_run,
                                       triggered_by="manual",
                                       event_sync_rules=None):
            captured["rules"] = list(rules)
            captured["event_sync_rules"] = list(event_sync_rules or [])
            return {
                "streams_evaluated": 0, "streams_matched": 0,
                "channels_created": 0, "channels_updated": 0,
                "groups_created": 0, "streams_merged": 0,
                "channels_touched": 0, "streams_skipped": 0,
                "streams_removed": 0, "channels_removed": 0,
                "channels_moved": 0, "pending_merges_added": 0,
                "created_entities": [], "modified_entities": [],
                "dry_run_results": [], "conflicts": [],
                "execution_log": [], "rule_match_counts": {},
                "streams_probed": 0,
            }

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("channel_pipeline_engine.get_session", return_value=test_session), \
             patch.object(self.engine, "_fetch_streams", new=AsyncMock(return_value=[])), \
             patch.object(self.engine, "_apply_global_filters", new=AsyncMock(return_value=([], []))), \
             patch.object(self.engine, "_create_execution", new=AsyncMock(return_value=mock_execution)), \
             patch.object(self.engine, "_save_execution", new=AsyncMock()), \
             patch.object(self.engine, "_process_streams", new=fake_process_streams):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True, triggered_by="manual")
            )

        assert result["success"] is True
        rule_names = [r.name for r in captured["rules"]]
        assert rule_names == ["Standard Rule"], (
            "event_sync rule leaked into Pass 1/2 evaluation"
        )
        # ti939.2.1: on a manual run the event_sync rule is routed to the
        # dedicated attach phase instead (threaded as event_sync_rules).
        assert [r.name for r in captured["event_sync_rules"]] == ["Event Rule"]

    def test_unspecified_default_trigger_never_reaches_event_sync(
        self, test_session
    ):
        """PR #616 review (bead ti939.2.2): run_pipeline's triggered_by
        defaults to the DENIED sentinel — a caller that forgets to identify
        its trigger can never execute event_sync rules by accident."""
        from channel_pipeline_engine import (
            EVENT_SYNC_ALLOWED_TRIGGERS,
            TRIGGERED_BY_UNSPECIFIED,
        )

        # The structural rail: the sentinel is not an allowed trigger.
        assert TRIGGERED_BY_UNSPECIFIED not in EVENT_SYNC_ALLOWED_TRIGGERS

        rule = _make_rule("Event Rule", event_sync=True)
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True)  # no triggered_by
            )

        assert result["success"] is True
        assert "manual-run-only" in result["message"]
        self.client.get_streams.assert_not_awaited()


class TestAutoRunTriggerGate:
    """Phase 2 (ti939.3.1): the per-rule trigger gate. Deny-by-default is
    preserved — the ONLY relaxation is the watermark trigger for rules whose
    config carries the explicit auto_run=true opt-in."""

    def test_manual_and_api_always_allowed(self):
        from channel_pipeline_engine import event_sync_trigger_allowed
        for trig in ("manual", "api"):
            assert event_sync_trigger_allowed(trig, {"auto_run": False}) is True
            assert event_sync_trigger_allowed(trig, {}) is True
            assert event_sync_trigger_allowed(trig, None) is True

    def test_watermark_trigger_requires_explicit_opt_in(self):
        from channel_pipeline_engine import (
            EVENT_SYNC_AUTO_RUN_TRIGGER,
            event_sync_trigger_allowed,
        )
        assert event_sync_trigger_allowed(
            EVENT_SYNC_AUTO_RUN_TRIGGER, {"auto_run": True}) is True
        assert event_sync_trigger_allowed(
            EVENT_SYNC_AUTO_RUN_TRIGGER, {"auto_run": False}) is False
        # Absent key == false — the backward-compat rail for stored configs.
        assert event_sync_trigger_allowed(
            EVENT_SYNC_AUTO_RUN_TRIGGER, {}) is False
        assert event_sync_trigger_allowed(
            EVENT_SYNC_AUTO_RUN_TRIGGER, None) is False

    def test_truthy_non_bool_auto_run_is_not_an_opt_in(self):
        """Only literal True opts in — a schema-bypassing caller that stored
        a truthy non-bool never enables unattended runs."""
        from channel_pipeline_engine import event_sync_trigger_allowed
        assert event_sync_trigger_allowed(
            "m3u_refresh", {"auto_run": "true"}) is False
        assert event_sync_trigger_allowed(
            "m3u_refresh", {"auto_run": 1}) is False

    def test_scheduled_and_unspecified_denied_even_with_opt_in(self):
        """auto_run opts into the WATERMARK trigger only — every other
        unattended trigger stays denied (deny-by-default posture)."""
        from channel_pipeline_engine import (
            TRIGGERED_BY_UNSPECIFIED,
            event_sync_trigger_allowed,
        )
        assert event_sync_trigger_allowed(
            "scheduled", {"auto_run": True}) is False
        assert event_sync_trigger_allowed(
            TRIGGERED_BY_UNSPECIFIED, {"auto_run": True}) is False


class TestAutoRunRouting:
    """run_pipeline routes the watermark trigger per rule (ti939.3.1)."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.get_m3u_accounts = AsyncMock(return_value=[])
        self.client.get_streams = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)

    def _run_with_captured_process_streams(self, test_session, triggered_by):
        captured: dict = {}

        async def fake_process_streams(streams, rules, execution, dry_run,
                                       triggered_by="manual",
                                       event_sync_rules=None):
            captured["rules"] = list(rules)
            captured["event_sync_rules"] = list(event_sync_rules or [])
            return {
                "streams_evaluated": 0, "streams_matched": 0,
                "channels_created": 0, "channels_updated": 0,
                "groups_created": 0, "streams_merged": 0,
                "channels_touched": 0, "streams_skipped": 0,
                "streams_removed": 0, "channels_removed": 0,
                "channels_moved": 0, "pending_merges_added": 0,
                "created_entities": [], "modified_entities": [],
                "dry_run_results": [], "conflicts": [],
                "execution_log": [], "rule_match_counts": {},
                "streams_probed": 0,
            }

        mock_execution = MagicMock()
        mock_execution.id = 1

        with patch("channel_pipeline_engine.get_session", return_value=test_session), \
             patch.object(self.engine, "_fetch_streams", new=AsyncMock(return_value=[])), \
             patch.object(self.engine, "_apply_global_filters", new=AsyncMock(return_value=([], []))), \
             patch.object(self.engine, "_create_execution", new=AsyncMock(return_value=mock_execution)), \
             patch.object(self.engine, "_save_execution", new=AsyncMock()), \
             patch.object(self.engine, "_process_streams", new=fake_process_streams):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True, triggered_by=triggered_by)
            )
        return result, captured

    def test_watermark_run_routes_only_opted_in_event_sync_rules(
        self, test_session
    ):
        """On the watermark trigger, ONLY rules carrying auto_run=true reach
        the attach phase; a non-opted rule keeps current behavior exactly."""
        opted = _make_rule(
            "Opted", event_sync=True,
            es_config=_event_sync_config(auto_run=True),
        )
        not_opted = _make_rule("Not Opted", event_sync=True, priority=1)
        test_session.add_all([opted, not_opted])
        test_session.commit()

        result, captured = self._run_with_captured_process_streams(
            test_session, "m3u_refresh"
        )

        assert result["success"] is True
        assert captured["rules"] == []
        assert [r.name for r in captured["event_sync_rules"]] == ["Opted"]

    def test_manual_run_still_routes_non_opted_rules(self, test_session):
        """The opt-in changes nothing about manual runs: both rules run."""
        opted = _make_rule(
            "Opted", event_sync=True,
            es_config=_event_sync_config(auto_run=True),
        )
        not_opted = _make_rule("Not Opted", event_sync=True, priority=1)
        test_session.add_all([opted, not_opted])
        test_session.commit()

        result, captured = self._run_with_captured_process_streams(
            test_session, "manual"
        )

        assert result["success"] is True
        assert sorted(r.name for r in captured["event_sync_rules"]) == [
            "Not Opted", "Opted",
        ]

    def test_scheduled_trigger_noop_even_with_opt_in(self, test_session):
        """Deny-by-default survives the opt-in: auto_run only ever admits the
        watermark trigger, never 'scheduled' or the unspecified sentinel."""
        rule = _make_rule(
            "Opted", event_sync=True,
            es_config=_event_sync_config(auto_run=True),
        )
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True, triggered_by="scheduled")
            )

        assert result["success"] is True
        assert "manual-run-only" in result["message"]
        self.client.get_streams.assert_not_awaited()


class TestPass4HardBypass:
    """_reconcile_orphans must skip event_sync rules entirely."""

    def setup_method(self):
        self.client = MagicMock()
        self.engine = ChannelPipelineEngine(self.client)

    def _reconcile(self, rule, rule_channel_order, session):
        executor = MagicMock()
        executor._channel_by_id = {}
        execution = MagicMock()
        execution.id = 1
        results = {
            "channels_removed": 0, "channels_moved": 0,
            "dry_run_results": [], "execution_log": [],
        }
        with patch("channel_pipeline_engine.get_session", return_value=session):
            asyncio.get_event_loop().run_until_complete(
                self.engine._reconcile_orphans(
                    [rule], rule_channel_order, executor, execution,
                    results, dry_run=False,
                )
            )
        return results

    def test_event_sync_rule_never_populates_managed_channel_ids(self, test_session):
        """Even when channels were touched this run, an event_sync rule's
        managed_channel_ids stays NULL — Dispatcharr owns those channels.
        This is STRICTER than orphan_action='none', which records the set."""
        rule = _make_rule("Event Rule", event_sync=True)
        rule.orphan_action = "delete"
        test_session.add(rule)
        test_session.commit()

        self._reconcile(rule, {rule.id: [501, 502]}, test_session)

        # _reconcile_orphans commits+closes the session; the detached
        # instance keeps its values (expire_on_commit=False fixture).
        assert rule.managed_channel_ids is None, (
            "Pass 4 populated managed_channel_ids for an event_sync rule — "
            "a later run would reconcile Dispatcharr-owned channels"
        )

    def test_event_sync_rule_orphans_never_cleaned_up(self, test_session):
        """A pre-populated managed set (defensive: should never exist) must
        not trigger deletions for an event_sync rule."""
        rule = _make_rule("Event Rule", event_sync=True)
        rule.orphan_action = "delete"
        rule.set_managed_channel_ids([601, 602])
        test_session.add(rule)
        test_session.commit()

        executor = MagicMock()
        executor._channel_by_id = {601: {"name": "Old 601"}, 602: {"name": "Old 602"}}
        executor.delete_channel = AsyncMock()
        execution = MagicMock()
        execution.id = 1
        results = {
            "channels_removed": 0, "channels_moved": 0,
            "dry_run_results": [], "execution_log": [],
        }
        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            asyncio.get_event_loop().run_until_complete(
                self.engine._reconcile_orphans(
                    [rule], {rule.id: []}, executor, execution,
                    results, dry_run=False,
                )
            )

        assert results["channels_removed"] == 0
        assert results["channels_moved"] == 0
        executor.delete_channel.assert_not_awaited()

    def test_standard_rule_none_action_still_records_managed_ids(self, test_session):
        """Contrast rail: orphan_action='none' on a STANDARD rule keeps its
        existing record-only behavior — proving the event_sync bypass is a
        distinct, stricter path."""
        rule = _make_rule("Standard Rule")
        rule.orphan_action = "none"
        test_session.add(rule)
        test_session.commit()

        self._reconcile(rule, {rule.id: [701]}, test_session)

        assert rule.get_managed_channel_ids() == [701]
