"""Characterization tests for shared Smart Sort evaluation semantics."""

from dataclasses import replace

import pytest

from smart_sort_evaluator import (
    PointRule,
    StreamFacts,
    evaluate_points,
    health_deprioritization_category,
    health_deprioritization_reason,
    sort_streams,
    sort_streams_by_points,
    sort_streams_by_priority,
    stream_metadata_criteria,
)


def _healthy(stream_id: int, **overrides) -> StreamFacts:
    values = {
        "probe_succeeded": True,
        "resolution_height": 1080,
        "video_bitrate": 5_000_000,
        "bitrate": 6_000_000,
        "framerate": 30.0,
        "video_codec": "h264",
        "m3u_priority": 0,
        "audio_channels": 2,
        "is_custom": False,
        "is_catchup": False,
        "failed": False,
        "black_screen": False,
        "low_fps": False,
    }
    values.update(overrides)
    return StreamFacts(stream_id=stream_id, **values)


def _health_category(facts, *, strategy="priority", master=True, black=True, low=True):
    return health_deprioritization_category(
        facts,
        strategy=strategy,
        deprioritize_failed=master,
        deprioritize_black_screen=black,
        deprioritize_low_fps=low,
    )


def test_health_category_points_never_claims_deprioritization():
    facts = _healthy(1, failed=True, black_screen=True, low_fps=True)

    assert _health_category(facts, strategy="points") is None


def test_health_category_master_off_makes_category_toggles_inert():
    facts = StreamFacts(
        stream_id=1,
        probe_stats_available=False,
        failed=True,
        black_screen=True,
        low_fps=True,
    )

    assert _health_category(facts, master=False, black=True, low=True) is None


def test_health_category_missing_stats_uses_failed_bucket_and_not_probed_reason():
    facts = StreamFacts(stream_id=1, probe_stats_available=False)
    category = _health_category(facts)

    assert category == "failed"
    assert health_deprioritization_reason(facts, category, None) == "not_probed"


def test_health_category_pending_precedes_enabled_black_and_low_fps():
    facts = _healthy(
        1,
        probe_succeeded=False,
        failed=True,
        black_screen=True,
        low_fps=True,
    )
    category = _health_category(facts)

    assert category == "failed"
    assert health_deprioritization_reason(facts, category, "pending") == "pending"


@pytest.mark.parametrize(
    ("facts", "black", "low", "expected"),
    [
        (_healthy(1, black_screen=True), True, False, "black_screen"),
        (_healthy(2, low_fps=True), False, True, "low_fps"),
    ],
)
def test_health_category_uses_each_enabled_category(facts, black, low, expected):
    assert _health_category(facts, black=black, low=low) == expected


@pytest.mark.parametrize(
    ("criterion", "lower", "higher"),
    [
        (
            "resolution",
            {"resolution_height": 720},
            {"resolution_height": 1080},
        ),
        (
            "framerate",
            {"framerate": 29.97},
            {"framerate": 60.0},
        ),
        (
            "m3u_priority",
            {"m3u_priority": 10},
            {"m3u_priority": 100},
        ),
        (
            "audio_channels",
            {"audio_channels": 2},
            {"audio_channels": 6},
        ),
        (
            "custom_streams",
            {"is_custom": False},
            {"is_custom": True},
        ),
        (
            "catchup",
            {"is_catchup": False},
            {"is_catchup": True},
        ),
    ],
)
def test_priority_mode_orders_each_normalized_criterion_descending(
    criterion, lower, higher
):
    streams = [_healthy(1, **lower), _healthy(2, **higher)]

    assert sort_streams_by_priority(streams, [criterion]) == [2, 1]


def test_priority_mode_bitrate_prefers_video_bitrate_then_overall_bitrate():
    streams = [
        _healthy(1, video_bitrate=3_000_000, bitrate=9_000_000),
        _healthy(2, video_bitrate=4_000_000, bitrate=5_000_000),
        _healthy(3, video_bitrate=None, bitrate=8_000_000),
    ]

    assert sort_streams_by_priority(streams, ["bitrate"]) == [3, 2, 1]


def test_priority_mode_preserves_current_codec_order_and_aliases():
    streams = [
        _healthy(61, video_codec="mpeg2"),
        _healthy(50, video_codec="VP8"),
        _healthy(41, video_codec="avc"),
        _healthy(30, video_codec="vp9"),
        _healthy(21, video_codec="H265"),
        _healthy(10, video_codec="AV1"),
        _healthy(60, video_codec="mpeg2video"),
        _healthy(40, video_codec="h264"),
        _healthy(20, video_codec="hevc"),
    ]

    assert sort_streams_by_priority(streams, ["video_codec"]) == [
        10,
        20,
        21,
        30,
        40,
        41,
        50,
        60,
        61,
    ]


def test_priority_mode_missing_probe_values_sort_as_zero():
    missing = StreamFacts(stream_id=1, probe_succeeded=True)
    populated = _healthy(2)

    assert sort_streams_by_priority(
        [missing, populated],
        ["resolution", "bitrate", "framerate", "video_codec", "audio_channels"],
    ) == [2, 1]


def test_priority_mode_unprobed_streams_still_use_non_probe_criteria():
    streams = [
        StreamFacts(
            stream_id=1,
            m3u_priority=10,
            is_custom=False,
            is_catchup=False,
            failed=True,
        ),
        StreamFacts(
            stream_id=2,
            m3u_priority=50,
            is_custom=True,
            is_catchup=True,
            failed=True,
        ),
    ]

    assert sort_streams_by_priority(
        streams,
        ["m3u_priority", "custom_streams", "catchup"],
        deprioritize_failed=False,
    ) == [2, 1]


def test_priority_mode_uses_health_buckets_before_criteria():
    streams = [
        _healthy(4, resolution_height=720),
        _healthy(3, resolution_height=2160, black_screen=True),
        _healthy(2, resolution_height=2160, low_fps=True),
        _healthy(
            1,
            probe_succeeded=False,
            resolution_height=2160,
            failed=True,
        ),
    ]

    assert sort_streams_by_priority(
        streams,
        ["resolution"],
        failed_stream_sort_order=["black_screen", "low_fps", "failed"],
    ) == [4, 3, 2, 1]


def test_priority_mode_per_category_health_toggles_are_independent():
    streams = [
        _healthy(1, resolution_height=2160, black_screen=True),
        _healthy(2, resolution_height=1080, low_fps=True),
        _healthy(3, resolution_height=720),
    ]

    assert sort_streams_by_priority(
        streams,
        ["resolution"],
        deprioritize_black_screen=False,
        deprioritize_low_fps=True,
    ) == [1, 3, 2]

    assert sort_streams_by_priority(
        streams,
        ["resolution"],
        deprioritize_black_screen=True,
        deprioritize_low_fps=False,
    ) == [2, 3, 1]


def test_priority_mode_suppresses_stale_probe_metrics_without_a_health_bucket():
    streams = [
        _healthy(
            1,
            probe_succeeded=False,
            resolution_height=2160,
            m3u_priority=10,
            failed=True,
        ),
        _healthy(
            2,
            probe_succeeded=False,
            resolution_height=720,
            m3u_priority=50,
            failed=True,
        ),
    ]

    assert sort_streams_by_priority(
        streams,
        ["resolution", "m3u_priority"],
        deprioritize_failed=False,
    ) == [2, 1]


def test_priority_mode_criteria_fall_through_in_configured_order():
    streams = [
        _healthy(1, resolution_height=1080, framerate=30.0),
        _healthy(2, resolution_height=1080, framerate=60.0),
        _healthy(3, resolution_height=720, framerate=120.0),
    ]

    assert sort_streams_by_priority(
        streams, ["resolution", "framerate"]
    ) == [2, 1, 3]


def test_priority_mode_final_ties_use_ascending_stream_id():
    streams = [_healthy(30), _healthy(10), _healthy(20)]

    assert sort_streams_by_priority(streams, ["resolution"]) == [10, 20, 30]
    assert sort_streams_by_priority(streams, []) == [10, 20, 30]


def test_points_mode_adds_every_matching_signed_rule_for_all_facts():
    facts = StreamFacts(
        stream_id=7,
        probe_succeeded=False,
        resolution_height=1080,
        video_bitrate=7_000_000,
        bitrate=9_000_000,
        framerate=60.0,
        video_codec="hevc",
        m3u_priority=50,
        audio_channels=6,
        is_custom=True,
        is_catchup=False,
        failed=True,
        black_screen=True,
        low_fps=False,
    )
    rules = [
        PointRule("resolution", ">=", 1080, 10),
        PointRule("resolution", "<=", 1080, 11),
        PointRule("bitrate", ">", 6_000_000, 7),
        PointRule("framerate", "==", 60, 3),
        PointRule("video_codec", ">=", "h265", 4),
        PointRule("m3u_priority", "==", 50, 2),
        PointRule("audio_channels", ">=", 6, 1),
        PointRule("custom_streams", "==", True, 5),
        PointRule("catchup", "==", False, 6),
        PointRule("failed", "==", True, -20),
        PointRule("black_screen", "==", True, -8),
        PointRule("low_fps", "==", False, 9),
    ]

    assert evaluate_points(facts, rules) == 30
    assert evaluate_points(facts, list(reversed(rules))) == 30


def test_points_mode_accepts_arbitrary_size_integers():
    facts = _healthy(1, m3u_priority=10**400)
    rules = [PointRule("m3u_priority", ">", 10**399, 7)]

    assert evaluate_points(facts, rules) == 7


def test_points_mode_missing_or_malformed_values_do_not_match():
    facts = StreamFacts(
        stream_id=1,
        resolution_height="1080",  # type: ignore[arg-type]
        video_bitrate="7000000",  # type: ignore[arg-type]
        framerate=float("nan"),
        video_codec="unknown",
        m3u_priority=None,
        audio_channels="6",  # type: ignore[arg-type]
        is_custom=None,
        is_catchup=None,
        black_screen=None,
        low_fps=None,
    )
    rules = [
        PointRule("resolution", ">=", 1080, 1),
        PointRule("bitrate", ">=", 7_000_000, 1),
        PointRule("framerate", ">=", 30, 1),
        PointRule("video_codec", "==", "h264", 1),
        PointRule("m3u_priority", ">", "bad", 1),
        PointRule("audio_channels", ">=", 6, 1),
        PointRule("custom_streams", "==", False, 1),
        PointRule("catchup", "==", False, 1),
        PointRule("failed", "==", False, 1),
        PointRule("black_screen", "==", False, 1),
        PointRule("low_fps", "==", False, 1),
    ]

    assert evaluate_points(facts, rules) == 0


def test_points_mode_has_no_hidden_health_or_priority_bucket():
    healthy = _healthy(1, resolution_height=720, m3u_priority=100)
    failed = replace(
        _healthy(2, resolution_height=2160, m3u_priority=0),
        probe_succeeded=False,
        failed=True,
        black_screen=True,
        low_fps=True,
    )
    rules = [
        PointRule("resolution", ">=", 2160, 50),
        PointRule("failed", "==", True, -10),
        PointRule("black_screen", "==", True, -10),
        PointRule("low_fps", "==", True, -10),
    ]

    assert evaluate_points(failed, rules) == 20
    assert evaluate_points(healthy, rules) == 0
    assert sort_streams_by_points([healthy, failed], rules) == [2, 1]


def test_points_mode_sorts_by_total_descending_then_stream_id_ascending():
    streams = [
        _healthy(30, resolution_height=1080),
        _healthy(10, resolution_height=1080),
        _healthy(20, resolution_height=720),
    ]
    rules = [PointRule("resolution", ">=", 1080, 5)]

    assert sort_streams_by_points(streams, rules) == [10, 30, 20]
    assert sort_streams_by_points(streams, []) == [10, 20, 30]


def test_strategy_dispatcher_selects_points_without_priority_health_buckets():
    healthy = _healthy(1, resolution_height=720)
    failed = _healthy(
        2,
        probe_succeeded=False,
        resolution_height=2160,
        failed=True,
        black_screen=True,
        low_fps=True,
    )

    assert sort_streams(
        [healthy, failed],
        strategy="points",
        point_rules=(
            PointRule("resolution", "gte", 2160, 50),
            PointRule("failed", "eq", True, -10),
            PointRule("black_screen", "eq", True, -10),
            PointRule("low_fps", "eq", True, -10),
        ),
        priority_criteria=("resolution",),
    ) == [2, 1]


def test_strategy_dispatcher_rejects_unknown_strategy():
    with pytest.raises(ValueError, match="Unknown Smart Sort strategy"):
        sort_streams([_healthy(1)], strategy="mystery")


def test_stream_metadata_criteria_follow_only_the_active_strategy():
    point_rules = (
        PointRule("custom_streams", "eq", True, 5),
        PointRule("resolution", "gte", 1080, 10),
    )

    assert stream_metadata_criteria(
        "priority",
        priority_criteria=("m3u_priority", "catchup", "resolution"),
        point_rules=point_rules,
    ) == frozenset({"m3u_priority", "catchup"})
    assert stream_metadata_criteria(
        "points",
        priority_criteria=("m3u_priority", "catchup"),
        point_rules=point_rules,
    ) == frozenset({"custom_streams"})
