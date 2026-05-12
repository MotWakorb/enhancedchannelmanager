"""
Tests for event triggers, debounce behavior, cron evaluation, and webhook notifications.
"""
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from export_models import PlaylistProfile, PublishConfiguration


def _patch_pipeline_db(test_session):
    return patch("publish_pipeline.get_session", return_value=test_session)


def _create_profile(session, **overrides):
    defaults = {
        "name": "Trigger Profile",
        "selection_mode": "all",
        "selected_groups": "[]",
        "selected_channels": "[]",
        "stream_url_mode": "direct",
        "include_logos": True,
        "include_epg_ids": True,
        "include_channel_numbers": True,
        "sort_order": "number",
        "filename_prefix": "playlist",
    }
    defaults.update(overrides)
    p = PlaylistProfile(**defaults)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _create_config(session, profile_id, **overrides):
    defaults = {
        "name": "Trigger Config",
        "profile_id": profile_id,
        "schedule_type": "event",
        "event_triggers": json.dumps(["m3u_refresh"]),
        "enabled": True,
    }
    defaults.update(overrides)
    c = PublishConfiguration(**defaults)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


# =============================================================================
# Debounce behavior
# =============================================================================


class TestDebounce:
    @pytest.mark.asyncio
    async def test_multiple_events_debounce_to_single_publish(self, test_session):
        """Rapid events should debounce to a single publish call."""
        from publish_pipeline import fire_event, _debounce_tasks, DEBOUNCE_SECONDS

        profile = _create_profile(test_session)
        _create_config(test_session, profile.id)

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock) as mock_exec:
            # Fire 3 rapid events
            await fire_event("m3u_refresh")
            await fire_event("m3u_refresh")
            await fire_event("m3u_refresh")

            # Should have exactly one debounce task (latest replaces earlier)
            assert len(_debounce_tasks) == 1

            # Clean up
            for task in _debounce_tasks.values():
                task.cancel()
            _debounce_tasks.clear()

    @pytest.mark.asyncio
    async def test_different_configs_debounce_independently(self, test_session):
        """Different configs should have independent debounce timers."""
        from publish_pipeline import fire_event, _debounce_tasks

        profile = _create_profile(test_session)
        _create_config(test_session, profile.id, name="Config A")
        _create_config(test_session, profile.id, name="Config B",
                       event_triggers=json.dumps(["m3u_refresh"]))

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock):
            await fire_event("m3u_refresh")
            assert len(_debounce_tasks) == 2  # One per config

            for task in _debounce_tasks.values():
                task.cancel()
            _debounce_tasks.clear()


# =============================================================================
# Event type filtering
# =============================================================================


class TestEventTypeFiltering:
    @pytest.mark.asyncio
    async def test_only_matching_triggers_fire(self, test_session):
        """Only configs with matching event triggers should be scheduled."""
        from publish_pipeline import fire_event, _debounce_tasks

        profile = _create_profile(test_session)
        _create_config(test_session, profile.id, name="M3U Config",
                       event_triggers=json.dumps(["m3u_refresh"]))
        _create_config(test_session, profile.id, name="EPG Config",
                       event_triggers=json.dumps(["epg_refresh"]))

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock):
            await fire_event("epg_refresh")
            # Only EPG Config should be debounced
            assert len(_debounce_tasks) == 1

            for task in _debounce_tasks.values():
                task.cancel()
            _debounce_tasks.clear()

    @pytest.mark.asyncio
    async def test_disabled_configs_skipped(self, test_session):
        """Disabled configs should not be triggered by events."""
        from publish_pipeline import fire_event, _debounce_tasks

        profile = _create_profile(test_session)
        _create_config(test_session, profile.id, enabled=False)

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock):
            await fire_event("m3u_refresh")
            assert len(_debounce_tasks) == 0

    @pytest.mark.asyncio
    async def test_invalid_event_type_ignored(self, test_session):
        """Invalid event types should be silently ignored."""
        from publish_pipeline import fire_event, _debounce_tasks
        await fire_event("invalid_event")
        assert len(_debounce_tasks) == 0

    @pytest.mark.asyncio
    async def test_multi_trigger_config(self, test_session):
        """Config with multiple triggers responds to any matching event."""
        from publish_pipeline import fire_event, _debounce_tasks

        profile = _create_profile(test_session)
        _create_config(test_session, profile.id,
                       event_triggers=json.dumps(["m3u_refresh", "epg_refresh"]))

        with _patch_pipeline_db(test_session), \
             patch("publish_pipeline.execute_publish", new_callable=AsyncMock):
            await fire_event("epg_refresh")
            assert len(_debounce_tasks) == 1

            for task in _debounce_tasks.values():
                task.cancel()
            _debounce_tasks.clear()


# =============================================================================
# Cron evaluation
# =============================================================================


class TestCronEvaluation:
    @pytest.mark.asyncio
    async def test_cron_within_window_triggers(self, test_session):
        """Cron configs within the 60-second window should trigger."""
        from tasks.export_publish import ExportPublishTask
        from croniter import croniter

        profile = _create_profile(test_session)
        now = datetime.utcnow()
        # Create a cron expression that matches now
        cron_expr = f"{now.minute} {now.hour} * * *"

        _create_config(test_session, profile.id,
                       schedule_type="cron",
                       cron_expression=cron_expr)

        with patch("tasks.export_publish.get_session", return_value=test_session), \
             patch("tasks.export_publish.execute_publish", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = MagicMock(success=True)
            task = ExportPublishTask()
            result = await task.execute()
            assert result.success is True
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_cron_outside_window_skipped(self, test_session):
        """Cron configs outside the 60-second window should not trigger."""
        from tasks.export_publish import ExportPublishTask

        profile = _create_profile(test_session)
        now = datetime.utcnow()
        # Use a time that's definitely not now
        future_minute = (now.minute + 30) % 60
        cron_expr = f"{future_minute} {now.hour} * * *"

        _create_config(test_session, profile.id,
                       schedule_type="cron",
                       cron_expression=cron_expr)

        with patch("tasks.export_publish.get_session", return_value=test_session), \
             patch("tasks.export_publish.execute_publish", new_callable=AsyncMock) as mock_exec:
            task = ExportPublishTask()
            result = await task.execute()
            assert result.success is True
            mock_exec.assert_not_called()


# =============================================================================
# Webhook notifications
# =============================================================================


class TestWebhookNotifications:
    @pytest.mark.asyncio
    async def test_webhook_sent_on_success(self):
        """Webhook should be sent with publish result on success."""
        from publish_pipeline import _send_webhook, PublishResult

        config = {"name": "Test", "webhook_url": "https://hooks.example.com/pub"}
        result = PublishResult(success=True, channels_count=42, duration_ms=500)

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_resp = MagicMock(status_code=200)
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            await _send_webhook(config, result)
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
            assert payload["success"] is True
            assert payload["channels_count"] == 42

    @pytest.mark.asyncio
    async def test_webhook_skipped_without_url(self):
        """No webhook should be sent when URL is None."""
        from publish_pipeline import _send_webhook, PublishResult

        config = {"name": "Test", "webhook_url": None}
        result = PublishResult(success=True)
        # Should complete without error or HTTP calls
        await _send_webhook(config, result)

    @pytest.mark.asyncio
    async def test_webhook_retries_on_failure(self):
        """Webhook should retry once on failure."""
        from publish_pipeline import _send_webhook, PublishResult

        config = {"name": "Test", "webhook_url": "https://hooks.example.com/pub"}
        result = PublishResult(success=False, error="Generation failed")

        with patch("httpx.AsyncClient") as MockClient, \
             patch("publish_pipeline.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_resp = MagicMock(status_code=500)
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            await _send_webhook(config, result)
            # Should have been called twice (initial + 1 retry)
            assert mock_client.post.call_count == 2
