"""bd-0hjrk.7 + bd-0hjrk.8 — consolidated run-summary regression guard.

These pin field semantics that bd-0emgo.4 established (merge -> streams_merged,
channels_touched persisted, MCP formatter) plus the two NEW invariants the bead
calls out, and the bd-0hjrk.8 match_by no-op guard.

Part B (bd-0hjrk.7) — run-summary field semantics:
  (a) merge_stream results count as streams_merged, NEVER channels_updated.
  (b) INVARIANT: channels_touched <= streams_merged after a run merging N
      streams into M (M<=N) distinct channels — the explicit inequality is the
      new value over 0emgo.4's equality-on-a-fixed-scenario tests.
  (c) a property-only update (logo/tvg/epg/number) increments channels_updated,
      NOT streams_merged.
  (d) execution_id is present in BOTH a dry_run and a real run result.

Part C (bd-0hjrk.8, test-only slice) — match_by no-op:
  Two merge_streams actions identical except for match_by ("tvg_id" vs
  "normalized_name") resolve to the SAME target; loose_name_match is the toggle
  that actually changes matching.

Fixtures mirror the existing 0emgo.4 tests in
``test_channel_pipeline_executor.py`` (ExecutionContext.add_result) and
``test_channel_pipeline_engine.py::TestRunPipelineCreateChannelMergeChannelsTouched``
(real-pipeline create+merge dry-run/live driver) so engine scaffolding is not
rebuilt from scratch.
"""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from channel_pipeline_executor import ActionResult, ExecutionContext, ActionExecutor
from channel_pipeline_evaluator import StreamContext
from channel_pipeline_engine import ChannelPipelineEngine


def _merge_result(entity_id, name="ESPN"):
    return ActionResult(
        success=True,
        action_type="merge_stream",
        description=f"Added stream to {name}",
        entity_type="channel",
        entity_id=entity_id,
        entity_name=name,
        modified=True,
    )


def _property_result(action_type, entity_id=1, name="ESPN"):
    return ActionResult(
        success=True,
        action_type=action_type,
        description=f"Updated {name} via {action_type}",
        entity_type="channel",
        entity_id=entity_id,
        entity_name=name,
        modified=True,
    )


class TestRunSummaryFieldSemantics:
    """Part B (a), (c): merge vs property-only attribution at the chokepoint."""

    def test_merge_counts_as_streams_merged_never_channels_updated(self):
        """(a) merge_stream -> streams_merged, and channels_updated stays 0."""
        ctx = ExecutionContext()
        for i in range(25):
            ctx.add_result(_merge_result(entity_id=(i % 5) + 1))

        assert ctx.streams_merged == 25
        assert ctx.channels_updated == 0, (
            "merge_stream results must NEVER be counted as channels_updated"
        )

    def test_property_only_update_counts_as_channels_updated_not_streams_merged(self):
        """(c) logo/tvg/epg/number updates -> channels_updated, not streams_merged."""
        ctx = ExecutionContext()
        for action_type in ("assign_logo", "assign_tvg_id", "assign_epg",
                            "set_channel_number"):
            ctx.add_result(_property_result(action_type))

        assert ctx.channels_updated == 4
        assert ctx.streams_merged == 0, (
            "property-only updates must NOT inflate streams_merged"
        )

    def test_mixed_run_attributes_each_kind_separately(self):
        """A run with both merges and property updates keeps the buckets distinct."""
        ctx = ExecutionContext()
        # 3 merges into 2 distinct channels.
        ctx.add_result(_merge_result(entity_id=1))
        ctx.add_result(_merge_result(entity_id=1))
        ctx.add_result(_merge_result(entity_id=2))
        # 2 property-only updates.
        ctx.add_result(_property_result("assign_logo", entity_id=3))
        ctx.add_result(_property_result("assign_epg", entity_id=4))

        assert ctx.streams_merged == 3
        assert ctx.channels_updated == 2
        # channels_touched is derived from this set (engine unions across streams).
        assert len(ctx.merged_channel_ids) == 2


class TestChannelsTouchedInvariant:
    """Part B (b): channels_touched <= streams_merged after a real merge run."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Create+Merge Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([{
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }])
        return rule

    def _make_streams(self, names_to_ids):
        return [
            StreamContext(stream_id=sid, stream_name=name, m3u_account_id=1)
            for name, sid in names_to_ids
        ]

    def _run(self, rule, streams, dry_run):
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.engine._create_execution = AsyncMock(return_value=MagicMock(id=1, mode=None))
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()

        with patch("channel_pipeline_engine.get_session") as mock_get_session:
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_channels_touched_le_streams_merged_dry_run(self):
        """N streams merged into M<=N distinct channels => channels_touched <= streams_merged.

        ESPN x3 -> 2 merged; CNN x2 -> 1 merged; FOX x1 -> 0 merged.
        => streams_merged == 3 across M == 2 distinct channels (ESPN, CNN).
        """
        rule = self._make_rule()
        streams = self._make_streams([
            ("ESPN", 601), ("ESPN", 602), ("ESPN", 603),
            ("CNN", 701), ("CNN", 702),
            ("FOX", 801),
        ])

        result = self._run(rule, streams, dry_run=True)

        merged = result["streams_merged"]
        touched = result["channels_touched"]
        assert merged == 3
        assert touched == 2
        assert touched <= merged, (
            f"INVARIANT VIOLATED: channels_touched ({touched}) must be "
            f"<= streams_merged ({merged}) — a channel cannot be 'touched by a "
            f"merge' more times than there were merges"
        )

    def test_channels_touched_le_streams_merged_live(self):
        """Same invariant holds on the live create+merge path."""
        self._next_live_id = iter(range(3000, 4000))
        self.client.create_channel = AsyncMock(
            side_effect=lambda data: {
                "id": next(self._next_live_id),
                "name": data["name"],
                "channel_number": data.get("channel_number"),
                "channel_group_id": data.get("channel_group_id"),
                "streams": data.get("streams", []),
            }
        )
        rule = self._make_rule()
        streams = self._make_streams([
            ("ESPN", 901), ("ESPN", 902), ("ESPN", 903),
            ("CNN", 911), ("CNN", 912),
            ("FOX", 921),
        ])

        with patch("channel_pipeline_executor.journal.log_entries"):
            result = self._run(rule, streams, dry_run=False)

        merged = result["streams_merged"]
        touched = result["channels_touched"]
        assert merged == 3
        assert touched == 2
        assert touched <= merged, (
            f"INVARIANT VIOLATED (live): channels_touched ({touched}) "
            f"must be <= streams_merged ({merged})"
        )


class TestExecutionIdPresentBothModes:
    """Part B (d): execution_id is returned for BOTH dry_run and real runs.

    FINDING (run_pipeline, backend/channel_pipeline_engine.py): whenever there is
    at least one enabled rule to process, the engine creates an
    ChannelPipelineExecution row (mode="dry_run"/"execute") and returns
    ``execution_id=execution.id`` for both modes (lines ~169-172, ~230). The
    only path that returns NO execution_id is the no-enabled-rules early return
    (no row is created there) — an expected no-op, not a dry_run gap. So
    execution_id is genuinely present in both real-work modes; nothing is faked.
    """

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.update_channel = AsyncMock()
        self.engine = ChannelPipelineEngine(self.client)

    def _make_rule(self):
        from models import ChannelPipelineRule

        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Create+Merge Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([{
            "type": "create_channel",
            "name_template": "{stream_name}",
            "if_exists": "merge",
        }])
        return rule

    def _run(self, dry_run):
        rule = self._make_rule()
        streams = [StreamContext(stream_id=601, stream_name="ESPN", m3u_account_id=1)]
        # The real execution id the row would carry — assert it is threaded out.
        self.engine._load_rules = AsyncMock(return_value=[rule])
        self.engine._fetch_streams = AsyncMock(return_value=streams)
        self.engine._create_execution = AsyncMock(
            return_value=MagicMock(id=4242, mode="dry_run" if dry_run else "execute")
        )
        self.engine._save_execution = AsyncMock()
        self.engine._update_rule_stats = AsyncMock()

        with patch("channel_pipeline_engine.get_session") as mock_get_session, \
                patch("channel_pipeline_executor.journal.log_entries"):
            mock_get_session.return_value = MagicMock()
            return asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=dry_run)
            )

    def test_execution_id_present_dry_run(self):
        result = self._run(dry_run=True)
        assert "execution_id" in result
        assert result["execution_id"] == 4242

    def test_execution_id_present_real_run(self):
        result = self._run(dry_run=False)
        assert "execution_id" in result
        assert result["execution_id"] == 4242


class TestMatchByIsNoOp:
    """Part C (bd-0hjrk.8): match_by is a documented no-op; loose_name_match is
    the real matching toggle.

    Two merge_streams actions identical except for match_by ("tvg_id" vs
    "normalized_name") must resolve to the SAME target channel — match_by never
    changes the resolved target. Then loose_name_match flipped on shows it IS
    the toggle that changes matching (restores the fuzzy cascade).

    (There is no exact_match/match_strict param — those do not exist.)
    """

    def _make_engine(self, core_map):
        engine = MagicMock()

        def _normalize(name, *args, **kwargs):
            result = MagicMock()
            result.normalized = core_map.get(name, name)
            result.transformations = []
            return result

        engine.normalize.side_effect = _normalize
        engine.extract_core_name.side_effect = lambda n: core_map.get(n, n)
        engine.extract_call_sign.return_value = None
        return engine

    def _build(self):
        client = MagicMock()
        client.update_channel = AsyncMock(return_value={})
        existing = [{"id": 50, "name": "SKY Sport 4K", "streams": [],
                     "channel_group_id": 9, "auto_created": True}]
        core_map = {
            "SKY Sport 4K": "sky sport",
            "Sky Sport Bundesliga": "sky sport bundesliga",
        }
        engine = self._make_engine(core_map)
        executor = ActionExecutor(client, existing_channels=existing,
                                  normalization_engine=engine)
        return client, executor

    def _stream(self, sid, name):
        return StreamContext(
            stream_id=sid,
            stream_name=name,
            m3u_account_id=1,
            m3u_account_name="Provider",
            group_name="Sports",
            tvg_id=None,
        )

    def _merge(self, executor, stream_ctx, match_by, loose=False):
        action = {"type": "merge_streams", "target": "auto", "match_by": match_by}
        if loose:
            action["loose_name_match"] = True
        return asyncio.get_event_loop().run_until_complete(
            executor.execute(action, stream_ctx, ExecutionContext(),
                             normalization_group_ids=[1])
        )

    def test_match_by_does_not_change_resolved_target_exact_case(self):
        """An EXACT-name match resolves the SAME way under both match_by values."""
        # Stream whose normalized/core name equals the channel core ("sky sport").
        client_a, executor_a = self._build()
        result_tvg = self._merge(executor_a, self._stream(401, "SKY Sport 4K"),
                                 match_by="tvg_id")

        client_b, executor_b = self._build()
        result_norm = self._merge(executor_b, self._stream(401, "SKY Sport 4K"),
                                  match_by="normalized_name")

        # Both succeed and resolve to the SAME channel (id=50) — match_by inert.
        assert result_tvg.success is True
        assert result_norm.success is True
        assert client_a.update_channel.call_args[0][0] == 50
        assert client_b.update_channel.call_args[0][0] == 50
        assert (client_a.update_channel.call_args[0][0]
                == client_b.update_channel.call_args[0][0]), (
            "match_by must NOT change the resolved merge target"
        )

    def test_match_by_does_not_change_resolved_target_no_match_case(self):
        """A non-exact stream is SKIPPED under both match_by values (no fuzzy)."""
        client_a, executor_a = self._build()
        result_tvg = self._merge(executor_a, self._stream(501, "Sky Sport Bundesliga"),
                                 match_by="tvg_id")

        client_b, executor_b = self._build()
        result_norm = self._merge(executor_b, self._stream(501, "Sky Sport Bundesliga"),
                                  match_by="normalized_name")

        assert result_tvg.skipped is True
        assert result_norm.skipped is True
        client_a.update_channel.assert_not_called()
        client_b.update_channel.assert_not_called()

    def test_loose_name_match_is_the_real_toggle(self):
        """loose_name_match=True (NOT match_by) is what changes matching: the
        'Sky Sport Bundesliga' stream that was skipped now merges into id=50."""
        # Default (loose off): skipped — proven above.
        client_off, executor_off = self._build()
        result_off = self._merge(executor_off, self._stream(601, "Sky Sport Bundesliga"),
                                 match_by="tvg_id", loose=False)
        assert result_off.skipped is True
        client_off.update_channel.assert_not_called()

        # loose on: same stream, same match_by, now merges (fuzzy cascade).
        client_on, executor_on = self._build()
        result_on = self._merge(executor_on, self._stream(601, "Sky Sport Bundesliga"),
                                match_by="tvg_id", loose=True)
        assert result_on.success is True
        client_on.update_channel.assert_called()
        assert client_on.update_channel.call_args[0][0] == 50
