import json
import math

import pytest

import config
from config import DispatcharrSettings, stream_sort_point_rules_for_evaluator
from smart_sort_evaluator import PointRule


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path)
    monkeypatch.setattr(config, "MCP_KEY_FILE", tmp_path / config.MCP_KEY_FILENAME)
    config.clear_settings_cache()
    yield settings_file
    config.clear_settings_cache()


def test_legacy_settings_file_resolves_smart_sort_defaults(isolated_settings):
    isolated_settings.write_text(json.dumps({"theme": "light"}))

    settings = config.load_settings()

    assert settings.stream_sort_strategy == "priority"
    assert settings.stream_sort_point_rules == []
    assert settings.theme == "light"


def test_point_rules_round_trip_in_order_with_exact_public_shape():
    rules = [
        {"criterion": "bitrate", "operator": "gte", "value": 6000, "points": 25},
        {"criterion": "failed", "operator": "eq", "value": True, "points": -40},
    ]

    settings = DispatcharrSettings(
        stream_sort_strategy="points",
        stream_sort_point_rules=rules,
    )
    restored = DispatcharrSettings(**json.loads(json.dumps(settings.model_dump())))

    assert restored.stream_sort_strategy == "points"
    assert [rule.model_dump() for rule in restored.stream_sort_point_rules] == rules


@pytest.mark.parametrize("operator", ["eq", "ne", "gt", "gte", "lt", "lte"])
def test_all_canonical_ordered_operators_are_accepted(operator):
    settings = DispatcharrSettings(
        stream_sort_point_rules=[
            {"criterion": "bitrate", "operator": operator, "value": 1, "points": 1}
        ]
    )

    assert settings.stream_sort_point_rules[0].operator == operator


def test_evaluator_conversion_scales_only_bitrate_thresholds():
    settings = DispatcharrSettings(
        stream_sort_point_rules=[
            {"criterion": "bitrate", "operator": "gte", "value": 6000, "points": 25},
            {"criterion": "bitrate", "operator": "lt", "value": 59.94, "points": -5},
            {"criterion": "resolution", "operator": "gte", "value": 1080, "points": 10},
            {"criterion": "video_codec", "operator": "eq", "value": "h265", "points": 5},
            {"criterion": "failed", "operator": "eq", "value": True, "points": -50},
        ]
    )

    assert stream_sort_point_rules_for_evaluator(settings) == (
        PointRule("bitrate", "gte", 6_000_000, 25),
        PointRule("bitrate", "lt", 59_940.0, -5),
        PointRule("resolution", "gte", 1080, 10),
        PointRule("video_codec", "eq", "h265", 5),
        PointRule("failed", "eq", True, -50),
    )


def test_evaluator_conversion_keeps_large_finite_bitrate_threshold_finite():
    public_value = 1e308
    settings = DispatcharrSettings(
        stream_sort_point_rules=[
            {
                "criterion": "bitrate",
                "operator": "gte",
                "value": public_value,
                "points": 1,
            }
        ]
    )

    converted = stream_sort_point_rules_for_evaluator(settings)[0].value
    numerator, denominator = public_value.as_integer_ratio()

    assert converted == numerator * 1000 // denominator
    assert type(converted) is int or math.isfinite(converted)
