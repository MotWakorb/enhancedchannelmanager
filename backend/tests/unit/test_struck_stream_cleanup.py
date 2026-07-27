"""
Unit tests for the StruckStreamCleanupTask.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestStruckStreamCleanupTask:
    """Tests for StruckStreamCleanupTask."""

    def _make_task(self):
        from tasks.struck_stream_cleanup import StruckStreamCleanupTask
        return StruckStreamCleanupTask()

    @pytest.mark.asyncio
    async def test_threshold_disabled(self):
        """threshold=0 returns success with no removal."""
        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 0

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings):
            result = await task.execute()

        assert result.success is True
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_struck_streams(self, test_session):
        """No streams above threshold returns success."""
        from models import StreamStats

        # All streams below threshold
        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=1))
        test_session.add(StreamStats(stream_id=20, stream_name="s20", probe_status="success", consecutive_failures=0))
        test_session.commit()

        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings), \
             patch("tasks.struck_stream_cleanup.get_session", return_value=test_session):
            result = await task.execute()

        assert result.success is True
        assert "No struck-out streams" in result.message

    @pytest.mark.asyncio
    async def test_removes_struck_streams(self, test_session):
        """Mock struck streams in channels — removes them, resets failures."""
        from models import StreamStats

        # Create struck streams
        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=3))
        test_session.add(StreamStats(stream_id=20, stream_name="s20", probe_status="timeout", consecutive_failures=5))
        test_session.add(StreamStats(stream_id=30, stream_name="s30", probe_status="success", consecutive_failures=0))
        test_session.commit()

        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        # Mock client with channels containing struck streams
        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(return_value={
            "results": [
                {"id": 1, "name": "CH1", "streams": [10, 20, 30]},
                {"id": 2, "name": "CH2", "streams": [20, 40]},
                {"id": 3, "name": "CH3", "streams": [50, 60]},
            ],
            "count": 3,
        })
        mock_client.update_channel = AsyncMock()

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings), \
             patch("tasks.struck_stream_cleanup.get_session", return_value=test_session), \
             patch("tasks.struck_stream_cleanup.get_client", return_value=mock_client):
            result = await task.execute()

        assert result.success is True
        assert result.success_count == 3  # 2 from CH1 + 1 from CH2

        # Verify channels were updated correctly
        assert mock_client.update_channel.call_count == 2
        # CH1: removed 10 and 20, kept 30
        mock_client.update_channel.assert_any_call(1, {"streams": [30]})
        # CH2: removed 20, kept 40
        mock_client.update_channel.assert_any_call(2, {"streams": [40]})

        # Verify consecutive_failures was reset
        s10 = test_session.query(StreamStats).filter_by(stream_id=10).first()
        s20 = test_session.query(StreamStats).filter_by(stream_id=20).first()
        assert s10.consecutive_failures == 0
        assert s20.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_handles_api_error(self, test_session):
        """Client error during channel update returns failure details."""
        from models import StreamStats

        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=5))
        test_session.commit()

        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(return_value={
            "results": [
                {"id": 1, "name": "CH1", "streams": [10, 20]},
            ],
            "count": 1,
        })
        mock_client.update_channel = AsyncMock(side_effect=Exception("API error"))

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings), \
             patch("tasks.struck_stream_cleanup.get_session", return_value=test_session), \
             patch("tasks.struck_stream_cleanup.get_client", return_value=mock_client):
            result = await task.execute()

        assert result.success is False
        assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_outer_exception_handler_on_channel_fetch_failure(self, test_session):
        """A failure fetching channels hits the OUTER handler → success=False + error.

        u8qr6.4: the existing test_handles_api_error only exercises the INNER
        per-channel ``update_channel`` failure path. Nothing covered the outer
        try/except (lines 212-220) that wraps the whole fetch/pagination/removal
        block. Here ``get_channels`` itself raises, so the pagination loop blows
        up before any per-channel work and the outer handler must convert it into
        a failed TaskResult carrying the error string — not propagate the
        exception out of execute().
        """
        from models import StreamStats

        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=5))
        test_session.commit()

        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(side_effect=RuntimeError("Dispatcharr unreachable"))
        mock_client.update_channel = AsyncMock()

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings), \
             patch("tasks.struck_stream_cleanup.get_session", return_value=test_session), \
             patch("tasks.struck_stream_cleanup.get_client", return_value=mock_client):
            result = await task.execute()

        assert result.success is False
        assert "failed" in result.message.lower()
        assert result.error == "Dispatcharr unreachable"
        # The fetch blew up before any channel could be mutated.
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_page_pagination_fetches_all_channels(self, test_session):
        """The pagination loop fetches every page and removes struck streams across pages.

        u8qr6.4: all prior tests returned a single page (all_channels >= count
        after page 1), so the multi-page loop (lines 107-124) was never taken.
        Here page 1 does not yet cover ``count``, forcing a second fetch; assert
        both pages are requested (page=1 then page=2) and that struck streams on
        BOTH pages are removed.
        """
        from models import StreamStats

        test_session.add(StreamStats(stream_id=10, stream_name="s10", probe_status="failed", consecutive_failures=5))
        test_session.commit()

        task = self._make_task()

        mock_settings = MagicMock()
        mock_settings.strike_threshold = 3

        page1 = {
            "results": [
                {"id": 1, "name": "CH1", "streams": [10]},
                {"id": 2, "name": "CH2", "streams": [40]},
            ],
            "count": 3,
        }
        page2 = {
            "results": [
                {"id": 3, "name": "CH3", "streams": [10, 50]},
            ],
            "count": 3,
        }

        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(side_effect=[page1, page2])
        mock_client.update_channel = AsyncMock()

        with patch("tasks.struck_stream_cleanup.get_settings", return_value=mock_settings), \
             patch("tasks.struck_stream_cleanup.get_session", return_value=test_session), \
             patch("tasks.struck_stream_cleanup.get_client", return_value=mock_client):
            result = await task.execute()

        assert result.success is True
        # Both pages fetched, in order.
        assert mock_client.get_channels.call_count == 2
        mock_client.get_channels.assert_any_call(page=1, page_size=100)
        mock_client.get_channels.assert_any_call(page=2, page_size=100)
        # Struck stream 10 removed from CH1 (page 1) and CH3 (page 2); CH2 untouched.
        assert mock_client.update_channel.call_count == 2
        mock_client.update_channel.assert_any_call(1, {"streams": []})
        mock_client.update_channel.assert_any_call(3, {"streams": [50]})
        assert result.success_count == 2

    def test_get_config_empty(self):
        """get_config returns empty dict."""
        task = self._make_task()
        assert task.get_config() == {}

    def test_update_config_noop(self):
        """update_config is a no-op."""
        task = self._make_task()
        task.update_config({"anything": "ignored"})
        assert task.get_config() == {}
