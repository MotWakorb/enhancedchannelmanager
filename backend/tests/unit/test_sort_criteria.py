"""
Unit tests for stream sort criteria in the stream_prober module.

Tests the _smart_sort_streams method with focus on:
- M3U priority sorting
- Audio channels sorting
- Edge cases and backwards compatibility
"""
import pytest
from unittest.mock import MagicMock, Mock
from datetime import datetime

# Import the StreamProber class
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stream_prober import StreamProber
from models import StreamStats


def create_mock_stats(
    stream_id: int,
    stream_name: str = None,
    resolution: str = "1920x1080",
    bitrate: int = 5000000,
    fps: str = "30",
    audio_channels: int = 2,
    probe_status: str = "success"
) -> StreamStats:
    """Create a mock StreamStats object for testing."""
    stats = Mock(spec=StreamStats)
    stats.stream_id = stream_id
    stats.stream_name = stream_name or f"Stream {stream_id}"
    stats.resolution = resolution
    stats.bitrate = bitrate
    stats.fps = fps
    stats.audio_channels = audio_channels
    stats.probe_status = probe_status
    return stats


def create_prober(
    stream_sort_priority: list = None,
    stream_sort_enabled: dict = None,
    m3u_account_priorities: dict = None,
    deprioritize_failed_streams: bool = True
) -> StreamProber:
    """Create a StreamProber with specified sort settings."""
    mock_client = MagicMock()
    prober = StreamProber(
        client=mock_client,
        stream_sort_priority=stream_sort_priority or ["resolution", "bitrate", "framerate"],
        stream_sort_enabled=stream_sort_enabled or {"resolution": True, "bitrate": True, "framerate": True},
        m3u_account_priorities=m3u_account_priorities or {},
        deprioritize_failed_streams=deprioritize_failed_streams
    )
    return prober


class TestM3UPrioritySorting:
    """Tests for M3U priority sort criterion."""

    def test_m3u_priority_higher_first(self):
        """Streams from higher priority M3Us sort first."""
        prober = create_prober(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100, "2": 50, "3": 10}
        )

        # Create stats for streams from different M3U accounts
        stats_map = {
            1: create_mock_stats(1),  # M3U account 3, priority 10
            2: create_mock_stats(2),  # M3U account 1, priority 100
            3: create_mock_stats(3),  # M3U account 2, priority 50
        }

        # Map stream IDs to M3U accounts
        stream_m3u_map = {1: 3, 2: 1, 3: 2}

        sorted_ids = prober._smart_sort_streams([1, 2, 3], stats_map, stream_m3u_map, "Test Channel")

        # Should be sorted by priority: 100 > 50 > 10
        assert sorted_ids == [2, 3, 1]

    def test_m3u_priority_unknown_account_gets_zero(self):
        """Streams from unknown M3U accounts get priority 0."""
        prober = create_prober(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100}
        )

        stats_map = {
            1: create_mock_stats(1),   # M3U account 1, priority 100
            2: create_mock_stats(2),  # M3U account 99, unknown, priority 0
        }

        stream_m3u_map = {1: 1, 2: 99}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # Known M3U first
        assert sorted_ids == [1, 2]

    def test_m3u_priority_none_account_gets_zero(self):
        """Streams with no M3U account get priority 0."""
        prober = create_prober(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100}
        )

        stats_map = {
            1: create_mock_stats(1),     # M3U account 1, priority 100
            2: create_mock_stats(2),  # no M3U account, priority 0
        }

        stream_m3u_map = {1: 1, 2: None}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        assert sorted_ids == [1, 2]

    def test_m3u_priority_disabled_no_effect(self):
        """When m3u_priority is disabled, it has no effect on sorting."""
        prober = create_prober(
            stream_sort_priority=["m3u_priority", "resolution"],
            stream_sort_enabled={"m3u_priority": False, "resolution": True},
            m3u_account_priorities={"1": 100, "2": 10}
        )

        # Different M3U priorities but same resolution
        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080"),  # M3U account 2, low M3U priority
            2: create_mock_stats(2, resolution="1280x720"),   # M3U account 1, high M3U priority but low res
        }

        stream_m3u_map = {1: 2, 2: 1}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # Should sort by resolution only (m3u_priority disabled)
        assert sorted_ids == [1, 2]

    def test_m3u_priority_empty_priorities_map(self):
        """Empty m3u_account_priorities treats all accounts as priority 0."""
        prober = create_prober(
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={}
        )

        stats_map = {
            1: create_mock_stats(1),
            2: create_mock_stats(2),
        }

        stream_m3u_map = {1: 1, 2: 2}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # All same priority, original order preserved
        assert sorted_ids == [1, 2]


class TestAudioChannelsSorting:
    """Tests for audio channels sort criterion."""

    def test_audio_channels_surround_first(self):
        """5.1 surround (6 channels) sorts before stereo (2 channels)."""
        prober = create_prober(
            stream_sort_priority=["audio_channels"],
            stream_sort_enabled={"audio_channels": True}
        )

        stats_map = {
            1: create_mock_stats(1, audio_channels=2),  # stereo
            2: create_mock_stats(2, audio_channels=6),  # 5.1
            3: create_mock_stats(3, audio_channels=1),  # mono
        }

        sorted_ids = prober._smart_sort_streams([1, 2, 3], stats_map, {}, "Test Channel")

        # Should be sorted: 6ch > 2ch > 1ch
        assert sorted_ids == [2, 1, 3]

    def test_audio_channels_none_treated_as_zero(self):
        """Streams with no audio channel info sort last."""
        prober = create_prober(
            stream_sort_priority=["audio_channels"],
            stream_sort_enabled={"audio_channels": True}
        )

        stats_map = {
            1: create_mock_stats(1, audio_channels=None),
            2: create_mock_stats(2, audio_channels=2),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        assert sorted_ids == [2, 1]

    def test_audio_channels_disabled_no_effect(self):
        """When audio_channels is disabled, it has no effect."""
        prober = create_prober(
            stream_sort_priority=["audio_channels", "bitrate"],
            stream_sort_enabled={"audio_channels": False, "bitrate": True}
        )

        stats_map = {
            1: create_mock_stats(1, audio_channels=6, bitrate=1000000),
            2: create_mock_stats(2, audio_channels=2, bitrate=5000000),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Should sort by bitrate only
        assert sorted_ids == [2, 1]

    def test_audio_channels_eight_channel(self):
        """7.1 surround (8 channels) sorts before 5.1."""
        prober = create_prober(
            stream_sort_priority=["audio_channels"],
            stream_sort_enabled={"audio_channels": True}
        )

        stats_map = {
            1: create_mock_stats(1, audio_channels=6),  # 5.1
            2: create_mock_stats(2, audio_channels=8),  # 7.1
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        assert sorted_ids == [2, 1]


class TestCombinedCriteria:
    """Tests for multiple sort criteria working together."""

    def test_m3u_priority_as_tiebreaker(self):
        """M3U priority breaks ties when resolution is equal."""
        prober = create_prober(
            stream_sort_priority=["resolution", "m3u_priority"],
            stream_sort_enabled={"resolution": True, "m3u_priority": True},
            m3u_account_priorities={"1": 100, "2": 50}
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080"),  # same res, M3U account 2, low M3U priority
            2: create_mock_stats(2, resolution="1920x1080"),  # same res, M3U account 1, high M3U priority
        }

        stream_m3u_map = {1: 2, 2: 1}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # Same resolution, so M3U priority decides
        assert sorted_ids == [2, 1]

    def test_audio_channels_as_tiebreaker(self):
        """Audio channels breaks ties when other criteria are equal."""
        prober = create_prober(
            stream_sort_priority=["resolution", "audio_channels"],
            stream_sort_enabled={"resolution": True, "audio_channels": True}
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080", audio_channels=2),
            2: create_mock_stats(2, resolution="1920x1080", audio_channels=6),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        assert sorted_ids == [2, 1]

    def test_priority_order_matters(self):
        """First criterion in priority list takes precedence."""
        prober = create_prober(
            stream_sort_priority=["audio_channels", "resolution"],
            stream_sort_enabled={"audio_channels": True, "resolution": True}
        )

        stats_map = {
            1: create_mock_stats(1, resolution="3840x2160", audio_channels=2),  # 4K, stereo
            2: create_mock_stats(2, resolution="1920x1080", audio_channels=6),  # 1080p, 5.1
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Audio channels first, so 5.1 wins despite lower resolution
        assert sorted_ids == [2, 1]

    def test_all_five_criteria(self):
        """Test with all five criteria enabled."""
        prober = create_prober(
            stream_sort_priority=["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"],
            stream_sort_enabled={
                "resolution": True,
                "bitrate": True,
                "framerate": True,
                "m3u_priority": True,
                "audio_channels": True
            },
            m3u_account_priorities={"1": 100, "2": 50}
        )

        # All same resolution and bitrate, different framerate
        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080", bitrate=5000000, fps="30"),
            2: create_mock_stats(2, resolution="1920x1080", bitrate=5000000, fps="60"),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Higher framerate wins
        assert sorted_ids == [2, 1]


class TestBackwardsCompatibility:
    """Tests for backwards compatibility with old configurations."""

    def test_old_config_without_new_criteria(self):
        """Old configs without m3u_priority and audio_channels still work."""
        prober = create_prober(
            stream_sort_priority=["resolution", "bitrate", "framerate"],
            stream_sort_enabled={"resolution": True, "bitrate": True, "framerate": True}
            # Note: no m3u_account_priorities, no new criteria in enabled map
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1280x720", bitrate=3000000),
            2: create_mock_stats(2, resolution="1920x1080", bitrate=5000000),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Higher resolution wins
        assert sorted_ids == [2, 1]

    def test_new_criteria_not_in_priority_list(self):
        """New criteria not in priority list are ignored."""
        prober = create_prober(
            stream_sort_priority=["resolution"],  # Only resolution
            stream_sort_enabled={
                "resolution": True,
                "m3u_priority": True,  # Enabled but not in priority list
                "audio_channels": True
            },
            m3u_account_priorities={"1": 100, "2": 10}
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080", audio_channels=6),
            2: create_mock_stats(2, resolution="1920x1080", audio_channels=2),
        }

        stream_m3u_map = {1: 2, 2: 1}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # Only resolution considered, and they're equal, so original order
        assert sorted_ids == [1, 2]


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_stream_list(self):
        """Empty stream list returns empty list."""
        prober = create_prober()
        sorted_ids = prober._smart_sort_streams([], {}, {}, "Test Channel")
        assert sorted_ids == []

    def test_single_stream(self):
        """Single stream returns single-item list."""
        prober = create_prober()
        stats_map = {1: create_mock_stats(1)}
        sorted_ids = prober._smart_sort_streams([1], stats_map, {}, "Test Channel")
        assert sorted_ids == [1]

    def test_missing_stats_for_stream(self):
        """Stream with missing stats is handled gracefully."""
        prober = create_prober(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True}
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080"),
            # Stream 2 has no stats
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Stream with stats should come first
        assert sorted_ids[0] == 1

    def test_failed_stream_deprioritized(self):
        """Failed streams are sorted to the bottom when deprioritize is enabled."""
        prober = create_prober(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_failed_streams=True
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1280x720", probe_status="success"),
            2: create_mock_stats(2, resolution="1920x1080", probe_status="failed"),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Failed stream at bottom despite higher resolution
        assert sorted_ids == [1, 2]

    def test_failed_stream_not_deprioritized_when_disabled(self):
        """Failed streams are not pushed to bottom when deprioritize is disabled.

        Note: Failed streams still get zero sort values (not sorted by their stats)
        because their probe data may be unreliable. The deprioritize flag only
        controls whether they're actively pushed to the bottom.
        """
        prober = create_prober(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_failed_streams=False
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1280x720", probe_status="success"),
            2: create_mock_stats(2, resolution="1920x1080", probe_status="failed"),
        }

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, {}, "Test Channel")

        # Successful stream sorted by stats, failed stream gets zeros (not pushed to bottom)
        # Both have (0,) prefix, but stream 1 has negative resolution, stream 2 has 0
        assert sorted_ids == [1, 2]


class TestUpdateSortSettings:
    """Tests for the update_sort_settings method."""

    def test_update_sort_settings_changes_priority(self):
        """update_sort_settings updates the sort priority."""
        prober = create_prober(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True}
        )

        prober.update_sort_settings(
            stream_sort_priority=["bitrate", "resolution"],
            stream_sort_enabled={"bitrate": True, "resolution": True},
            m3u_account_priorities={"1": 100}
        )

        assert prober.stream_sort_priority == ["bitrate", "resolution"]
        assert prober.stream_sort_enabled == {"bitrate": True, "resolution": True}
        assert prober.m3u_account_priorities == {"1": 100}

    def test_update_sort_settings_enables_new_criteria(self):
        """update_sort_settings can enable new criteria."""
        prober = create_prober(
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            m3u_account_priorities={}
        )

        prober.update_sort_settings(
            stream_sort_priority=["resolution", "m3u_priority", "audio_channels"],
            stream_sort_enabled={"resolution": True, "m3u_priority": True, "audio_channels": True},
            m3u_account_priorities={"1": 100, "2": 50}
        )

        # Verify it works with a sort
        stats_map = {
            1: create_mock_stats(1, resolution="1920x1080"),
            2: create_mock_stats(2, resolution="1920x1080"),
        }

        stream_m3u_map = {1: 2, 2: 1}

        sorted_ids = prober._smart_sort_streams([1, 2], stats_map, stream_m3u_map, "Test Channel")

        # Higher M3U priority wins
        assert sorted_ids == [2, 1]
