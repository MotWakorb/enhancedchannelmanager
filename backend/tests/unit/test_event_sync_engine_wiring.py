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
               priority: int = 0) -> ChannelPipelineRule:
    return ChannelPipelineRule(
        name=name,
        enabled=enabled,
        priority=priority,
        conditions=json.dumps([{"type": "always"}]),
        actions=json.dumps([{"type": "skip"}]),
        event_sync_config=(
            json.dumps(_event_sync_config()) if event_sync else None
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

    def test_only_event_sync_rules_returns_preview_only_message(self, test_session):
        """A run scoped ONLY to event_sync rules is an explicit no-op that
        says WHY, not a silent 'no rules' shrug."""
        rule = _make_rule("Event Rule", event_sync=True)
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True)
            )

        assert result["success"] is True
        assert "preview-only" in result["message"]
        assert result["streams_evaluated"] == 0
        assert result["streams_matched"] == 0

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
                                       triggered_by="manual"):
            captured["rules"] = list(rules)
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
                self.engine.run_pipeline(dry_run=True)
            )

        assert result["success"] is True
        rule_names = [r.name for r in captured["rules"]]
        assert rule_names == ["Standard Rule"], (
            "event_sync rule leaked into Pass 1/2 evaluation"
        )


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
