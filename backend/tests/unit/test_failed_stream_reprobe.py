"""
Unit tests for the FailedStreamReprobeTask.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFailedStreamReprobeTask:
    """Tests for FailedStreamReprobeTask."""

    def _make_task(self):
        from tasks.failed_stream_reprobe import FailedStreamReprobeTask
        return FailedStreamReprobeTask()

    @pytest.mark.asyncio
    async def test_no_failed_streams(self, test_session):
        """Empty query returns success with no probe called."""
        task = self._make_task()
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False
        task.set_prober(mock_prober)

        with patch("tasks.failed_stream_reprobe.get_session", return_value=test_session):
            result = await task.execute()

        assert result.success is True
        assert "No failed streams" in result.message
        assert result.total_items == 0

    @pytest.mark.asyncio
    async def test_reprobes_failed_and_timeout(self, test_session):
        """Mock failed/timeout streams calls probe_all_streams with correct IDs."""
        from models import StreamStats

        # Create failed and timeout stream stats
        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=1))
        test_session.add(StreamStats(stream_id=20, stream_name="s20", probe_status="timeout", consecutive_failures=2))
        test_session.add(StreamStats(stream_id=30, stream_name="s30", probe_status="success", consecutive_failures=0))
        test_session.commit()

        task = self._make_task()

        # Set up mock prober
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = False
        mock_prober.probe_timeout = 30
        mock_prober.max_concurrent_probes = 3
        mock_prober._probe_progress_total = 2
        mock_prober._probe_progress_current = 2
        mock_prober._probe_progress_status = "completed"
        mock_prober._probe_progress_current_stream = ""
        mock_prober._probe_progress_success_count = 2
        mock_prober._probe_progress_failed_count = 0
        mock_prober._probe_progress_skipped_count = 0
        mock_prober._probe_success_streams = [{"id": 10, "name": "s10"}, {"id": 20, "name": "s20"}]
        mock_prober._probe_failed_streams = []

        # Make probe_all_streams return immediately
        async def mock_probe(**kwargs):
            pass
        mock_prober.probe_all_streams = AsyncMock(side_effect=mock_probe)

        task.set_prober(mock_prober)

        with patch("tasks.failed_stream_reprobe.get_session", return_value=test_session):
            result = await task.execute()

        assert result.success is True
        assert result.success_count == 2

        # Verify probe_all_streams was called with failed IDs
        mock_prober.probe_all_streams.assert_called_once()
        call_kwargs = mock_prober.probe_all_streams.call_args[1]
        assert set(call_kwargs["stream_ids_filter"]) == {10, 20}
        assert call_kwargs["skip_m3u_refresh"] is True

    @pytest.mark.asyncio
    async def test_prober_not_initialized(self):
        """Returns error when prober is not set."""
        task = self._make_task()

        result = await task.execute()

        assert result.success is False
        assert "not initialized" in result.message.lower()
        assert result.error == "NOT_INITIALIZED"

    @pytest.mark.asyncio
    async def test_already_running(self):
        """Returns error when a probe is already in progress."""
        task = self._make_task()
        mock_prober = MagicMock()
        mock_prober._probing_in_progress = True
        task.set_prober(mock_prober)

        result = await task.execute()

        assert result.success is False
        assert "already in progress" in result.message.lower()

    @pytest.mark.asyncio
    async def test_validate_config_no_prober(self):
        """validate_config fails when prober is not set."""
        task = self._make_task()
        valid, msg = await task.validate_config()
        assert valid is False
        assert "not initialized" in msg.lower()

    @pytest.mark.asyncio
    async def test_validate_config_with_prober(self):
        """validate_config passes when prober is set."""
        task = self._make_task()
        task.set_prober(MagicMock())
        valid, msg = await task.validate_config()
        assert valid is True

    def test_update_config(self):
        """update_config sets timeout and max_concurrent overrides."""
        task = self._make_task()
        task.update_config({"timeout": 60, "max_concurrent": 10})
        config = task.get_config()
        assert config["timeout"] == 60
        assert config["max_concurrent"] == 10
