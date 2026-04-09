"""Tests for black screen detection feature in StreamProber."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from models import StreamStats
from stream_prober import StreamProber, smart_sort_streams


def create_prober(**kwargs) -> StreamProber:
    """Create a StreamProber with specified settings."""
    mock_client = MagicMock()
    defaults = {
        "probe_timeout": 30,
        "black_screen_detection_enabled": False,
        "black_screen_sample_duration": 5,
    }
    defaults.update(kwargs)
    return StreamProber(client=mock_client, **defaults)


def create_mock_stats(
    stream_id: int,
    probe_status: str = "success",
    resolution: str = "1920x1080",
    bitrate: int = 5000000,
    fps: str = "30",
    audio_channels: int = 2,
    is_black_screen: bool = False,
) -> StreamStats:
    """Create a mock StreamStats object."""
    stats = Mock(spec=StreamStats)
    stats.stream_id = stream_id
    stats.stream_name = f"Stream {stream_id}"
    stats.resolution = resolution
    stats.bitrate = bitrate
    stats.video_bitrate = None
    stats.fps = fps
    stats.audio_channels = audio_channels
    stats.probe_status = probe_status
    stats.is_black_screen = is_black_screen
    return stats


class TestDetectBlackScreen:
    """Tests for _detect_black_screen method (signalstats YAVG-based)."""

    def _make_mock_process(self, stderr_output):
        """Create a mock process that works with asyncio.wait_for."""
        mock_process = AsyncMock()

        async def mock_communicate():
            return (b"", stderr_output)

        mock_process.communicate = mock_communicate
        mock_process.kill = Mock()
        mock_process.wait = AsyncMock()
        return mock_process

    def _make_yavg_output(self, *values):
        """Build ffmpeg signalstats stderr output from YAVG values."""
        lines = [
            f"[Parsed_metadata_1 @ 0x1234] lavfi.signalstats.YAVG={v}\n".encode()
            for v in values
        ]
        return b"".join(lines)

    @pytest.mark.asyncio
    async def test_detects_dark_screen_below_threshold(self):
        """Returns True when average YAVG is below threshold (pure black = 16)."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = self._make_yavg_output(16.0, 16.0, 16.0, 16.0)
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is True

    @pytest.mark.asyncio
    async def test_detects_dark_slate_with_logo(self):
        """Returns True for dark slate with small logo (YAVG ~16.5)."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = self._make_yavg_output(16.5, 16.5, 16.6, 16.5)
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_for_normal_content(self):
        """Returns False when stream has normal content (YAVG ~88)."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = self._make_yavg_output(87.5, 88.0, 87.8, 88.2)
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_for_dim_but_not_dark_content(self):
        """Returns False when brightness is low but above threshold (YAVG ~30)."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = self._make_yavg_output(28.0, 30.0, 32.0, 29.0)
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        """Returns False when ffmpeg times out (not black, just failed)."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        mock_process = AsyncMock()

        async def mock_communicate():
            raise asyncio.TimeoutError()

        mock_process.communicate = mock_communicate
        mock_process.kill = Mock()
        mock_process.wait = AsyncMock()

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_yavg_data(self):
        """Returns False when signalstats produces no YAVG output."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = b"frame=  150 fps= 30 q=-0.0 Lsize=N/A time=00:00:05.00\n"
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False

    @pytest.mark.asyncio
    async def test_borderline_at_threshold(self):
        """Returns False when YAVG is exactly at threshold."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        threshold = StreamProber.BLACK_SCREEN_YAVG_THRESHOLD
        stderr_output = self._make_yavg_output(threshold, threshold, threshold)
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False  # < threshold, not <=


class TestSmartSortBlackScreen:
    """Tests for black screen deprioritization in smart sort."""

    def test_black_screen_streams_sort_to_bottom(self):
        """Streams with is_black_screen=True sort to bottom when deprioritize is enabled."""
        stats = {
            1: create_mock_stats(1, is_black_screen=True),
            2: create_mock_stats(2, is_black_screen=False),
        }
        result = smart_sort_streams(
            [1, 2],
            stats,
            deprioritize_failed_streams=True,
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
        )
        assert result == [2, 1]

    def test_black_screen_not_deprioritized_when_setting_off(self):
        """Black screen streams are not deprioritized when deprioritize_failed_streams is False."""
        stats = {
            1: create_mock_stats(1, is_black_screen=True, resolution="1920x1080"),
            2: create_mock_stats(2, is_black_screen=False, resolution="1280x720"),
        }
        result = smart_sort_streams(
            [1, 2],
            stats,
            deprioritize_failed_streams=False,
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
        )
        # Stream 1 has higher resolution, should be first even though black
        assert result == [1, 2]


class TestSaveProbeResultBlackScreen:
    """Tests for is_black_screen persistence in _save_probe_result."""

    def test_stores_black_screen_on_success(self):
        """is_black_screen is stored when probe succeeds and detection is enabled."""
        prober = create_prober(black_screen_detection_enabled=True)
        mock_stats = Mock(spec=StreamStats)
        mock_stats.consecutive_failures = 0

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_stats
        mock_stats.to_dict.return_value = {"is_black_screen": True}

        with patch("stream_prober.get_session", return_value=mock_session):
            prober._save_probe_result(1, "Test", {}, "success", None, is_black_screen=True)

        assert mock_stats.is_black_screen is True

    def test_clears_black_screen_on_failure(self):
        """is_black_screen is cleared when probe fails."""
        prober = create_prober()
        mock_stats = Mock(spec=StreamStats)
        mock_stats.consecutive_failures = 0
        mock_stats.is_black_screen = True

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_stats
        mock_stats.to_dict.return_value = {"is_black_screen": False}

        with patch("stream_prober.get_session", return_value=mock_session):
            prober._save_probe_result(1, "Test", None, "failed", "Connection refused")

        assert mock_stats.is_black_screen is False

    def test_stores_false_when_not_black(self):
        """is_black_screen=False is stored when probe succeeds and detection is enabled."""
        prober = create_prober(black_screen_detection_enabled=True)
        mock_stats = Mock(spec=StreamStats)
        mock_stats.consecutive_failures = 0

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_stats
        mock_stats.to_dict.return_value = {"is_black_screen": False}

        with patch("stream_prober.get_session", return_value=mock_session):
            prober._save_probe_result(1, "Test", {}, "success", None, is_black_screen=False)

        assert mock_stats.is_black_screen is False

    def test_preserves_black_screen_when_detection_disabled(self):
        """is_black_screen is NOT overwritten when detection is disabled."""
        prober = create_prober(black_screen_detection_enabled=False)
        mock_stats = Mock(spec=StreamStats)
        mock_stats.consecutive_failures = 0
        mock_stats.is_black_screen = True  # Set by prior black screen scan

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_stats
        mock_stats.to_dict.return_value = {"is_black_screen": True}

        with patch("stream_prober.get_session", return_value=mock_session):
            prober._save_probe_result(1, "Test", {}, "success", None, is_black_screen=False)

        # Should still be True — probe didn't run detection, so it preserves the existing value
        assert mock_stats.is_black_screen is True


class TestConstructorBlackScreenSettings:
    """Tests for black screen constructor settings."""

    def test_default_values(self):
        """Black screen detection is off by default."""
        prober = create_prober()
        assert prober.black_screen_detection_enabled is False
        assert prober.black_screen_sample_duration == 5

    def test_custom_values(self):
        """Custom black screen settings are accepted."""
        prober = create_prober(
            black_screen_detection_enabled=True,
            black_screen_sample_duration=10,
        )
        assert prober.black_screen_detection_enabled is True
        assert prober.black_screen_sample_duration == 10

    def test_sample_duration_clamped(self):
        """Sample duration is clamped to 3-30 range."""
        prober_low = create_prober(black_screen_sample_duration=1)
        assert prober_low.black_screen_sample_duration == 3

        prober_high = create_prober(black_screen_sample_duration=60)
        assert prober_high.black_screen_sample_duration == 30
