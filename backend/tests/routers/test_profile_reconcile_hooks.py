"""Hook-level tests: the group-settings save path invokes the profile
reconcile for an edited group (GH #720 Part B / bead y3m6o, decision 3a).

The reconcile itself is unit-tested in tests/services/test_profile_reconcile.py;
here we pin only that the ``update_m3u_group_settings`` router wires it in —
so an operator's modal save applies the selection instantly instead of waiting
for the next sync. ``reconcile_group_profiles`` is patched to an AsyncMock so
the test asserts invocation without re-driving the Dispatcharr calls.
"""
from unittest.mock import AsyncMock, patch

import pytest


def _account_with_selection() -> dict:
    """M3U account whose group 1304 carries a channel_profile_ids selection."""
    return {
        "id": 11,
        "name": "HD Homerun",
        "channel_groups": [
            {
                "channel_group": 1304,
                "enabled": True,
                "auto_channel_sync": True,
                "auto_sync_channel_start": None,
                "auto_sync_channel_end": None,
                "custom_properties": {"channel_profile_ids": [12]},
            }
        ],
    }


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_m3u_account.return_value = _account_with_selection()
    client.get_channel_groups.return_value = [{"id": 1304, "name": "HD Homerun"}]
    client.update_m3u_group_settings.return_value = {"message": "ok"}
    # The save hook fetches fresh settings for override resolution.
    client.get_all_m3u_group_settings.return_value = {
        1304: {"custom_properties": {"channel_profile_ids": [12]}}
    }
    return client


@pytest.mark.asyncio
async def test_group_settings_save_invokes_reconcile_for_edited_group(async_client):
    client = _mock_client()
    fake_reconcile = AsyncMock(return_value={"status": "reconciled", "channels_scoped": 2})

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch(
             "services.profile_reconcile.reconcile_group_profiles",
             fake_reconcile,
         ):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
    fake_reconcile.assert_awaited_once()
    # Called with (client, fresh_settings, group_id=1304).
    call = fake_reconcile.await_args
    assert call.args[2] == 1304


@pytest.mark.asyncio
async def test_reconcile_failure_does_not_fail_the_save(async_client):
    """A reconcile error must never fail the save the operator just made."""
    client = _mock_client()
    boom = AsyncMock(side_effect=RuntimeError("reconcile boom"))

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile.reconcile_group_profiles", boom):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
