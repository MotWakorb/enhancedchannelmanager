"""98eah.3: direct quality dispatcher contract, separate from Smart Sort policy."""
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

import channel_pipeline_engine as engine


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("tie", ["asc", "desc", None, "invalid"])
@pytest.mark.parametrize("enabled", [False, True])
def test_quality_resolution_provider_directions_and_lookup_budget(order, tie, enabled):
    rule = SimpleNamespace(stream_sort_field="quality", stream_sort_order=order,
                           quality_tie_break_order=tie, quality_m3u_tie_break_enabled=enabled)
    settings = SimpleNamespace(deprioritize_failed_streams=False,
                               m3u_account_priorities={"1": 10, "custom": 5})
    stats = {sid: {"resolution": f"1920x{height}"} for sid, height in [(8, 720), (3, 1080), (2, 1080), (1, 1080)]}
    equal = [1, 2, 3] if not enabled else ([3, 2, 1] if tie == "asc" else [1, 2, 3])
    expected = [8, *equal] if order == "asc" else [*equal, 8]
    with patch.object(engine, "_m3u_account_priority_value", wraps=engine._m3u_account_priority_value) as priority:
        assert engine._reorder_streams_for_rule([8, 3, 2, 1], rule, stats, {1: 1, 3: 99}, "Ch", settings) == expected
    assert priority.call_args_list == ([call(sid, {1: 1, 3: 99}, settings) for sid in [8, 3, 2, 1]] if enabled else [])


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_quality_equal_keys_use_ascending_id_not_input_order(enabled, order):
    rule = SimpleNamespace(stream_sort_field="quality", stream_sort_order=order,
                           quality_m3u_tie_break_enabled=enabled)
    assert engine._reorder_streams_for_rule([9, 2, 7, 2], rule, {}, {}, "Ch", None) == [2, 2, 7, 9]


@pytest.mark.parametrize("failure_order,expected", [
    (None, [1, 4, 5, 3, 2]), ([], [1, 4, 5, 3, 2]),
    (["low_fps", "black_screen", "failed"], [1, 2, 3, 4, 5]),
    (["low_fps"], [1, 2, 3, 4, 5]),
    (["failed", "black_screen", "failed"], [1, 3, 2, 4, 5]),
])
@pytest.mark.parametrize("deprioritize", [False, True])
def test_quality_overlapping_health_precedence(failure_order, expected, deprioritize):
    stats = {
        1: {}, 2: {"is_low_fps": True},
        3: {"is_black_screen": True, "is_low_fps": True},
        4: {"probe_status": "failed", "is_black_screen": True, "is_low_fps": True},
        5: {"probe_status": "timeout", "is_black_screen": True, "is_low_fps": True},
    }
    settings = SimpleNamespace(deprioritize_failed_streams=deprioritize, failed_stream_sort_order=failure_order)
    rule = SimpleNamespace(stream_sort_field="quality")
    assert engine._reorder_streams_for_rule([5, 4, 3, 2, 1], rule, stats, {}, "Ch", settings) == (expected if deprioritize else [1, 2, 3, 4, 5])


@pytest.mark.parametrize("stats", [None, {}, {"resolution": None}, {"resolution": "bad"},
                                  {"resolution": "1xno"}, {"resolution": "1x2x3"}, [], 0])
def test_quality_missing_null_and_malformed_text_stats_count_as_zero(stats):
    rule = SimpleNamespace(stream_sort_field="quality", stream_sort_order="desc")
    assert engine._reorder_streams_for_rule([3, 2, 1], rule, {3: stats, 2: {"resolution": "1x1"}}, {}, "Ch", None) == [2, 1, 3]


@pytest.mark.parametrize("stats", [1, [1], {"resolution": 1080}, {"resolution": [1080]}])
def test_quality_unsupported_stats_still_raise(stats):
    with pytest.raises(AttributeError):
        engine._reorder_streams_for_rule([1], SimpleNamespace(stream_sort_field="quality"), {1: stats}, {}, "Ch", None)


@pytest.mark.parametrize("priority", [None, "high", []])
def test_quality_unsupported_priority_is_not_coerced(priority):
    settings = SimpleNamespace(m3u_account_priorities={"custom": priority})
    with pytest.raises(TypeError):
        engine._reorder_streams_for_rule([1], SimpleNamespace(stream_sort_field="quality"), {}, {}, "Ch", settings)
