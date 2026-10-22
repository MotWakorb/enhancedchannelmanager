import pytest

from config import DispatcharrSettings
from models import StreamStats
from smart_sort_evaluator import (
    PointRule, StreamFacts, evaluate_points, health_deprioritization_category, sort_streams,
)


def _facts(**kw):
    return StreamFacts(**{"probe_succeeded": True, "resolution_height": 1080, "bitrate": 5_000_000, **kw})


def test_category_tri_state_and_overlap():
    kwargs = dict(strategy="priority", deprioritize_failed=True, deprioritize_black_screen=True,
                  deprioritize_low_fps=True, deprioritize_low_bitrate=True, deprioritize_unstable=True)
    assert health_deprioritization_category(_facts(stream_id=1, unstable=True), **kwargs) == "unstable"
    assert health_deprioritization_category(_facts(stream_id=1, unstable=False), **kwargs) is None
    assert health_deprioritization_category(_facts(stream_id=1, unstable=None), **kwargs) is None
    assert health_deprioritization_category(_facts(stream_id=1, unstable=True, low_bitrate=True), **kwargs) == "low_bitrate"


@pytest.mark.parametrize("global_gate,toggle,expected", [
    (True, True, [2, 1]),
    (True, False, [1, 2]),
    (False, True, [1, 2]),
])
def test_priority_gates(global_gate, toggle, expected):
    facts = [_facts(stream_id=1, resolution_height=2160, unstable=True), _facts(stream_id=2, unstable=False)]
    assert sort_streams(facts, strategy="priority", priority_criteria=["resolution"],
                        deprioritize_failed=global_gate, deprioritize_unstable=toggle) == expected


def test_unmeasured_sorts_as_healthy():
    facts = [_facts(stream_id=1, resolution_height=2160, unstable=None), _facts(stream_id=2, unstable=False)]
    assert sort_streams(facts, strategy="priority", priority_criteria=["resolution"],
                        deprioritize_failed=True, deprioritize_unstable=True) == [1, 2]


def test_custom_order_places_unstable_before_failed():
    facts = [_facts(stream_id=1, failed=True), _facts(stream_id=2, unstable=True), _facts(stream_id=3)]
    assert sort_streams(facts, strategy="priority", deprioritize_failed=True, deprioritize_unstable=True,
                        failed_stream_sort_order=["unstable", "failed", "black_screen", "low_fps", "low_bitrate"]) == [3, 2, 1]


@pytest.mark.parametrize("value", [True, False])
def test_points_is_explicit_boolean_only(value):
    assert evaluate_points(_facts(stream_id=1, unstable=value), [PointRule("unstable", "eq", value, -10)]) == -10
    assert evaluate_points(_facts(stream_id=1, unstable=value), [PointRule("unstable", "eq", not value, -10)]) == 0
    assert evaluate_points(_facts(stream_id=1, unstable=None), [PointRule("unstable", "eq", value, -10)]) == 0


@pytest.mark.parametrize("strategy", ["priority", "points"])
def test_smart_sort_adapters_agree(strategy):
    from channel_pipeline_engine import _smart_sort_streams
    from config import stream_sort_point_rules_for_evaluator
    from stream_prober import smart_sort_streams
    settings = DispatcharrSettings(
        deprioritize_unstable=True, stream_sort_strategy=strategy,
        stream_sort_point_rules=[{"criterion": "unstable", "operator": "eq", "value": True, "points": -50}],
    )
    stats = {
        1: StreamStats(stream_id=1, probe_status="success", resolution="3840x2160", bitrate=8_000_000, is_unstable=True),
        2: StreamStats(stream_id=2, probe_status="success", resolution="1920x1080", bitrate=5_000_000, is_unstable=False),
    }
    expected = [2, 1]
    assert smart_sort_streams(list(stats), stats, deprioritize_unstable=True,
                              stream_sort_strategy=strategy,
                              stream_sort_point_rules=stream_sort_point_rules_for_evaluator(settings)) == expected
    assert _smart_sort_streams(list(stats), {sid: row.to_dict() for sid, row in stats.items()}, {}, settings=settings) == expected
