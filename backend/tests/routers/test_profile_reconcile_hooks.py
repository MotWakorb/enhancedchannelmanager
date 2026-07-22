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


def _account(aid, gid, selection):
    return {
        "id": aid,
        "name": f"Account {aid}",
        "channel_groups": [
            {
                "channel_group": gid,
                "enabled": True,
                "auto_channel_sync": True,
                "auto_sync_channel_start": None,
                "auto_sync_channel_end": None,
                "custom_properties": (
                    {"channel_profile_ids": selection} if selection is not None else {}
                ),
            }
        ],
    }


@pytest.mark.asyncio
async def test_enforced_global_propagates_selection_to_sibling_accounts(async_client):
    """PO decision (enforced global): saving group 1304's selection on account
    11 CASCADE-WRITES the same selection into sibling account 22's row for 1304,
    so no contradictory per-account rows persist."""
    client = _mock_client()
    # Two accounts share group 1304; account 22 currently has a DIFFERENT
    # selection that must be normalized to the just-saved [12].
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, [12]),
        _account(22, 1304, [99]),
    ]
    fake_reconcile = AsyncMock(return_value={"status": "reconciled", "group_id": 1304})

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
    # A PATCH was issued to the SIBLING account 22 carrying the saved selection.
    sibling_calls = [
        c for c in client.update_m3u_group_settings.await_args_list
        if c.args and c.args[0] == 22
    ]
    assert sibling_calls, "sibling account 22 should have been propagated to"
    payload = sibling_calls[0].args[1]
    row = payload["group_settings"][0]
    assert row["channel_group"] == 1304
    assert row["custom_properties"]["channel_profile_ids"] == [12]


@pytest.mark.asyncio
async def test_reconcile_setup_failure_surfaces_error_in_summary(async_client):
    """Blocker 3b: a reconcile SETUP failure (get_all_m3u_group_settings throws)
    before the per-group loop must emit an explicit error entry in
    ecm_profile_apply so an empty summary is never read as a clean success."""
    client = _mock_client()
    client.get_all_m3u_group_settings.side_effect = RuntimeError("dispatcharr down")

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
    summary = resp.json().get("ecm_profile_apply", [])
    assert any(o.get("status") == "error" for o in summary)


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
