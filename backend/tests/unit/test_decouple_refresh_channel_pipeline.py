"""ADR-011 (bd-ka7j9) — decouple M3U refresh from auto-creation (event-driven).

Phase 2 follow-up to the exo4j circuit-breaker. M3U refresh no longer
hard-chains auto-creation as a side-effect; instead:

  - A SUCCESSFUL M3U refresh advances the ``last_m3u_refresh_completed_at``
    watermark in settings (Q1: on EVERY successful refresh, change-gated NO).
  - The interval-scheduled ``ChannelPipelineTask`` decides FOR ITSELF whether to
    run via a top-of-run AUTO-FIRE GUARD: enabled AND breaker clear AND
    >=1 enabled+run_on_refresh rule AND refresh watermark newer than the
    consumed watermark. On run it advances the consumed watermark and runs
    ONLY ``enabled AND run_on_refresh=True`` rules (Q2).
  - The manual pipeline ``/run`` path stays UN-gated (covered elsewhere).

These tests assert the decoupling itself: the refresh task no longer invokes
auto-creation, the watermark advances, and the auto-fire guard fires iff all
four conditions hold.
"""
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_scheduler import ScheduleType


# ---------------------------------------------------------------------------
# Step 1 — M3U refresh advances the watermark and NO LONGER calls auto-creation
# ---------------------------------------------------------------------------
def _make_refresh_task():
    from tasks.m3u_refresh import M3URefreshTask
    task = M3URefreshTask()
    task.account_ids = []
    task.skip_inactive = True
    return task


@pytest.mark.asyncio
async def test_refresh_advances_watermark_on_success():
    """A successful refresh advances last_m3u_refresh_completed_at via save_settings."""
    account = {"id": 1, "name": "Prov", "is_active": True}

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.get_m3u_account = AsyncMock(side_effect=[
        {"id": 1, "updated_at": "2026-01-01T00:00:00Z", "channel_groups": []},
        {"id": 1, "updated_at": "2026-01-02T00:00:00Z", "channel_groups": []},
    ])

    settings = MagicMock(last_m3u_refresh_completed_at="")

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.get_settings", return_value=settings), \
         patch("tasks.m3u_refresh.save_settings") as mock_save:
        task = _make_refresh_task()
        result = await task.execute()

    assert result.success is True
    # Watermark was advanced and persisted.
    mock_save.assert_called_once()
    assert settings.last_m3u_refresh_completed_at != ""


@pytest.mark.asyncio
async def test_refresh_does_not_invoke_auto_creation():
    """The hard chain is gone: refresh never calls run_auto_creation_after_refresh
    nor the auto-creation engine."""
    account = {"id": 1, "name": "Prov", "is_active": True}

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.get_m3u_account = AsyncMock(side_effect=[
        {"id": 1, "updated_at": "2026-01-01T00:00:00Z", "channel_groups": []},
        {"id": 1, "updated_at": "2026-01-02T00:00:00Z", "channel_groups": []},
    ])

    settings = MagicMock(last_m3u_refresh_completed_at="")

    # tasks.m3u_refresh must not even import the auto-creation module.
    import tasks.m3u_refresh as m3u_mod
    src = open(m3u_mod.__file__).read()
    assert "run_auto_creation_after_refresh" not in src, (
        "m3u_refresh must not reference run_auto_creation_after_refresh"
    )
    assert "from tasks.channel_pipeline import" not in src, (
        "m3u_refresh must not import from tasks.channel_pipeline"
    )

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.get_settings", return_value=settings), \
         patch("tasks.m3u_refresh.save_settings"):
        task = _make_refresh_task()
        result = await task.execute()

    assert result.success is True


@pytest.mark.asyncio
async def test_refresh_with_failures_does_not_advance_watermark():
    """A run where the account refresh raises must NOT advance the watermark."""
    account = {"id": 1, "name": "Prov", "is_active": True}

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[account])
    client.get_channel_groups = AsyncMock(return_value=[{"id": 1, "name": "G"}])
    client.refresh_m3u_account = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_m3u_account = AsyncMock(return_value={"id": 1, "channel_groups": []})

    settings = MagicMock(last_m3u_refresh_completed_at="")

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock(return_value=None)), \
         patch("tasks.m3u_refresh.get_settings", return_value=settings), \
         patch("tasks.m3u_refresh.save_settings") as mock_save:
        task = _make_refresh_task()
        await task.execute()

    # No fully-successful account -> watermark not advanced.
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Step 2 — ChannelPipelineTask interval schedule + auto-fire guard
# ---------------------------------------------------------------------------
def test_auto_creation_task_has_interval_schedule():
    """The default ChannelPipelineTask schedule is INTERVAL ~60s so the engine
    ticks it (the guard then decides whether to actually run)."""
    from tasks.channel_pipeline import ChannelPipelineTask
    from task_engine import DEFAULT_CHECK_INTERVAL

    task = ChannelPipelineTask()
    assert task.schedule_config.schedule_type == ScheduleType.INTERVAL
    assert task.schedule_config.interval_seconds == DEFAULT_CHECK_INTERVAL


def _patch_settings(**kwargs):
    base = dict(
        auto_creation_run_on_refresh_disabled=False,
        last_m3u_refresh_completed_at="",
        last_auto_creation_consumed_refresh_at="",
    )
    base.update(kwargs)
    return MagicMock(**base)


async def _run_autofire(settings, rules, engine_result=None, env=None):
    """Run ChannelPipelineTask.execute() with the given settings + run_on_refresh rules.

    Returns (result, engine_mock, save_settings_mock).
    """
    from tasks.channel_pipeline import ChannelPipelineTask

    fake_engine = MagicMock()
    fake_engine.run_pipeline = AsyncMock(
        return_value=engine_result or {
            "channels_created": 0, "channels_updated": 0,
            "streams_matched": 0, "streams_evaluated": 0,
        }
    )

    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = rules
    session.query.return_value.filter.return_value.count.return_value = len(rules)

    env = env or {}
    with patch.dict(os.environ, env, clear=False), \
         patch("tasks.channel_pipeline.get_settings", return_value=settings), \
         patch("tasks.channel_pipeline.save_settings") as mock_save, \
         patch("services.notification_service.create_notification_internal", new=AsyncMock()), \
         patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=fake_engine), \
         patch("channel_pipeline_engine.init_channel_pipeline_engine", new=AsyncMock(return_value=fake_engine)), \
         patch("dispatcharr_client.get_client", return_value=MagicMock()), \
         patch("tasks.channel_pipeline.get_client", return_value=MagicMock()), \
         patch("database.get_session", return_value=session), \
         patch("journal.log_entry"):
        if "ECM_DISABLE_RUN_ON_REFRESH" not in env:
            os.environ.pop("ECM_DISABLE_RUN_ON_REFRESH", None)
        task = ChannelPipelineTask()
        # i2xad: scheduled auto-creation is opt-in (default_enabled=False). These
        # tests exercise AUTO-FIRE GUARD conditions (b)/(c)/(d), which only apply
        # once condition (a) "enabled" holds — i.e. after an operator opts in.
        task._enabled = True
        result = await task.execute()

    return result, fake_engine, mock_save


def _rule(rid=1, name="R1"):
    r = MagicMock(id=rid)
    r.name = name
    return r


@pytest.mark.asyncio
async def test_autofire_runs_when_all_conditions_hold():
    """Enabled + breaker clear + run_on_refresh rule + watermark newer -> runs,
    advances the consumed watermark, runs only run_on_refresh rules."""
    settings = _patch_settings(
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    result, engine, mock_save = await _run_autofire(
        settings, rules=[_rule()],
        engine_result={"channels_created": 3, "channels_updated": 0,
                       "streams_matched": 3, "streams_evaluated": 5},
    )

    assert result.success is True
    engine.run_pipeline.assert_awaited_once()
    # Ran only the run_on_refresh rule set.
    kwargs = engine.run_pipeline.call_args.kwargs
    assert kwargs["rule_ids"] == [1]
    assert kwargs["dry_run"] is False
    # Consumed watermark advanced to the refresh watermark and persisted.
    assert settings.last_auto_creation_consumed_refresh_at == "2026-01-02T00:00:00+00:00"
    mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_autofire_skips_when_breaker_set():
    settings = _patch_settings(
        auto_creation_run_on_refresh_disabled=True,
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    result, engine, mock_save = await _run_autofire(settings, rules=[_rule()])
    engine.run_pipeline.assert_not_awaited()
    # Watermark NOT consumed when suppressed.
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_autofire_skips_when_break_glass_env_set():
    settings = _patch_settings(
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    result, engine, mock_save = await _run_autofire(
        settings, rules=[_rule()], env={"ECM_DISABLE_RUN_ON_REFRESH": "1"}
    )
    engine.run_pipeline.assert_not_awaited()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_autofire_skips_when_no_run_on_refresh_rules():
    settings = _patch_settings(
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    result, engine, mock_save = await _run_autofire(settings, rules=[])
    engine.run_pipeline.assert_not_awaited()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_autofire_skips_when_watermark_not_newer():
    """No new refresh since the last consumed watermark -> nothing to do."""
    settings = _patch_settings(
        last_m3u_refresh_completed_at="2026-01-01T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="2026-01-01T00:00:00+00:00",
    )
    result, engine, mock_save = await _run_autofire(settings, rules=[_rule()])
    engine.run_pipeline.assert_not_awaited()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_autofire_skips_when_no_refresh_yet():
    """Empty refresh watermark (never refreshed) -> nothing to do."""
    settings = _patch_settings(
        last_m3u_refresh_completed_at="",
        last_auto_creation_consumed_refresh_at="",
    )
    result, engine, mock_save = await _run_autofire(settings, rules=[_rule()])
    engine.run_pipeline.assert_not_awaited()
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_autofire_first_ever_refresh_fires():
    """First refresh ever (consumed empty, refresh set) -> fires once."""
    settings = _patch_settings(
        last_m3u_refresh_completed_at="2026-01-02T00:00:00+00:00",
        last_auto_creation_consumed_refresh_at="",
    )
    result, engine, mock_save = await _run_autofire(settings, rules=[_rule()])
    engine.run_pipeline.assert_awaited_once()
    assert settings.last_auto_creation_consumed_refresh_at == "2026-01-02T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Step 2 — no double-fire across overlapping ~60s ticks (engine guard)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_double_fire_when_already_running():
    """The engine's "already running" guard prevents an overlapping tick from
    starting a second auto_creation run while one is in flight."""
    from task_engine import TaskEngine

    engine = TaskEngine()
    # Simulate a run already in flight for the auto_creation task.
    engine._active_tasks.add("auto_creation")

    result = await engine._execute_task("auto_creation", triggered_by="scheduled")

    assert result is not None
    assert result.error == "ALREADY_RUNNING"
    assert result.success is False
