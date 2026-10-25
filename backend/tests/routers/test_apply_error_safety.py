"""Error messages cross the real JSON boundary without downstream diagnostics."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.routers.test_e9e5o_normalize_failure_disclosure import _commit_and_wait
from tests.routers.test_profile_reconcile_hooks import _mock_client

INTERNAL = (
    "internal-host.invalid/parser.py:91 api_key=private-apply-token\ntrace details"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_normalization_failure_keeps_landed_create_without_internal_text(
    async_client, bulk, caplog
):
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    client.create_channel.return_value = {"id": 11, "name": "Raw Name"}
    with (
        patch("routers.channels.get_client", return_value=client),
        patch(
            "routers.channels.get_normalization_engine",
            side_effect=RuntimeError(INTERNAL),
        ),
        patch("routers.channels.journal"),
    ):
        if bulk:
            body = await _commit_and_wait(
                async_client,
                {
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "Raw Name",
                            "normalize": True,
                        }
                    ]
                },
            )
            assert body["operationsApplied"] == 1
            error = body["normalizationFailures"][0]["error"]
        else:
            response = await async_client.post(
                "/api/channels", json={"name": "Raw Name", "normalize": True}
            )
            assert response.status_code == 200
            body = response.json()
            assert body["normalization"]["applied"] is False
            error = body["normalization"]["error"]
    assert error == "Normalization failed; the original channel name was used."
    assert "internal-host.invalid" not in str(body)
    assert "private-apply-token" not in str(body)
    assert client.create_channel.await_args.args[0]["name"] == "Raw Name"
    assert any(
        "internal-host.invalid" in record.getMessage() for record in caplog.records
    )
    assert all(
        "private-apply-token" not in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["settings", "live_rules", "group"])
async def test_m3u_apply_failure_is_safe_but_still_visible(
    async_client, failure_stage, caplog
):
    client = _mock_client()
    if failure_stage == "settings":
        client.get_all_m3u_group_settings.side_effect = RuntimeError(INTERNAL)
    rules = AsyncMock(return_value=set())
    reconcile = AsyncMock(return_value={"status": "reconciled", "group_id": 1304})
    if failure_stage == "live_rules":
        rules.side_effect = RuntimeError(INTERNAL)
    elif failure_stage == "group":
        reconcile.side_effect = RuntimeError(INTERNAL)
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch("services.profile_reconcile._resolve_live_rule_ids", rules),
        patch("services.profile_reconcile.reconcile_group_profiles", reconcile),
    ):
        response = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={
                "group_settings": [
                    {
                        "channel_group": 1304,
                        "custom_properties": {"channel_profile_ids": [12]},
                    }
                ],
            },
        )
    assert response.status_code == 200
    body = response.json()
    outcome = body["ecm_profile_apply"][0]
    assert outcome["status"] == "error"
    assert outcome["group_id"] == (1304 if failure_stage == "group" else None)
    assert outcome["error"] and "retry" in outcome["error"].lower()
    assert "internal-host.invalid" not in response.text
    assert "private-apply-token" not in response.text
    assert any(
        "internal-host.invalid" in record.getMessage() for record in caplog.records
    )
    assert all(
        "private-apply-token" not in record.getMessage() for record in caplog.records
    )
