import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import config


VALID_RULES = [
    {"criterion": "resolution", "operator": "gte", "value": 1080, "points": 20},
    {"criterion": "bitrate", "operator": "gte", "value": 6000, "points": 25},
    {"criterion": "framerate", "operator": "lt", "value": 59.94, "points": 2},
    {"criterion": "m3u_priority", "operator": "ne", "value": -1, "points": 3},
    {"criterion": "audio_channels", "operator": "eq", "value": 2, "points": 4},
    {"criterion": "video_codec", "operator": "gte", "value": "h265", "points": 10},
    {"criterion": "custom_streams", "operator": "eq", "value": True, "points": 5},
    {"criterion": "catchup", "operator": "eq", "value": False, "points": -5},
    {"criterion": "failed", "operator": "eq", "value": True, "points": -40},
    {"criterion": "black_screen", "operator": "eq", "value": True, "points": -30},
    {"criterion": "low_fps", "operator": "eq", "value": True, "points": -20},
]


async def _post_settings(async_client, current, payload):
    with ExitStack() as stack:
        stack.enter_context(patch("routers.settings.get_settings", return_value=current))
        save = stack.enter_context(patch("routers.settings.save_settings"))
        stack.enter_context(patch("routers.settings.clear_settings_cache"))
        stack.enter_context(patch("routers.settings.reset_client"))
        stack.enter_context(patch("routers.settings.get_prober", return_value=None))
        stack.enter_context(patch("routers.settings.get_cache", return_value=MagicMock()))
        response = await async_client.post("/api/settings", json=payload)
    return response, save


@pytest.mark.asyncio
async def test_get_returns_resolved_smart_sort_defaults(async_client):
    settings = config.DispatcharrSettings()

    with patch("routers.settings.get_settings", return_value=settings), patch(
        "routers.settings._has_discord_alert_method", return_value=False
    ):
        response = await async_client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["stream_sort_strategy"] == "priority"
    assert response.json()["stream_sort_point_rules"] == []


@pytest.mark.asyncio
async def test_valid_point_settings_persist_round_trip_and_update_live_config(
    async_client, tmp_path, monkeypatch
):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path)
    monkeypatch.setattr(config, "MCP_KEY_FILE", tmp_path / config.MCP_KEY_FILENAME)
    config.clear_settings_cache()
    config.save_settings(
        config.DispatcharrSettings(
            theme="light",
            event_sync_team_aliases=[{"terms": ["A", "B"], "note": None}],
        )
    )

    try:
        with patch("routers.settings.reset_client"), patch(
            "routers.settings.get_prober", return_value=None
        ):
            response = await async_client.post(
                "/api/settings",
                json={
                    "url": "",
                    "username": "",
                    "theme": "light",
                    "stream_sort_strategy": "points",
                    "stream_sort_point_rules": VALID_RULES,
                },
            )

        assert response.status_code == 200, response.text
        live = config.get_settings()
        assert live.stream_sort_strategy == "points"
        assert [rule.model_dump() for rule in live.stream_sort_point_rules] == VALID_RULES
        assert live.theme == "light"
        assert live.event_sync_team_aliases == [
            {"terms": ["A", "B"], "note": None}
        ]

        persisted = json.loads(settings_file.read_text())
        assert persisted["stream_sort_strategy"] == "points"
        assert persisted["stream_sort_point_rules"] == VALID_RULES
        assert persisted["event_sync_team_aliases"] == [
            {"terms": ["A", "B"], "note": None}
        ]

        with patch("routers.settings._has_discord_alert_method", return_value=False):
            resolved = await async_client.get("/api/settings")
        assert resolved.status_code == 200
        assert resolved.json()["stream_sort_strategy"] == "points"
        assert resolved.json()["stream_sort_point_rules"] == VALID_RULES
    finally:
        config.clear_settings_cache()


@pytest.mark.asyncio
async def test_omitted_stream_sort_strategy_preserves_stored_value(async_client):
    current = config.DispatcharrSettings(
        stream_sort_strategy="points",
        stream_sort_point_rules=VALID_RULES[:2],
    )

    response, save = await _post_settings(
        async_client,
        current,
        {"url": "", "username": "", "stream_sort_point_rules": []},
    )

    assert response.status_code == 200
    saved = save.call_args.args[0]
    assert saved.stream_sort_strategy == "points"


@pytest.mark.asyncio
async def test_omitted_stream_sort_point_rules_preserves_stored_value(async_client):
    current = config.DispatcharrSettings(
        stream_sort_strategy="points",
        stream_sort_point_rules=VALID_RULES[:2],
    )

    response, save = await _post_settings(
        async_client,
        current,
        {"url": "", "username": "", "stream_sort_strategy": "priority"},
    )

    assert response.status_code == 200
    saved = save.call_args.args[0]
    assert [rule.model_dump() for rule in saved.stream_sort_point_rules] == VALID_RULES[:2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_name",
    ["stream_sort_strategy", "stream_sort_point_rules"],
)
async def test_explicit_null_smart_sort_setting_returns_actionable_422(
    async_client, field_name
):
    current = config.DispatcharrSettings(
        stream_sort_strategy="points",
        stream_sort_point_rules=VALID_RULES[:2],
    )

    response, save = await _post_settings(
        async_client,
        current,
        {"url": "", "username": "", field_name: None},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert len(detail) == 1
    assert detail[0]["loc"] == ["body", field_name]
    assert (
        f"{field_name} cannot be null; omit it to preserve the stored value"
        in detail[0]["msg"]
    )
    save.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_empty_point_rules_are_valid(async_client):
    current = config.DispatcharrSettings(
        stream_sort_strategy="points",
        stream_sort_point_rules=VALID_RULES[:1],
    )

    response, save = await _post_settings(
        async_client,
        current,
        {
            "url": "",
            "username": "",
            "stream_sort_strategy": "points",
            "stream_sort_point_rules": [],
        },
    )

    assert response.status_code == 200
    assert save.call_args.args[0].stream_sort_point_rules == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_values", "expected_location", "expected_message"),
    [
        (
            {"stream_sort_strategy": "weighted"},
            ["body", "stream_sort_strategy"],
            "Input should be 'priority' or 'points'",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "latency", "operator": "gte", "value": 1, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "criterion must be one of:",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": ">=", "value": 1, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "operator for 'bitrate' must be one of:",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "between", "value": 1, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "operator for 'bitrate' must be one of:",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "failed", "operator": "ne", "value": True, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "operator for 'failed' must be one of:",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": True, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'bitrate' must be a finite JSON number",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": "6000", "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'bitrate' must be a finite JSON number",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "failed", "operator": "eq", "value": 1, "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'failed' must be a JSON boolean",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "video_codec", "operator": "eq", "value": "theora", "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'video_codec' must be a recognized codec name or alias",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": float("nan"), "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'bitrate' must be a finite JSON number",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": float("inf"), "points": 1}]},
            ["body", "stream_sort_point_rules", 0],
            "value for 'bitrate' must be a finite JSON number",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": 1, "points": True}]},
            ["body", "stream_sort_point_rules", 0],
            "points must be a signed JSON integer",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": 1, "points": 1.5}]},
            ["body", "stream_sort_point_rules", 0],
            "points must be a signed JSON integer",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": 1, "points": "1"}]},
            ["body", "stream_sort_point_rules", 0],
            "points must be a signed JSON integer",
        ),
        (
            {"stream_sort_point_rules": [{"criterion": "bitrate", "operator": "gte", "value": 1, "points": 1, "enabled": True}]},
            ["body", "stream_sort_point_rules", 0, "enabled"],
            "Extra inputs are not permitted",
        ),
    ],
)
async def test_invalid_point_settings_return_actionable_422(
    async_client, field_values, expected_location, expected_message
):
    payload = {"url": "", "username": "", **field_values}
    rules = field_values.get("stream_sort_point_rules", [])
    non_finite = rules and isinstance(rules[0].get("value"), float) and not (
        float("-inf") < rules[0]["value"] < float("inf")
    )
    if non_finite:
        response = await async_client.post(
            "/api/settings",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
    else:
        response = await async_client.post("/api/settings", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert len(detail) == 1
    assert detail[0]["loc"] == expected_location
    assert expected_message in detail[0]["msg"]
