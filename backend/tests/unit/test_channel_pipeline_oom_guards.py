"""
GH #473 OOM cluster — guard tests (bd-sjdsq / bd-exo4j / bd-h2xnl).

Part A (bd-sjdsq): bounded in-memory execution_log.
  - BoundedExecutionLog caps retained entries at N (not 2N) for non-dry-run.
  - Verbose per-stream trace (rules_evaluated) is stripped for non-dry-run,
    retained for dry-run.
  - On truncation a marker + aggregate summary entry is present.

Part B (bd-exo4j): crash-loop guard.
  - Startup crash-sentinel marks status='running' ChannelPipelineExecution rows
    'abandoned' (idempotent; leaves 'completed' rows untouched) and trips the
    persisted circuit-breaker flag.
  - run_auto_creation_after_refresh is SKIPPED when the flag is set, fires when
    clear, and is suppressed by the ECM_DISABLE_RUN_ON_REFRESH break-glass env.

Part C (bd-h2xnl): created-channel cap.
  - BoundedExecutionLog/cap math is exercised in Part A; the soft-abort
    behavior is asserted via _process_streams reaching the cap.
"""
import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channel_pipeline_engine import ChannelPipelineEngine, BoundedExecutionLog


# ---------------------------------------------------------------------------
# Part A — BoundedExecutionLog
# ---------------------------------------------------------------------------
class TestBoundedExecutionLog:
    def _entry(self, sid):
        return {
            "stream_id": sid,
            "stream_name": f"Stream {sid}",
            "m3u_account_id": 1,
            "rules_evaluated": [
                {"rule_id": 1, "rule_name": "r", "conditions": [{"x": 1}], "matched": True}
            ],
            "actions_executed": [{"type": "create_channel", "entity_id": sid, "success": True}],
        }

    def test_caps_retained_entries_non_dry_run(self):
        """2*CAP appends retain exactly CAP entries (plus the summary)."""
        cap = 10
        log = BoundedExecutionLog(cap=cap, verbose=False)
        for i in range(2 * cap):
            log.append(self._entry(i))
        log.finalize()
        # CAP retained + 1 aggregate summary entry
        assert log.retained_count == cap
        assert log.truncated_count == cap
        assert log.truncated is True
        # The materialized list = CAP entries + 1 summary
        assert len(log) == cap + 1
        summary = log[-1]
        assert summary.get("log_truncated") is True
        assert "10 more" in summary["actions_executed"][0]["description"]

    def test_no_summary_when_under_cap(self):
        cap = 10
        log = BoundedExecutionLog(cap=cap, verbose=False)
        for i in range(5):
            log.append(self._entry(i))
        log.finalize()
        assert log.truncated is False
        assert len(log) == 5

    def test_non_dry_run_strips_verbose_trace(self):
        log = BoundedExecutionLog(cap=100, verbose=False)
        log.append(self._entry(1))
        entry = log[0]
        # verbose rules_evaluated dropped; identity + actions retained
        assert entry["rules_evaluated"] == []
        assert entry["stream_id"] == 1
        assert entry["stream_name"] == "Stream 1"
        assert entry["actions_executed"][0]["entity_id"] == 1

    def test_dry_run_retains_full_trace(self):
        log = BoundedExecutionLog(cap=100, verbose=True)
        log.append(self._entry(1))
        entry = log[0]
        assert entry["rules_evaluated"][0]["conditions"] == [{"x": 1}]

    def test_cap_disabled_when_non_positive(self):
        log = BoundedExecutionLog(cap=0, verbose=False)
        for i in range(50):
            log.append(self._entry(i))
        log.finalize()
        assert log.truncated is False
        assert len(log) == 50


# ---------------------------------------------------------------------------
# Part B — crash-loop guard
# ---------------------------------------------------------------------------
class TestCrashSentinel:
    def test_sentinel_marks_running_abandoned_and_trips_breaker(self, test_session):
        from models import ChannelPipelineExecution
        from task_engine import _abandon_orphaned_auto_creation_executions

        running = ChannelPipelineExecution(
            mode="execute", triggered_by="m3u_refresh",
            started_at=datetime.utcnow(), status="running",
        )
        completed = ChannelPipelineExecution(
            mode="execute", triggered_by="manual",
            started_at=datetime.utcnow(), status="completed",
            completed_at=datetime.utcnow(),
        )
        test_session.add_all([running, completed])
        test_session.commit()
        running_id, completed_id = running.id, completed.id

        with patch("config.save_settings") as mock_save, \
             patch("config.get_settings", return_value=MagicMock(auto_creation_run_on_refresh_disabled=False)):
            abandoned = _abandon_orphaned_auto_creation_executions(session=test_session)

        assert abandoned == 1
        test_session.expire_all()
        assert test_session.get(ChannelPipelineExecution, running_id).status == "abandoned"
        assert test_session.get(ChannelPipelineExecution, running_id).completed_at is not None
        assert test_session.get(ChannelPipelineExecution, completed_id).status == "completed"
        # breaker tripped
        mock_save.assert_called_once()

    def test_sentinel_idempotent(self, test_session):
        from models import ChannelPipelineExecution
        from task_engine import _abandon_orphaned_auto_creation_executions

        with patch("config.save_settings"), \
             patch("config.get_settings", return_value=MagicMock(auto_creation_run_on_refresh_disabled=True)):
            n = _abandon_orphaned_auto_creation_executions(session=test_session)
        assert n == 0


class TestRunOnRefreshBreaker:
    """exo4j breaker/break-glass still trips/skips end-to-end through the new
    SINGLE auto-creation path (ADR-011 collapsed ChannelPipelineTask.execute())."""

    def _settings(self, **kw):
        base = dict(
            auto_creation_run_on_refresh_disabled=False,
            last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
            last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
        )
        base.update(kw)
        return MagicMock(**base)

    @pytest.mark.asyncio
    async def test_skipped_when_flag_set(self):
        from tasks.channel_pipeline import ChannelPipelineTask

        with patch.dict(os.environ, {}, clear=False), \
             patch("tasks.channel_pipeline.get_settings",
                   return_value=self._settings(auto_creation_run_on_refresh_disabled=True)), \
             patch("services.notification_service.create_notification_internal", new=AsyncMock()) as mock_notif, \
             patch("journal.log_entry") as mock_journal:
            os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
            # i2xad: scheduled auto-creation is opt-in (default_enabled=False);
            # enable the instance to exercise the post-(a) AUTO-FIRE GUARD path.
            task = ChannelPipelineTask()
            task._enabled = True
            result = await task.execute()

        assert result.success is True
        assert result.details.get("skipped") is True
        mock_notif.assert_awaited()  # operator is told
        mock_journal.assert_called()

    @pytest.mark.asyncio
    async def test_break_glass_env_skips_regardless(self):
        from tasks.channel_pipeline import ChannelPipelineTask

        with patch.dict(os.environ, {"ECM_DISABLE_RUN_ON_REFRESH": "1"}, clear=False), \
             patch("tasks.channel_pipeline.get_settings", return_value=self._settings()), \
             patch("services.notification_service.create_notification_internal", new=AsyncMock()), \
             patch("journal.log_entry"):
            task = ChannelPipelineTask()
            task._enabled = True  # i2xad: opt-in; exercise post-(a) guard
            result = await task.execute()

        assert result.details.get("skipped") is True

    @pytest.mark.asyncio
    async def test_fires_when_flag_clear(self):
        from tasks.channel_pipeline import ChannelPipelineTask

        fake_engine = MagicMock()
        fake_engine.run_pipeline = AsyncMock(return_value={
            "channels_created": 2, "streams_matched": 2, "streams_evaluated": 5,
        })
        session = MagicMock()
        rule = MagicMock(id=1)
        rule.name = "R1"
        session.query.return_value.filter.return_value.all.return_value = [rule]

        with patch.dict(os.environ, {}, clear=False), \
             patch("tasks.channel_pipeline.get_settings", return_value=self._settings()), \
             patch("tasks.channel_pipeline.save_settings"), \
             patch("services.notification_service.create_notification_internal", new=AsyncMock()), \
             patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=fake_engine), \
             patch("tasks.channel_pipeline.get_client", return_value=MagicMock()), \
             patch("database.get_session", return_value=session):
            os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
            task = ChannelPipelineTask()
            task._enabled = True  # i2xad: opt-in; exercise post-(a) guard
            result = await task.execute()

        assert result.details.get("channels_created") == 2
        fake_engine.run_pipeline.assert_awaited_once()


# ---------------------------------------------------------------------------
# Part C — created-channel cap soft-abort (real pipeline)
# ---------------------------------------------------------------------------
class TestCreatedChannelCapSoftAbort:
    """With cap=10 and 50 would-create streams: exactly 10 created, status capped."""

    def _make_engine(self):
        from channel_pipeline_evaluator import StreamContext  # noqa: F401

        client = MagicMock()
        client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        client.get_channel_groups = AsyncMock(return_value=[])

        # Each create returns a distinct channel with a unique id so each
        # counts as a NEW creation (no merge).
        self._next_id = 1000

        async def _create_channel(data):
            self._next_id += 1
            return {"id": self._next_id, "name": data.get("name"),
                    "channel_number": data.get("channel_number"), "streams": data.get("streams", [])}

        client.create_channel = AsyncMock(side_effect=_create_channel)
        client.assign_channel_numbers = AsyncMock()
        return ChannelPipelineEngine(client)

    def _make_rule(self):
        from models import ChannelPipelineRule
        rule = ChannelPipelineRule()
        rule.id = 1
        rule.name = "Create-everything Rule"
        rule.priority = 0
        rule.enabled = True
        rule.m3u_account_id = None
        rule.target_group_id = None
        rule.stop_on_first_match = True
        rule.match_scope_target_group = False
        rule.match_scope_group_id = None
        rule.skip_struck_streams = False
        rule.sort_field = None
        rule.set_conditions([{"type": "always"}])
        rule.set_actions([{"type": "create_channel", "name_template": "{stream_name}", "if_exists": "skip"}])
        return rule

    def _make_streams(self, n):
        from channel_pipeline_evaluator import StreamContext
        return [
            StreamContext(stream_id=1000 + i, stream_name=f"Chan {i}", m3u_account_id=1)
            for i in range(n)
        ]

    def test_cap_soft_aborts_at_ten_of_fifty(self):
        import asyncio
        engine = self._make_engine()
        rule = self._make_rule()
        streams = self._make_streams(50)

        engine._load_rules = AsyncMock(return_value=[rule])
        engine._fetch_streams = AsyncMock(return_value=streams)
        execution = MagicMock(id=1, mode=None)
        engine._create_execution = AsyncMock(return_value=execution)
        engine._save_execution = AsyncMock()
        engine._update_rule_stats = AsyncMock()
        engine._capture_snapshot = AsyncMock()

        fake_settings = MagicMock(
            max_auto_created_channels_per_run=10,
            max_auto_creation_log_entries=500,
            timezone_preference="both",
            default_channel_profile_ids=[],
        )

        with patch("channel_pipeline_engine.get_session", return_value=MagicMock()), \
             patch("channel_pipeline_engine.get_settings", return_value=fake_settings), \
             patch("config.get_settings", return_value=fake_settings):
            result = asyncio.get_event_loop().run_until_complete(
                engine.run_pipeline(dry_run=False)
            )

        assert result["channels_created"] == 10, "cap must stop creation at exactly 10"
        assert result["capped"] is True
        assert result["cap_would_create"] > 0
        # Execution record marked capped (not 'completed').
        assert execution.status == "capped"
        assert execution.error_message and "cap" in execution.error_message.lower()
        # client.create_channel called exactly 10 times — nothing created past the cap.
        assert engine.client.create_channel.await_count == 10
