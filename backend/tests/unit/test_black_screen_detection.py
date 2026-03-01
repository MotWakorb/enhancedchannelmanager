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
    """Tests for _detect_black_screen method."""

    def _make_mock_process(self, stderr_output):
        """Create a mock process that works with asyncio.wait_for."""
        mock_process = AsyncMock()

        async def mock_communicate():
            return (b"", stderr_output)

        mock_process.communicate = mock_communicate
        mock_process.kill = Mock()
        mock_process.wait = AsyncMock()
        return mock_process

    @pytest.mark.asyncio
    async def test_detects_black_screen_above_threshold(self):
        """Returns True when >90% of sample is black."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = (
            b"[blackdetect @ 0x1234] black_start=0 black_end=4.8 black_duration=4.8\n"
        )
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_black(self):
        """Returns False when stream has normal content."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = b"frame=  150 fps= 30 q=-0.0 Lsize=N/A time=00:00:05.00\n"
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_below_threshold(self):
        """Returns False when black is present but below 90% threshold."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=10)
        stderr_output = (
            b"[blackdetect @ 0x1234] black_start=0 black_end=5 black_duration=5\n"
        )
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
    async def test_handles_multiple_black_segments(self):
        """Correctly sums multiple black segments."""
        prober = create_prober(black_screen_detection_enabled=True, black_screen_sample_duration=5)
        stderr_output = (
            b"[blackdetect @ 0x1234] black_start=0 black_end=2 black_duration=2\n"
            b"[blackdetect @ 0x1234] black_start=2.5 black_end=5.5 black_duration=3\n"
        )
        mock_process = self._make_mock_process(stderr_output)

        with patch("stream_prober.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await prober._detect_black_screen("http://example.com/stream")

        assert result is True


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
        """is_black_screen is stored when probe succeeds."""
        prober = create_prober()
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
        """is_black_screen=False is stored when probe succeeds and stream is not black."""
        prober = create_prober()
        mock_stats = Mock(spec=StreamStats)
        mock_stats.consecutive_failures = 0

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_stats
        mock_stats.to_dict.return_value = {"is_black_screen": False}

        with patch("stream_prober.get_session", return_value=mock_session):
            prober._save_probe_result(1, "Test", {}, "success", None, is_black_screen=False)

        assert mock_stats.is_black_screen is False


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
