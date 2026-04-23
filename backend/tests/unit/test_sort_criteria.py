"""
Unit tests for stream sort criteria in the stream_prober module.

Tests the _smart_sort_streams method with focus on:
- M3U priority sorting
- Audio channels sorting
- Edge cases and backwards compatibility
"""
from unittest.mock import MagicMock, Mock

# Import the StreamProber class
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stream_prober import StreamProber, smart_sort_streams, extract_m3u_account_id
from models import StreamStats


def create_mock_stats(
    stream_id: int,
    stream_name: str = None,
    resolution: str = "1920x1080",
    bitrate: int = 5000000,
    video_bitrate: int = None,
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
    stats.video_bitrate = video_bitrate  # Add video_bitrate field
    stats.fps = fps
    stats.audio_channels = audio_channels
    stats.probe_status = probe_status
    stats.is_black_screen = False
    stats.is_low_fps = False
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


def run_standalone_sort(stream_ids, stats_map, stream_m3u_map=None, **kwargs):
    """Helper to call standalone smart_sort_streams with sensible defaults."""
    return smart_sort_streams(
        stream_ids=stream_ids,
        stats_map=stats_map,
        stream_m3u_map=stream_m3u_map or {},
        stream_sort_priority=kwargs.get("stream_sort_priority", ["resolution", "bitrate", "framerate"]),
        stream_sort_enabled=kwargs.get("stream_sort_enabled", {"resolution": True, "bitrate": True, "framerate": True}),
        m3u_account_priorities=kwargs.get("m3u_account_priorities", {}),
        deprioritize_failed_streams=kwargs.get("deprioritize_failed_streams", True),
        channel_name=kwargs.get("channel_name", "Test Channel"),
    )


class TestStandaloneSortFunction:
    """Tests for the standalone smart_sort_streams() function."""

    def test_standalone_matches_prober_method(self):
        """Same input produces identical output for both standalone and prober method."""
        priority = ["resolution", "bitrate", "framerate"]
        enabled = {"resolution": True, "bitrate": True, "framerate": True}
        prober = create_prober(
            stream_sort_priority=priority,
            stream_sort_enabled=enabled,
            deprioritize_failed_streams=True,
        )

        stats_map = {
            1: create_mock_stats(1, resolution="1280x720", bitrate=3000000, fps="30"),
            2: create_mock_stats(2, resolution="1920x1080", bitrate=5000000, fps="60"),
            3: create_mock_stats(3, resolution="1920x1080", bitrate=5000000, fps="30"),
        }

        prober_result = prober._smart_sort_streams([1, 2, 3], stats_map, {}, "Test Channel")
        standalone_result = smart_sort_streams(
            [1, 2, 3], stats_map, {},
            priority, enabled, {}, True, "Test Channel"
        )

        assert prober_result == standalone_result

    def test_standalone_m3u_priority(self):
        """M3U priority sorting works without StreamProber instance."""
        result = run_standalone_sort(
            [1, 2, 3],
            {
                1: create_mock_stats(1),
                2: create_mock_stats(2),
                3: create_mock_stats(3),
            },
            stream_m3u_map={1: 3, 2: 1, 3: 2},
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100, "2": 50, "3": 10},
        )
        assert result == [2, 3, 1]

    def test_standalone_resolution_sort(self):
        """Resolution-based sort via standalone function."""
        result = run_standalone_sort(
            [1, 2],
            {
                1: create_mock_stats(1, resolution="1280x720"),
                2: create_mock_stats(2, resolution="1920x1080"),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
        )
        assert result == [2, 1]

    def test_standalone_all_criteria(self):
        """All 5 criteria work together via standalone function."""
        result = run_standalone_sort(
            [1, 2],
            {
                1: create_mock_stats(1, resolution="1920x1080", bitrate=5000000, fps="30"),
                2: create_mock_stats(2, resolution="1920x1080", bitrate=5000000, fps="60"),
            },
            stream_sort_priority=["resolution", "bitrate", "framerate", "m3u_priority", "audio_channels"],
            stream_sort_enabled={
                "resolution": True, "bitrate": True, "framerate": True,
                "m3u_priority": True, "audio_channels": True,
            },
        )
        # Higher framerate wins (all else equal)
        assert result == [2, 1]

    def test_standalone_empty_list(self):
        """Returns [] for empty input."""
        assert run_standalone_sort([], {}) == []

    def test_standalone_single_stream(self):
        """Returns [id] for single stream."""
        result = run_standalone_sort([42], {42: create_mock_stats(42)})
        assert result == [42]

    def test_standalone_failed_deprioritized(self):
        """Failed streams deprioritized via standalone function."""
        result = run_standalone_sort(
            [1, 2],
            {
                1: create_mock_stats(1, resolution="1280x720", probe_status="success"),
                2: create_mock_stats(2, resolution="1920x1080", probe_status="failed"),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_failed_streams=True,
        )
        # Failed stream at bottom despite higher resolution
        assert result == [1, 2]

    def test_standalone_failed_still_sorted_by_m3u(self):
        """Both-failed streams still get M3U priority ordering within their bucket.

        bd-sw883 / issue #73: within a deprioritized bucket the primary criteria
        (here m3u_priority) must still apply. Stream 2 is on m3u account 1
        (priority 100); stream 1 is on m3u account 2 (priority 50) — so stream 2
        leads the bucket.
        """
        result = run_standalone_sort(
            [1, 2],
            {
                1: create_mock_stats(1, probe_status="failed"),
                2: create_mock_stats(2, probe_status="failed"),
            },
            stream_m3u_map={1: 2, 2: 1},
            stream_sort_priority=["m3u_priority"],
            stream_sort_enabled={"m3u_priority": True},
            m3u_account_priorities={"1": 100, "2": 50},
            deprioritize_failed_streams=True,
        )
        # Both deprioritized, but m3u_priority ordering is applied within the bucket
        assert result == [2, 1]


class TestExtractM3uAccountId:
    """Tests for the standalone extract_m3u_account_id function."""

    def test_extract_from_direct_id(self):
        """Direct integer ID is returned as-is."""
        assert extract_m3u_account_id(3) == 3

    def test_extract_from_dict(self):
        """Nested dict format extracts the 'id' field."""
        assert extract_m3u_account_id({"id": 3, "name": "Provider"}) == 3

    def test_extract_none(self):
        """None input returns None."""
        assert extract_m3u_account_id(None) is None

    def test_extract_dict_without_id(self):
        """Dict without 'id' key returns None."""
        assert extract_m3u_account_id({"name": "Provider"}) is None


class TestPerCategoryDeprioritization:
    """Tests for per-category deprioritize_black_screen / deprioritize_low_fps (GitHub #56)."""

    def _make_stats(self, stream_id, resolution="1920x1080", is_black_screen=False, is_low_fps=False):
        stats = create_mock_stats(stream_id, resolution=resolution)
        stats.is_black_screen = is_black_screen
        stats.is_low_fps = is_low_fps
        return stats

    # -- Black screen --

    def test_black_screen_deprioritized_by_default(self):
        """With defaults, black screen streams sort to the bottom."""
        result = smart_sort_streams(
            [1, 2],
            {
                1: self._make_stats(1, resolution="1280x720"),
                2: self._make_stats(2, resolution="1920x1080", is_black_screen=True),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
        )
        assert result == [1, 2]

    def test_black_screen_sorted_by_quality_when_not_deprioritized(self):
        """When deprioritize_black_screen=False, black screen streams sort by quality."""
        result = smart_sort_streams(
            [1, 2],
            {
                1: self._make_stats(1, resolution="1280x720"),
                2: self._make_stats(2, resolution="1920x1080", is_black_screen=True),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_black_screen=False,
        )
        # Stream 2 has higher resolution and should sort first
        assert result == [2, 1]

    def test_black_screen_still_deprioritized_when_master_off(self):
        """When master deprioritize_failed_streams=False, per-category flag is irrelevant."""
        result = smart_sort_streams(
            [1, 2],
            {
                1: self._make_stats(1, resolution="1280x720"),
                2: self._make_stats(2, resolution="1920x1080", is_black_screen=True),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_failed_streams=False,
            deprioritize_black_screen=True,
        )
        # Master toggle is off, so black screen sorts by quality
        assert result == [2, 1]

    # -- Low FPS --

    def test_low_fps_deprioritized_by_default(self):
        """With defaults, low FPS streams sort to the bottom."""
        result = smart_sort_streams(
            [1, 2],
            {
                1: self._make_stats(1, resolution="1280x720"),
                2: self._make_stats(2, resolution="1920x1080", is_low_fps=True),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
        )
        assert result == [1, 2]

    def test_low_fps_sorted_by_quality_when_not_deprioritized(self):
        """When deprioritize_low_fps=False, low FPS streams sort by quality."""
        result = smart_sort_streams(
            [1, 2],
            {
                1: self._make_stats(1, resolution="1280x720"),
                2: self._make_stats(2, resolution="1920x1080", is_low_fps=True),
            },
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_low_fps=False,
        )
        assert result == [2, 1]

    # -- Mixed --

    def test_independent_per_category_flags(self):
        """Can deprioritize black screen but not low FPS (or vice versa)."""
        bs = self._make_stats(1, resolution="1920x1080", is_black_screen=True)
        lf = self._make_stats(2, resolution="1920x1080", is_low_fps=True)
        ok = self._make_stats(3, resolution="1280x720")

        result = smart_sort_streams(
            [1, 2, 3],
            {1: bs, 2: lf, 3: ok},
            stream_sort_priority=["resolution"],
            stream_sort_enabled={"resolution": True},
            deprioritize_black_screen=True,
            deprioritize_low_fps=False,
        )
        # Low FPS sorts by quality (1080), ok by quality (720), black screen at bottom
        assert result[0] == 2  # Low FPS 1080 first
        assert result[-1] == 1  # Black screen last


class TestWithinBucketPrimaryCriteria:
    """Regression tests for bd-sw883 / GitHub #73.

    Primary sort criteria (resolution, framerate, etc.) must be applied
    WITHIN each failed-rank bucket — not just at the bucket-boundary level.
    Previously streams inside the same bucket (e.g. all black_screen at rank=0,
    or all status=failed at rank=2) were tied on the composite sort key and
    therefore kept insertion order regardless of configured criteria.
    """

    def _bs_stats(self, stream_id, resolution, fps):
        """Build a success-probed black-screen stream stats (lands in rank=0)."""
        stats = create_mock_stats(
            stream_id,
            resolution=resolution,
            fps=fps,
            probe_status="success",
        )
        stats.is_black_screen = True
        stats.is_low_fps = False
        return stats

    def _failed_stats(self, stream_id, resolution, fps):
        """Build a status=failed stream stats (lands in rank=2).

        Failed probes don't have meaningful resolution/fps on disk, but we
        attach them here so the test can assert sort-key ordering logic —
        reporter's log shows these fields ARE populated from the prior
        successful probe when a later probe fails.
        """
        stats = create_mock_stats(
            stream_id,
            resolution=resolution,
            fps=fps,
            probe_status="failed",
        )
        stats.is_black_screen = False
        stats.is_low_fps = False
        return stats

    # -- Black screen bucket (rank=0) --

    def test_black_screen_bucket_sorted_by_resolution_desc(self):
        """Within the black_screen bucket, higher resolution sorts first."""
        result = smart_sort_streams(
            [1, 2, 3],
            {
                1: self._bs_stats(1, resolution="1024x576", fps="25"),
                2: self._bs_stats(2, resolution="1920x1080", fps="25"),
                3: self._bs_stats(3, resolution="1024x576", fps="25"),
            },
            stream_sort_priority=["resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec"],
            stream_sort_enabled={
                "resolution": True, "framerate": True, "m3u_priority": True,
                "bitrate": True, "audio_channels": True, "video_codec": True,
            },
            deprioritize_failed_streams=True,
            deprioritize_black_screen=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
        )
        # All three are rank=0 black_screen. Primary criterion is resolution desc:
        # the 1920x1080 stream (id=2) must lead the bucket.
        assert result[0] == 2, (
            f"Expected 1920x1080 stream (id=2) at #1 in black_screen bucket, "
            f"got ordering {result}"
        )

    # -- Status=failed bucket (rank=2) --

    def test_failed_bucket_sorted_by_resolution_then_framerate(self):
        """Within the status=failed bucket, resolution desc then framerate desc apply.

        Reporter's exact scenario: 1280x720@25 was landing ahead of 1920x1080@50
        inside the failed bucket because the within-bucket tiebreaker was a
        tuple of zeros across all primary criteria.
        """
        result = smart_sort_streams(
            [10, 20, 30, 40],
            {
                10: self._failed_stats(10, resolution="1280x720", fps="25"),
                20: self._failed_stats(20, resolution="1920x1080", fps="50"),
                30: self._failed_stats(30, resolution="1280x720", fps="25"),
                40: self._failed_stats(40, resolution="1920x1080", fps="50"),
            },
            stream_sort_priority=["resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec"],
            stream_sort_enabled={
                "resolution": True, "framerate": True, "m3u_priority": True,
                "bitrate": True, "audio_channels": True, "video_codec": True,
            },
            deprioritize_failed_streams=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
        )
        # Expected within-bucket order: both 1920x1080@50 streams first (by
        # insertion order 20, 40), then both 1280x720@25 streams (10, 30).
        assert result[:2] == [20, 40], (
            f"Expected 1920x1080@50 streams (20, 40) to lead the failed bucket, "
            f"got {result}"
        )
        assert result[2:] == [10, 30], (
            f"Expected 1280x720@25 streams (10, 30) at the tail of the failed bucket, "
            f"got {result}"
        )

    # -- Cross-bucket invariant MUST NOT break --

    def test_cross_bucket_ordering_preserved(self):
        """Cross-bucket ordering: rank=0 (black_screen) still precedes rank=2 (failed).

        This is the invariant we must not regress while fixing within-bucket sort.
        Pick values so primary-criteria alone would invert the order: the
        rank=2 stream has HIGHER resolution than the rank=0 stream, yet
        the rank=0 bucket must still sort above rank=2.
        """
        result = smart_sort_streams(
            [1, 2],
            {
                # rank=0 (black_screen) but lower-resolution content
                1: self._bs_stats(1, resolution="1024x576", fps="25"),
                # rank=2 (failed) but higher-resolution content
                2: self._failed_stats(2, resolution="1920x1080", fps="50"),
            },
            stream_sort_priority=["resolution", "framerate"],
            stream_sort_enabled={"resolution": True, "framerate": True},
            deprioritize_failed_streams=True,
            deprioritize_black_screen=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
        )
        # rank=0 (id=1) must precede rank=2 (id=2) regardless of quality inversion
        assert result == [1, 2], (
            f"Cross-bucket invariant broken: expected rank=0 before rank=2 "
            f"but got {result}"
        )

    # -- Full ferteque-like scenario --

    def test_ferteque_channel_scenario(self):
        """Condensed reproduction of the reporter's channel-591424 case.

        3 black_screen streams and 4 failed streams. Expected:
        - Black_screen bucket leads.
        - Within black_screen, the 1920x1080 stream leads (was landing #2 in prod).
        - Within failed, the 1920x1080@50 streams lead (were landing #8 in prod).
        """
        stats_map = {
            # Black-screen bucket — from reporter's log
            101: self._bs_stats(101, resolution="1024x576", fps="25"),    # D.LaLiga2
            102: self._bs_stats(102, resolution="1920x1080", fps="25"),   # UHD
            103: self._bs_stats(103, resolution="1024x576", fps="25"),    # SD
            # Failed bucket — condensed
            201: self._failed_stats(201, resolution="1280x720", fps="25"),   # HD
            202: self._failed_stats(202, resolution="1920x1080", fps="50"),  # HD 1080
            203: self._failed_stats(203, resolution="1920x1080", fps="25"),  # S.LaLiga2
        }
        result = smart_sort_streams(
            [101, 102, 103, 201, 202, 203],
            stats_map,
            stream_sort_priority=["resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec"],
            stream_sort_enabled={
                "resolution": True, "framerate": True, "m3u_priority": True,
                "bitrate": True, "audio_channels": True, "video_codec": True,
            },
            deprioritize_failed_streams=True,
            deprioritize_black_screen=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
        )
        # Bucket boundaries: first 3 are rank=0, last 3 are rank=2
        bs_bucket = result[:3]
        failed_bucket = result[3:]
        # Within black_screen bucket — 1920x1080 (id=102) leads
        assert bs_bucket[0] == 102, (
            f"Expected 1920x1080 black_screen stream (102) to lead bucket, "
            f"got black_screen bucket order {bs_bucket}"
        )
        # Within failed bucket — 1920x1080@50 (id=202) leads,
        # then 1920x1080@25 (id=203), then 1280x720@25 (id=201) at the tail
        assert failed_bucket == [202, 203, 201], (
            f"Expected failed bucket ordered by resolution desc then framerate desc "
            f"— [202, 203, 201] — got {failed_bucket}"
        )

    # -- Full 13-stream ferteque regression (bd-sw883 re-open 2026-04-21) --

    def test_ferteque_full_13_stream_regression(self):
        """Exact 13-stream reproduction of reporter's channel-591424 log.

        bd-sw883 was re-opened on 2026-04-21 when ferteque reported the
        GH-73 defect still repros after the original 2026-04-20 fix merged.
        This test mirrors the reporter's log entry ID-for-ID so regressions
        against the exact published log are caught here.

        Bucket expectations with full primary criteria enabled
        (resolution, framerate, m3u_priority, bitrate, audio_channels, video_codec):

        - rank=0 black_screen bucket (5 success probes flagged black):
          * 958629 (1920x1080 @ 50) — highest res + highest fps
          * 958471 (1920x1080 @ 50) — same res/fps; later insertion → tail via stable sort
          * 959174 (1920x1080 @ 25) — same res, lower fps
          * 962110 (1024x576 @ 25)  — lower res; earlier insertion
          * 959167 (1024x576 @ 25)  — lower res; later insertion
        - rank=2 failed bucket (8 failed probes):
          * 1465608 (1920x1080 @ 50) — lowest id among 1080@50 failed
          * 1465609 (1920x1080 @ 50)
          * 1151440 (1920x1080 @ 50)
          * 1151441 (1920x1080 @ 50)
          * 962109  (1920x1080 @ 25)
          * 962111  (1920x1080 @ 25)
          * 1465610 (1280x720 @ 25)
          * 1151442 (1280x720 @ 25)

        Within equal primary keys, the deterministic tiebreaker is stream_id
        ascending (see get_sort_value trailing ``(stream_id,)`` appendix).
        """
        stats_map = {
            # black_screen (success probes flagged black) — rank 0
            962110: self._bs_stats(962110, resolution="1024x576", fps="25"),    # D.LaLiga2
            959174: self._bs_stats(959174, resolution="1920x1080", fps="25"),   # MOVISTAR UHD
            959167: self._bs_stats(959167, resolution="1024x576", fps="25"),    # MOVISTAR SD
            958629: self._bs_stats(958629, resolution="1920x1080", fps="50"),   # M.LALIGA 2 RAW
            958471: self._bs_stats(958471, resolution="1920x1080", fps="50"),   # VO MOVISTAR LALIGA 2 RAW
            # status=failed — rank 2
            962109: self._failed_stats(962109, resolution="1920x1080", fps="25"),  # K.LaLiga2
            1465610: self._failed_stats(1465610, resolution="1280x720", fps="25"), # HD
            1465608: self._failed_stats(1465608, resolution="1920x1080", fps="50"),# HD 1080
            1465609: self._failed_stats(1465609, resolution="1920x1080", fps="50"),# HD 1080 B
            1151442: self._failed_stats(1151442, resolution="1280x720", fps="25"), # HD (dup)
            1151440: self._failed_stats(1151440, resolution="1920x1080", fps="50"),# HD 1080 (dup)
            1151441: self._failed_stats(1151441, resolution="1920x1080", fps="50"),# HD 1080 B (dup)
            962111: self._failed_stats(962111, resolution="1920x1080", fps="25"),  # S.LaLiga2 HD
        }
        # Original insertion order from reporter's log
        ids_in = [
            962110, 959174, 959167, 962109, 958629, 958471, 1465610,
            1465608, 1465609, 1151442, 1151440, 1151441, 962111,
        ]
        result = smart_sort_streams(
            ids_in,
            stats_map,
            stream_sort_priority=["resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec"],
            stream_sort_enabled={
                "resolution": True, "framerate": True, "m3u_priority": True,
                "bitrate": True, "audio_channels": True, "video_codec": True,
            },
            deprioritize_failed_streams=True,
            deprioritize_black_screen=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
            channel_name="channel-591424",
        )
        bs_bucket = result[:5]
        failed_bucket = result[5:]
        # Black-screen bucket: 1920x1080@50 streams lead (id asc: 958471 < 958629)
        assert bs_bucket == [958471, 958629, 959174, 959167, 962110], (
            f"black_screen bucket mismatch: got {bs_bucket}"
        )
        # Failed bucket: 1920x1080@50 > 1920x1080@25 > 1280x720@25, id asc within ties
        assert failed_bucket == [
            1151440, 1151441, 1465608, 1465609,  # 1920x1080 @ 50 (id asc)
            962109, 962111,                      # 1920x1080 @ 25 (id asc)
            1151442, 1465610,                    # 1280x720 @ 25 (id asc)
        ], f"failed bucket mismatch: got {failed_bucket}"

    def test_all_black_screen_bucket_pure_primary_criteria(self):
        """All streams in a single bucket: within-bucket order is pure primary-criteria.

        Investigation guard for bd-sw883 re-open: reporter's briefing called out
        the corner where every stream lands in rank=0 (success probes + black
        screen). Prior to the fix this degenerate case exposed the bug most
        cleanly because there's no cross-bucket structure to hide behind.
        """
        stats_map = {
            1: self._bs_stats(1, resolution="1024x576", fps="25"),
            2: self._bs_stats(2, resolution="1920x1080", fps="25"),
            3: self._bs_stats(3, resolution="1024x576", fps="25"),
            4: self._bs_stats(4, resolution="1920x1080", fps="50"),
            5: self._bs_stats(5, resolution="1920x1080", fps="50"),
            6: self._bs_stats(6, resolution="1280x720", fps="25"),
        }
        result = smart_sort_streams(
            [1, 2, 3, 4, 5, 6],
            stats_map,
            stream_sort_priority=["resolution", "framerate", "m3u_priority", "bitrate", "audio_channels", "video_codec"],
            stream_sort_enabled={
                "resolution": True, "framerate": True, "m3u_priority": True,
                "bitrate": True, "audio_channels": True, "video_codec": True,
            },
            deprioritize_failed_streams=True,
            deprioritize_black_screen=True,
            failed_stream_sort_order=["black_screen", "low_fps", "failed"],
        )
        # Expected: 1920x1080@50 (4, 5 by id) > 1920x1080@25 (2) > 1280x720@25 (6) > 1024x576@25 (1, 3 by id)
        assert result == [4, 5, 2, 6, 1, 3], (
            f"All-black-screen bucket ordering failed: got {result}"
        )
