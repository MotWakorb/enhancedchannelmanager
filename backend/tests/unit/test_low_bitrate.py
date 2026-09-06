"""GH980's frozen classification, persistence and sorting contract."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from config import DispatcharrSettings, StreamSortPointRule
from models import StreamStats
from smart_sort_evaluator import PointRule, StreamFacts, evaluate_points, sort_streams
from stream_prober import StreamProber


@pytest.fixture
def prober(test_engine, monkeypatch, tmp_path):
    monkeypatch.setattr("stream_prober.get_session", sessionmaker(bind=test_engine))
    monkeypatch.setattr("stream_prober.PROBE_HISTORY_FILE", tmp_path / "probe_history.json")
    return StreamProber(MagicMock())


def metadata(width=1920, height=1080, **video):
    return {"streams": [{"codec_type": "video", "width": width,
                         "height": height, **video}]}


@pytest.mark.parametrize("size", [(640, 480), (1920, 1080), (3840, 2160)])
@pytest.mark.parametrize("delta,expected", [(-1, True), (0, False), (1, False)])
@pytest.mark.parametrize("fps", ["10/1", "60/1"])
def test_resolution_floor_strict_boundary_independent_of_fps(prober, size, delta, expected, fps):
    width, height = size
    result = prober._save_probe_result(1, "local", metadata(width, height, r_frame_rate=fps),
                                       "success", None, width * height + delta)
    assert result["probe_status"] == "success"
    assert result["is_low_bitrate"] is expected


@pytest.mark.parametrize("data,measured,expected", [
    (metadata(bit_rate="100"), 3000000, False),
    (metadata(bit_rate="3000000"), 100, True),
    (metadata(bit_rate="100"), None, True),
    (metadata(tags={"BPS": "100"}), None, True),
    (metadata(tags={"BPS-eng": "100"}), None, True),
    ({**metadata(), "format": {"bit_rate": "100"}}, None, True),
    (metadata(), None, False),
    ({}, 100, False),
    (metadata(width=0), 100, False),
    (metadata(height=-1), 100, False),
    (metadata(width=float("inf")), 100, False),
    (metadata(bit_rate="nan"), None, False),
    (metadata(bit_rate=float("inf")), None, False),
    ({**metadata(), "format": {"bit_rate": float("inf")}}, None, False),
    (metadata(bit_rate="0"), None, False),
    (metadata(bit_rate="-1"), None, False),
    (metadata(), float("nan"), False),
])
def test_fresh_inputs_not_persisted_fields(prober, data, measured, expected):
    prober._save_probe_result(1, "local", metadata(bit_rate="100"), "success", None)
    result = prober._save_probe_result(1, "local", data, "success", None, measured)
    assert result["is_low_bitrate"] is expected


@pytest.mark.parametrize("status", ["failed", "timeout", "pending", "success"])
def test_unknown_or_failure_clears_previous_flag(prober, status):
    assert prober._save_probe_result(1, "local", metadata(), "success", None, 100)["is_low_bitrate"]
    assert not prober._save_probe_result(1, "local", None, status, None)["is_low_bitrate"]


def test_real_generated_local_ffprobe_capture(prober):
    data = json.loads((Path(__file__).parents[1] / "fixtures/gh980_generated_local_ffprobe.json").read_text())
    result = prober._save_probe_result(1, "generated local media", data, "success", None)
    assert result["video_bitrate"] == 1514264
    assert result["bitrate"] == 1522296
    assert result["resolution"] == "640x480"
    assert result["is_low_bitrate"] is False
    prober.low_bitrate_threshold = 5.0
    assert prober._save_probe_result(1, "local", data, "success", None)["is_low_bitrate"] is True


@pytest.mark.asyncio
async def test_resdet_effective_dimensions_reach_classifier(prober):
    prober.use_resdet_for_resolution = True
    prober._run_ffprobe = AsyncMock(return_value=metadata())
    prober._run_resdet = AsyncMock(return_value=(640, 480))
    prober._measure_stream_bitrate = AsyncMock(return_value=400000)
    prober._push_stats_to_dispatcharr = AsyncMock()
    result = await prober.probe_stream(1, "https://provider.example/local.ts", "local")
    assert result["resolution"] == "640x480"
    assert result["is_low_bitrate"] is False


def test_defaults_and_saved_custom_order():
    settings = DispatcharrSettings(failed_stream_sort_order=["low_fps", "failed", "black_screen"])
    assert settings.low_bitrate_threshold == 1.0
    assert settings.deprioritize_low_bitrate is False
    assert settings.failed_stream_sort_order == ["low_fps", "failed", "black_screen", "low_bitrate"]
    assert not any(rule.criterion == "low_bitrate" for rule in settings.stream_sort_point_rules)


@pytest.mark.parametrize("enabled", [False, True])
def test_classification_does_not_depend_on_sort_switches(prober, enabled):
    prober.deprioritize_failed_streams = enabled
    prober.deprioritize_low_bitrate = enabled
    assert prober._save_probe_result(1, "local", metadata(), "success", None, 100)["is_low_bitrate"] is True


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_threshold_positive_finite(threshold):
    with pytest.raises(ValidationError):
        DispatcharrSettings(low_bitrate_threshold=threshold)


def test_threshold_has_no_arbitrary_upper_bound():
    assert DispatcharrSettings(low_bitrate_threshold=1e100).low_bitrate_threshold == 1e100


def test_finite_arithmetic_guard(prober):
    prober.low_bitrate_threshold = 1e308
    assert not prober._save_probe_result(1, "local", metadata(), "success", None, 100)["is_low_bitrate"]


def test_direct_quality_and_numeric_bitrate_sort_ignore_low_bitrate_flag():
    from channel_pipeline_engine import _sort_streams_by_resolution_height
    settings = DispatcharrSettings(deprioritize_low_bitrate=True)
    stats = {1: {"probe_status": "success", "resolution": "3840x2160", "is_low_bitrate": True},
             2: {"probe_status": "success", "resolution": "1920x1080", "is_low_bitrate": False}}
    assert _sort_streams_by_resolution_height([2, 1], stats, settings, "desc", "local") == [1, 2]
    facts = [StreamFacts(1, probe_succeeded=True, video_bitrate=3000000, bitrate=100, low_bitrate=True),
             StreamFacts(2, probe_succeeded=True, video_bitrate=1000000, bitrate=9000000, low_bitrate=False)]
    assert sort_streams(facts, strategy="priority", priority_criteria=["bitrate"]) == [1, 2]
    assert evaluate_points(facts[0], [PointRule("bitrate", "gt", 2000000, 10)]) == 10


@pytest.mark.asyncio
async def test_persisted_classification_serializes_through_stats_api(prober, async_client):
    prober._save_probe_result(1, "local", metadata(), "success", None, 100)
    response = await async_client.get("/api/stream-stats/1")
    assert response.status_code == 200
    assert response.json()["is_low_bitrate"] is True
    prober.low_bitrate_threshold = 0.000001
    response = await async_client.get("/api/stream-stats/1")
    assert response.json()["is_low_bitrate"] is True


@pytest.mark.parametrize("global_gate,toggle,expected", [(True, False, [1, 2]), (False, True, [1, 2]), (True, True, [2, 1])])
def test_priority_gates(global_gate, toggle, expected):
    facts = [StreamFacts(1, probe_succeeded=True, low_bitrate=True), StreamFacts(2, probe_succeeded=True)]
    assert sort_streams(facts, strategy="priority", deprioritize_failed=global_gate,
                        deprioritize_low_bitrate=toggle) == expected


@pytest.mark.parametrize("value", [True, False])
def test_points_is_explicit_boolean_only(value):
    StreamSortPointRule(criterion="low_bitrate", operator="eq", value=value, points=-10)
    facts = StreamFacts(1, probe_succeeded=True, low_bitrate=value)
    assert evaluate_points(facts, []) == 0
    assert evaluate_points(facts, [PointRule("low_bitrate", "eq", value, -10)]) == -10
    assert evaluate_points(facts, [PointRule("low_bitrate", "eq", not value, -10)]) == 0


def test_serialization_default_false():
    assert StreamStats(stream_id=1).to_dict()["is_low_bitrate"] is False


@pytest.mark.parametrize("strategy,value", [("priority", True), ("points", True), ("points", False)])
def test_smart_sort_adapters_agree(strategy, value):
    from channel_pipeline_engine import _smart_sort_streams
    from config import stream_sort_point_rules_for_evaluator
    from stream_prober import smart_sort_streams

    settings = DispatcharrSettings(
        deprioritize_low_bitrate=True, stream_sort_strategy=strategy,
        stream_sort_point_rules=[{"criterion": "low_bitrate", "operator": "eq", "value": value, "points": -10}],
    )
    stats = {sid: StreamStats(stream_id=sid, probe_status="success", resolution="1920x1080",
                             is_low_bitrate=low, is_low_fps=False, is_black_screen=False)
             for sid, low in [(1, value), (2, not value)]}
    expected = [2, 1]
    assert smart_sort_streams(list(stats), stats, deprioritize_low_bitrate=True,
                              stream_sort_strategy=strategy,
                              stream_sort_point_rules=stream_sort_point_rules_for_evaluator(settings)) == expected
    assert _smart_sort_streams(list(stats), {sid: row.to_dict() for sid, row in stats.items()}, {}, settings=settings) == expected


def test_overlap_precedence_and_custom_order():
    from smart_sort_evaluator import health_deprioritization_category
    facts = StreamFacts(1, probe_succeeded=True, low_fps=True, low_bitrate=True)
    assert health_deprioritization_category(facts, strategy="priority", deprioritize_failed=True,
                                            deprioritize_black_screen=True, deprioritize_low_fps=True,
                                            deprioritize_low_bitrate=True) == "low_fps"
    assert sort_streams([facts, StreamFacts(2, low_bitrate=True)], strategy="priority",
                        deprioritize_low_bitrate=True, failed_stream_sort_order=["low_bitrate", "low_fps", "failed", "black_screen"]) == [2, 1]


def test_live_sort_snapshot_and_results(prober):
    prober.update_sort_settings([], {}, {}, deprioritize_low_bitrate=True)
    assert prober._sort_settings_snapshot()["deprioritize_low_bitrate"] is True
    results = prober.get_probe_results()
    assert results["low_bitrate_count"] == 0
    assert results["low_bitrate_streams"] == []


def test_self_healing_prober_uses_configured_threshold_and_toggle(monkeypatch):
    from stream_prober import ensure_prober

    settings = DispatcharrSettings(
        url="https://fixture.invalid", username="fixture", password="<synthetic-password>",
        low_bitrate_threshold=0.25, deprioritize_low_bitrate=True,
    )
    monkeypatch.setattr("stream_prober._prober", None)
    monkeypatch.setattr("config.get_settings", lambda: settings)
    monkeypatch.setattr("dispatcharr_client.get_client", MagicMock())
    created = ensure_prober()
    assert created.low_bitrate_threshold == 0.25
    assert created.deprioritize_low_bitrate is True


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [False, True])
async def test_probe_all_envelope_tracks_low_bitrate(prober, parallel):
    prober.parallel_probing_enabled = parallel
    prober.client = AsyncMock()
    prober.client.get_m3u_accounts.return_value = []
    prober.client.get_channel_stats.return_value = {"channels": []}
    prober._fetch_channel_stream_ids = AsyncMock(return_value=({1}, {}, {}))
    prober._fetch_all_streams = AsyncMock(return_value=[{"id": 1, "name": "Local", "url": "https://fixture.invalid/local.ts"}])
    prober._run_ffprobe = AsyncMock(return_value=metadata())
    prober._measure_stream_bitrate = AsyncMock(return_value=100)
    prober._push_stats_to_dispatcharr = AsyncMock()
    result = await prober.probe_all_streams(skip_m3u_refresh=True)
    assert result["status"] == "completed"
    assert prober.get_probe_results()["low_bitrate_count"] == 1
    assert prober.get_probe_results()["success_count"] == 1


@pytest.mark.asyncio
async def test_bulk_probe_persists_flag_and_history_independent_of_toggle(prober):
    prober._fetch_all_streams = AsyncMock(return_value=[{"id": 1, "name": "Local test", "url": "https://fixture.invalid/local.ts"}])
    prober._run_ffprobe = AsyncMock(return_value=metadata())
    prober._measure_stream_bitrate = AsyncMock(return_value=100)
    prober._push_stats_to_dispatcharr = AsyncMock()
    await prober.probe_streams_by_ids([1])
    assert prober.get_probe_results()["success_count"] == 1
    assert prober.get_probe_results()["low_bitrate_count"] == 1
    assert prober.get_probe_results()["low_bitrate_streams"][0]["id"] == 1
    assert prober.get_probe_progress()["low_bitrate_count"] == 1
    assert prober._probe_history[0]["low_bitrate_count"] == 1
    assert prober._probe_history[0]["low_bitrate_streams"][0]["id"] == 1
    prober._measure_stream_bitrate = AsyncMock(return_value=3000000)
    await prober.probe_streams_by_ids([1])
    assert prober.get_probe_results()["low_bitrate_count"] == 0


@pytest.mark.asyncio
async def test_low_bitrate_completion_is_warning_not_perfect_health(prober):
    prober._probe_progress_total = 1
    prober._probe_progress_success_count = 1
    prober._probe_progress_low_bitrate_count = 1
    prober._probe_notification_id = 1
    prober._notification_update_callback = AsyncMock()
    await prober._finalize_probe_notification()
    assert prober._notification_update_callback.call_args.kwargs["notification_type"] == "warning"
    assert "1 low bitrate" in prober._notification_update_callback.call_args.kwargs["message"]
