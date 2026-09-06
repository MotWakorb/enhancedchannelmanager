"""GH858: global matched stop, with unchanged unmatched/else semantics."""

from unittest.mock import AsyncMock, patch

import pytest

from normalization_engine import NormalizationEngine
from tests.fixtures.factories import (
    create_normalization_rule,
    create_normalization_rule_group,
)


@pytest.fixture
def stop_rules(test_session):
    group = create_normalization_rule_group(test_session, priority=0)
    rule = create_normalization_rule(
        test_session, group_id=group.id, priority=0,
        condition_value="Los Angeles Clippers", action_type="replace",
        action_value="LA Clippers", stop_processing=True,
    )
    return group, rule


@pytest.mark.parametrize("mode", ["title", "lower", "upper"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("no_change", [False, True])
@pytest.mark.asyncio
async def test_matched_stop_preserves_case_across_preview_and_execution(
    async_client, test_session, stop_rules, mode, compact, no_change,
):
    group, rule = stop_rules
    if compact:
        rule.condition_value = "LosAngelesClippers"
        rule.action_value = "LAClippers"
    if no_change:
        rule.condition_value = rule.action_value
    later = create_normalization_rule_group(test_session, priority=1)
    recase = create_normalization_rule(
        test_session, group_id=later.id, condition_type="always",
        action_type="capitalize", action_value=mode,
    )
    engine = NormalizationEngine(test_session)
    direct = engine.normalize(rule.condition_value, group_ids=[group.id, later.id])
    assert direct.normalized == rule.action_value
    assert direct.rules_applied == ([] if no_change else [rule.id])
    assert direct.transformations == ([] if no_change else [(rule.id, rule.condition_value, rule.action_value)])
    for endpoint in ("test-batch", "normalize"):
        response = await async_client.post(
            f"/api/normalization/{endpoint}", json={"texts": [rule.condition_value]},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["normalized"] == rule.action_value

    # The later rule really can transform the result when stop is unchecked.
    rule.stop_processing = False
    test_session.commit()
    continued = NormalizationEngine(test_session).normalize(rule.condition_value)
    expected = {
        "title": "LAClippers" if compact else "La Clippers",
        "lower": rule.action_value.lower(),
        "upper": rule.action_value.upper(),
    }[mode]
    assert continued.normalized == expected
    if continued.normalized != rule.action_value:
        assert recase.id in continued.rules_applied


@pytest.mark.parametrize("no_change", [False, True])
def test_matched_stop_skips_repeat_passes_and_same_group_rules(
    test_session, stop_rules, no_change,
):
    group, rule = stop_rules
    if no_change:
        rule.condition_value = rule.action_value
    create_normalization_rule(
        test_session, group_id=group.id, priority=-1,
        condition_value="LA Clippers", action_type="replace", action_value="WRONG",
    )
    create_normalization_rule(
        test_session, group_id=group.id, priority=1,
        condition_type="always", action_type="replace", action_value="WRONG",
    )
    # For a no-change match the earlier rule must not match the initial text.
    if no_change:
        rule.priority = -2
        test_session.commit()
    result = NormalizationEngine(test_session).normalize(rule.condition_value)
    assert result.normalized == "LA Clippers"
    assert result.rules_applied == ([] if no_change else [rule.id])


@pytest.mark.parametrize("no_change", [False, True])
def test_matched_stop_skips_legacy_tags_and_whitespace_cleanup(
    test_session, stop_rules, no_change,
):
    _, rule = stop_rules
    rule.action_value = " LA  Clippers HD "
    if no_change:
        rule.condition_value = "LA  Clippers HD"
        rule.action_value = rule.condition_value
    test_session.commit()
    with patch("config.get_settings") as settings:
        settings.return_value.custom_normalization_tags = [{"value": "HD", "mode": "suffix"}]
        result = NormalizationEngine(test_session).normalize(rule.condition_value)
    assert result.normalized == rule.action_value
    assert result.rules_applied == ([] if no_change else [rule.id])


@pytest.mark.parametrize("inactive", ["unmatched", "rule", "group"])
def test_unmatched_and_disabled_stops_do_not_halt(test_session, stop_rules, inactive):
    group, rule = stop_rules
    if inactive == "unmatched":
        rule.condition_value = "Other"
    elif inactive == "rule":
        rule.enabled = False
    else:
        group.enabled = False
    later = create_normalization_rule_group(test_session, priority=1)
    create_normalization_rule(
        test_session, group_id=later.id, condition_type="always",
        action_type="capitalize", action_value="lower",
    )
    assert NormalizationEngine(test_session).normalize("LA Clippers").normalized == "la clippers"


@pytest.mark.parametrize("else_changes", [False, True])
def test_else_stop_remains_group_local_with_cleanup_and_repeats(
    test_session, stop_rules, else_changes,
):
    group, rule = stop_rules
    rule.else_action_type = "replace" if else_changes else "remove"
    rule.else_action_value = " LA  Clippers HD"
    create_normalization_rule(
        test_session, group_id=group.id, priority=1,
        condition_type="always", action_type="replace", action_value="WRONG",
    )
    later = create_normalization_rule_group(test_session, priority=1)
    recase = create_normalization_rule(
        test_session, group_id=later.id, condition_type="always",
        action_type="capitalize", action_value="lower",
    )
    engine = NormalizationEngine(test_session)
    with patch("config.get_settings") as settings, patch.object(
        engine, "_apply_rules_single_pass", wraps=engine._apply_rules_single_pass,
    ) as passes:
        settings.return_value.custom_normalization_tags = [{"value": "HD", "mode": "suffix"}]
        result = engine.normalize("LA  Clippers HD")
    assert result.normalized == "la clippers"
    assert recase.id in result.rules_applied
    assert passes.call_count == 2


@pytest.mark.asyncio
async def test_mapping_precedes_matched_stop_in_batch_and_direct_execution(
    async_client, test_session, stop_rules,
):
    from models import ChannelNameAlias, ChannelNameMapping

    _, rule = stop_rules
    mapping = ChannelNameMapping(preferred_name="Mapped Clippers HD")
    test_session.add(mapping)
    test_session.flush()
    test_session.add(ChannelNameAlias(
        mapping_id=mapping.id, name=rule.condition_value,
        match_key=rule.condition_value.casefold(),
    ))
    test_session.commit()
    result = NormalizationEngine(test_session).normalize(rule.condition_value)
    assert result.normalized == mapping.preferred_name
    assert result.rules_applied == []
    for endpoint in ("test-batch", "normalize"):
        response = await async_client.post(
            f"/api/normalization/{endpoint}", json={"texts": [rule.condition_value]},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["normalized"] == mapping.preferred_name


@pytest.mark.parametrize("matched", [False, True])
@pytest.mark.parametrize("stop", [False, True])
@pytest.mark.asyncio
async def test_single_rule_preview_only_skips_cleanup_on_matched_stop(async_client, matched, stop):
    response = await async_client.post("/api/normalization/test", json={
        "text": "Los Angeles Clippers", "condition_type": "contains",
        "condition_value": "Los Angeles Clippers" if matched else "Other",
        "action_type": "replace", "action_value": " LA  Clippers ",
        "else_action_type": "replace", "else_action_value": " LA  Clippers ",
        "stop_processing": stop,
    })
    assert response.status_code == 200
    assert response.json()["matched"] is matched
    assert response.json()["after"] == (" LA  Clippers " if matched and stop else "LA Clippers")


@pytest.mark.asyncio
async def test_bulk_apply_preview_and_execute_preserve_exact_stopped_output(
    async_client, test_session, stop_rules,
):
    _, rule = stop_rules
    rule.action_value = " LA  Clippers "
    test_session.commit()
    client = AsyncMock()
    client.get_channels.return_value = {"results": [{"id": 1, "name": rule.condition_value}], "next": None}
    client.get_channel_groups.return_value = []
    with patch("routers.normalization.get_client", return_value=client), patch("routers.normalization.journal"):
        preview = await async_client.post("/api/normalization/apply-to-channels?dry_run=true")
        assert preview.status_code == 200
        assert preview.json()["diffs"][0]["proposed_name"] == rule.action_value
        response = await async_client.post(
            "/api/normalization/apply-to-channels?dry_run=false",
            json={"actions": [{"channel_id": 1, "action": "rename"}]},
        )
    assert response.status_code == 200
    client.update_channel.assert_awaited_once_with(1, {"name": rule.action_value})


@pytest.mark.asyncio
async def test_pipeline_create_consumes_exact_stopped_output(test_session, stop_rules):
    from channel_pipeline_executor import ActionExecutor, ExecutionContext, StreamContext

    group, rule = stop_rules
    rule.action_value = " LA  Clippers "
    later = create_normalization_rule_group(test_session, priority=1)
    create_normalization_rule(
        test_session, group_id=later.id, condition_type="always",
        action_type="capitalize", action_value="lower",
    )
    client = AsyncMock()
    client.create_channel.return_value = {"id": 1, "name": rule.action_value}
    executor = ActionExecutor(client, existing_channels=[], normalization_engine=NormalizationEngine(test_session))
    stream = StreamContext(
        stream_id=1, stream_name=rule.condition_value, m3u_account_id=1,
        m3u_account_name="Provider", group_name="NBA", tvg_id=None,
        resolution_height=None, logo_url=None,
    )
    result = await executor.execute(
        {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "skip"},
        stream, ExecutionContext(), normalization_group_ids=[group.id, later.id],
    )
    assert result.success is True
    assert client.create_channel.call_args[0][0]["name"] == rule.action_value
