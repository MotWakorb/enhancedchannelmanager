"""Hook-level tests: the group-settings save path and the post-refresh poll
invoke the profile reconcile (GH #720 Part B / bead y3m6o).

The reconcile itself is unit-tested in tests/services/test_profile_reconcile.py;
here we pin only that the router / poll hooks WIRE it in — a disconnected hook
would pass every service unit test yet never apply anything (Should-Fix 8).
``reconcile_group_profiles`` / ``reconcile_all_selected_groups`` are patched to
AsyncMocks so the tests assert invocation without re-driving Dispatcharr.
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
    client.get_channel_groups.return_value = [
        {"id": 1304, "name": "HD Homerun"},
        {"id": 1305, "name": "Sports"},
    ]
    client.update_m3u_group_settings.return_value = {"message": "ok"}
    # The save hook fetches fresh settings for override resolution.
    client.get_all_m3u_group_settings.return_value = {
        1304: {"custom_properties": {"channel_profile_ids": [12]}},
        1305: {"custom_properties": {"channel_profile_ids": [13]}},
    }
    return client


async def _no_live_rules():
    return set()


@pytest.mark.asyncio
async def test_group_settings_save_invokes_reconcile_for_edited_group(async_client):
    client = _mock_client()
    fake_reconcile = AsyncMock(return_value={"status": "reconciled", "channels_scoped": 2})

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake_reconcile):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
    fake_reconcile.assert_awaited_once()
    call = fake_reconcile.await_args
    assert call.args[2] == 1304  # (client, fresh_settings, group_id)


@pytest.mark.asyncio
async def test_reconcile_failure_does_not_fail_the_save(async_client):
    """A reconcile error must never fail the save the operator just made."""
    client = _mock_client()
    boom = AsyncMock(side_effect=RuntimeError("reconcile boom"))

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", boom):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_save_hook_isolates_per_group_failure(async_client):
    """Should-Fix 7: one edited group's reconcile raising must NOT abort the
    others — every edited group is attempted."""
    client = _mock_client()

    async def _reconcile(_client, _settings, gid, **_kw):
        if gid == 1304:
            raise RuntimeError("group 1304 blew up")
        return {"status": "reconciled", "group_id": gid, "channels_scoped": 1}

    fake = AsyncMock(side_effect=_reconcile)
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}},
                {"channel_group": 1305, "custom_properties": {"channel_profile_ids": [13]}},
            ]},
        )

    assert resp.status_code == 200
    # BOTH groups attempted despite the first raising.
    assert fake.await_count == 2
    attempted = {c.args[2] for c in fake.await_args_list}
    assert attempted == {1304, 1305}


@pytest.mark.asyncio
async def test_save_response_carries_profile_apply_summary(async_client):
    """#9: the 200 body surfaces the per-group reconcile outcome (status,
    failed_profile_ids, conflict) so the modal can warn on an incomplete
    apply."""
    client = _mock_client()
    outcome = {
        "status": "partial_failure", "group_id": 1304,
        "failed_profile_ids": [2], "conflict": True,
    }
    fake = AsyncMock(return_value=outcome)
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "ecm_profile_apply" in body
    assert body["ecm_profile_apply"] == [outcome]


@pytest.mark.asyncio
async def test_post_refresh_completion_hook_invokes_sweep():
    """Should-Fix 8: the post-refresh completion helper actually calls the
    selected-group sweep — a disconnected hook would silently apply nothing."""
    from routers.m3u import _reconcile_profiles_after_refresh

    fake_sweep = AsyncMock(return_value={"groups_reconciled": 1})
    client = AsyncMock()
    with patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        await _reconcile_profiles_after_refresh(client, "HD Homerun")

    fake_sweep.assert_awaited_once()
    assert fake_sweep.await_args.args[0] is client


@pytest.mark.asyncio
async def test_post_refresh_hook_swallows_sweep_failure():
    """A sweep failure inside the post-refresh hook is best-effort."""
    from routers.m3u import _reconcile_profiles_after_refresh

    boom = AsyncMock(side_effect=RuntimeError("sweep boom"))
    with patch("services.profile_reconcile.reconcile_all_selected_groups", boom):
        # Must not raise.
        await _reconcile_profiles_after_refresh(AsyncMock(), "HD Homerun")
