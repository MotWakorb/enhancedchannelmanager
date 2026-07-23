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


def _sibling_calls(client, aid):
    return [
        c for c in client.update_m3u_group_settings.await_args_list
        if c.args and c.args[0] == aid
    ]


@pytest.mark.asyncio
async def test_enforced_global_propagates_genuine_selection_change(async_client):
    """PO decision (enforced global): a GENUINE selection change on account 11
    (prior none -> [12]) is CASCADE-WRITTEN into sibling account 22's row for
    1304, normalizing away 22's contradictory [99]."""
    client = _mock_client()
    # Prior primary state carries NO selection, so saving [12] is a real change.
    client.get_m3u_account.return_value = _account(11, 1304, None)
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, None),
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
    sibling_calls = _sibling_calls(client, 22)
    assert sibling_calls, "sibling account 22 should have been propagated to"
    row = sibling_calls[0].args[1]["group_settings"][0]
    assert row["channel_group"] == 1304
    assert row["custom_properties"]["channel_profile_ids"] == [12]


@pytest.mark.asyncio
async def test_enforced_global_field_only_edit_does_not_clear_sibling(async_client):
    """Blocker B1 (data loss): a field-only edit on account 11 (it has other
    custom props, NO profile selection — unchanged) must NOT clear sibling
    account 22's untouched [5]. No cascade PATCH may touch account 22."""
    client = _mock_client()
    # Primary 11 has custom_epg_id but NO selection, both before AND in the save
    # (a field-only edit); sibling 22 has a selection [5].
    prior_11 = {
        "id": 11, "name": "Account 11",
        "channel_groups": [{
            "channel_group": 1304, "enabled": True, "auto_channel_sync": True,
            "auto_sync_channel_start": None, "auto_sync_channel_end": None,
            "custom_properties": {"custom_epg_id": 9},
        }],
    }
    client.get_m3u_account.return_value = prior_11
    client.get_m3u_accounts.return_value = [prior_11, _account(22, 1304, [5])]
    fake_reconcile = AsyncMock(return_value={"status": "no_selection", "group_id": 1304})

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake_reconcile):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            # Field-only edit: bumps auto_sync_channel_start, carries the same
            # (no-selection) custom_properties — profiles untouched.
            json={"group_settings": [
                {"channel_group": 1304, "auto_sync_channel_start": 100,
                 "custom_properties": {"custom_epg_id": 9}}
            ]},
        )

    assert resp.status_code == 200
    # CRITICAL: no PATCH cascaded to the sibling — its [5] is untouched.
    assert _sibling_calls(client, 22) == []


@pytest.mark.asyncio
async def test_enforced_global_propagates_a_genuine_clear(async_client):
    """A genuine present->absent clear on the primary DOES clear siblings."""
    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])  # prior HAD [12]
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, None),
        _account(22, 1304, [12]),
    ]
    fake_reconcile = AsyncMock(return_value={"status": "no_selection", "group_id": 1304})

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake_reconcile):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            # Selection cleared: custom_properties present but no channel_profile_ids.
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {}}
            ]},
        )

    assert resp.status_code == 200
    sibling_calls = _sibling_calls(client, 22)
    assert sibling_calls, "sibling should be propagated the clear"
    row = sibling_calls[0].args[1]["group_settings"][0]
    assert "channel_profile_ids" not in (row["custom_properties"] or {})


@pytest.mark.asyncio
async def test_enforced_global_sync_groups_shape_does_not_alter_selection(async_client):
    """Blocker B1 / Sync Groups: a save whose rows OMIT custom_properties (the
    M3UManagerTab union save) leaves the merged selection == prior, so nothing
    is cascaded — no sibling selection is rewritten."""
    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])  # prior has [12]
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, [12]),
        _account(22, 1304, [12]),
    ]
    fake_reconcile = AsyncMock(return_value={"status": "reconciled", "group_id": 1304})

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules), \
         patch("services.profile_reconcile.reconcile_group_profiles", fake_reconcile):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            # Sync-Groups shape: only enabled/auto_channel_sync/start, NO custom_properties.
            json={"group_settings": [
                {"channel_group": 1304, "enabled": True, "auto_channel_sync": True,
                 "auto_sync_channel_start": None}
            ]},
        )

    assert resp.status_code == 200
    assert _sibling_calls(client, 22) == []


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


class _E2EClient:
    """Stateful client for the end-to-end profile-ID contract test: drives the
    REAL reconcile (no mocking) so a UI-shaped selection flows PATCH ->
    validation/coercion -> propagation -> reconcile -> bulk profile write."""

    def __init__(self, stored_selection):
        # ``stored_selection`` is what get_all_m3u_group_settings reports for the
        # group (may be legacy strings to exercise coercion).
        self._stored_selection = stored_selection
        self.bulk_calls = []
        self.group_settings_writes = []

    async def get_m3u_account(self, account_id):
        # PRIOR state has NO selection so the save is a genuine change.
        return {"id": account_id, "name": "A", "channel_groups": [
            {"channel_group": 1304, "enabled": True, "auto_channel_sync": True,
             "auto_sync_channel_start": None, "auto_sync_channel_end": None,
             "custom_properties": {}}]}

    async def get_channel_groups(self):
        return [{"id": 1304, "name": "HD Homerun"}]

    async def get_m3u_accounts(self):
        return [{"id": 11, "channel_groups": [
            {"channel_group": 1304, "custom_properties": {}}]}]

    async def update_m3u_group_settings(self, account_id, data):
        self.group_settings_writes.append((account_id, data))
        return {"message": "ok"}

    async def get_all_m3u_group_settings(self):
        return {1304: {"auto_channel_sync": True,
                       "custom_properties": {"channel_profile_ids": self._stored_selection}}}

    async def get_channels(self, page=1, page_size=100, search=None, channel_group=None):
        return {"count": 1, "next": None, "previous": None,
                "results": [{"id": 500, "channel_group": 1304, "custom_properties": {}}]}

    async def get_channel(self, channel_id):
        return {"id": channel_id, "custom_properties": {}}

    async def get_channel_profiles(self):
        return [{"id": 12, "name": "P12"}, {"id": 13, "name": "P13"}]

    async def bulk_update_profile_channels(self, profile_id, data):
        self.bulk_calls.append((profile_id, tuple(data["channel_ids"]), data["enabled"]))
        return {"ok": True}


async def _no_live_rules_impl():
    return set()


@pytest.mark.asyncio
async def test_e2e_ui_integer_selection_enables_profile(async_client):
    """Blocker 1 E2E CONTRACT: a UI-shaped integer selection [12] flows through
    PATCH -> propagation -> reconcile and the channel ends ENABLED in profile
    12."""
    client = _E2EClient(stored_selection=[12])
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules_impl):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )
    assert resp.status_code == 200
    # Channel 500 enabled in the selected profile 12 (and disabled in 13).
    assert (12, (500,), True) in client.bulk_calls
    assert (13, (500,), False) in client.bulk_calls


@pytest.mark.asyncio
async def test_e2e_legacy_string_selection_coerced_and_enabled(async_client):
    """Blocker 1: a LEGACY string-typed stored selection ("12") is coerced to
    int and still enables profile 12 — the exact case the pre-fix int-only drop
    silently no-op'd (this asserts the fix, and fails against that old code)."""
    client = _E2EClient(stored_selection=["12"])  # legacy strings in storage
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules_impl):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )
    assert resp.status_code == 200
    assert (12, (500,), True) in client.bulk_calls  # coerced "12" -> 12, applied


@pytest.mark.asyncio
async def test_non_integer_selection_rejected_422(async_client):
    """Blocker 1: a non-integer channel_profile_id is REJECTED with 422 (never
    silently dropped, which would read as a clear)."""
    client = _mock_client()
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": ["not-a-number"]}}
            ]},
        )
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_id", ["--5", "➂", "٣", "", "x"])
@pytest.mark.asyncio
async def test_garbage_profile_id_rejected_422_not_500(async_client, bad_id):
    """Finding 1: a garbage channel_profile_id is 422 at the boundary (NOT a 500
    from int() raising on '--5'/'➂')."""
    client = _mock_client()
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [bad_id]}}
            ]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_numeric_string_selection_normalized_to_int_in_payload(async_client):
    """A numeric-string id is coerced to int in the forwarded Dispatcharr payload
    (boundary normalization)."""
    client = _E2EClient(stored_selection=[12])
    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules_impl):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": ["12"]}}
            ]},
        )
    assert resp.status_code == 200
    # The PRIMARY forwarded row carries INT 12, not the string "12".
    primary = [w for w in client.group_settings_writes if w[0] == 11][0]
    row = primary[1]["group_settings"][0]
    assert row["custom_properties"]["channel_profile_ids"] == [12]


@pytest.mark.asyncio
async def test_save_fails_closed_on_lock_key_fetch_failure_for_selection_change(async_client):
    """Finding D: a SELECTION CHANGE saved while the lock-key group-settings
    fetch fails must FAIL CLOSED — NO unlocked primary write — and surface an
    error entry naming the recovery action."""
    client = _mock_client()
    # Prior state has NO selection so saving [12] is a genuine change.
    client.get_m3u_account.return_value = _account(11, 1304, None)
    client.get_all_m3u_group_settings.side_effect = RuntimeError("settings down")

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal") as mock_journal, \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    # Round-9 B1: a fail-closed selection change must NOT read as success — it
    # returns a retryable 503, not a 200.
    assert resp.status_code == 503
    # No unlocked primary write was performed.
    client.update_m3u_group_settings.assert_not_called()
    # No PHANTOM journal entry for a change that was never applied.
    mock_journal.log_entry.assert_not_called()
    # The 503 body carries the recovery hint.
    assert "NOT saved" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_save_field_only_edit_still_saves_when_lock_keys_unavailable(async_client):
    """Finding D corollary: a FIELD-ONLY edit (no selection change) still saves
    even when the lock-key fetch fails — fail-closed applies only to selection
    changes."""
    client = _mock_client()
    # Prior state 1304 has [12]; the save keeps [12] (no selection change) but
    # bumps a field.
    client.get_m3u_account.return_value = _account(11, 1304, [12])
    client.get_all_m3u_group_settings.side_effect = RuntimeError("settings down")

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal"), \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "auto_sync_channel_start": 50,
                 "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )

    assert resp.status_code == 200
    client.update_m3u_group_settings.assert_called()  # field-only save proceeded


@pytest.mark.asyncio
async def test_clear_aborts_before_primary_when_sibling_clear_fails(async_client):
    """DATA-INTEGRITY (DBA clear-ordering): clearing a selection where a SIBLING
    clear PATCH fails must ABORT BEFORE clearing the AUTHORITATIVE PRIMARY — so
    the primary keeps its selection (no resurrection) — and return a retryable
    503 naming the affected account."""
    calls = []

    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])  # prior HAD [12]
    client.get_all_m3u_group_settings.return_value = {
        1304: {"custom_properties": {}},  # winner cleared
    }
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, None),
        _account(22, 1304, [12]),  # sibling still has [12]; its clear PATCH fails
    ]

    async def _update(aid, data):
        calls.append(aid)
        if aid == 22:
            raise RuntimeError("sibling 22 PATCH failed")
        return {"message": "ok"}
    client.update_m3u_group_settings.side_effect = _update

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal") as mock_journal, \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {}}  # clear
            ]},
        )

    assert resp.status_code == 503
    # The AUTHORITATIVE PRIMARY (account 11) was NOT cleared — no resurrection.
    assert 11 not in calls
    assert 22 in calls  # sibling was attempted first (and failed)
    # No phantom journal entry (NOTHING actually changed), recovery names 22.
    mock_journal.log_entry.assert_not_called()
    assert "22" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_clear_partial_reports_truthfully_and_journals_changed(async_client):
    """Finding 4 (TRUTHFUL partial CLEAR): when clearing across 3 accounts and
    sibling A clears but sibling B fails, the response must NOT claim 'nothing
    changed'. It must (1) 503, (2) NOT clear the authoritative primary, (3) name
    BOTH the cleared account and the failed account, and (4) JOURNAL the account
    that ACTUALLY changed (the operator's recovery breadcrumb)."""
    calls = []

    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])  # prior HAD [12]
    client.get_all_m3u_group_settings.return_value = {1304: {"custom_properties": {}}}
    # 3 accounts: 11 primary, 22 clears OK, 33's clear PATCH fails.
    client.get_m3u_accounts.return_value = [
        _account(11, 1304, [12]),
        _account(22, 1304, [12]),
        _account(33, 1304, [12]),
    ]

    async def _update(aid, data):
        calls.append(aid)
        if aid == 33:
            raise RuntimeError("sibling 33 PATCH failed")
        return {"message": "ok"}
    client.update_m3u_group_settings.side_effect = _update

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal") as mock_journal, \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {}}  # clear
            ]},
        )

    assert resp.status_code == 503
    # Primary 11 NOT cleared (no resurrection); both siblings were attempted.
    assert 11 not in calls
    assert 22 in calls and 33 in calls
    detail = resp.json().get("detail", "")
    assert "22" in detail and "33" in detail  # names cleared AND failed
    # The sibling that ACTUALLY changed (22) is journaled — truthful, not silent.
    mock_journal.log_entry.assert_called_once()
    kwargs = mock_journal.log_entry.call_args.kwargs
    assert 22 in kwargs["after_value"]["cleared_siblings"]
    assert 33 not in kwargs["after_value"]["cleared_siblings"]


@pytest.mark.parametrize("bad", [None, {"not": "a list"}, "boom"])
@pytest.mark.asyncio
async def test_clear_fails_closed_on_malformed_account_list(async_client, bad):
    """Finding 5 (fail-closed enumeration): a CLEAR requires a VALID fresh account
    list read under the lock. A None / non-list / raised result must FAIL CLOSED —
    503, ZERO writes (primary NOT cleared), and NO journal entry — never clear the
    authoritative primary on an unverified/malformed account enumeration."""
    calls = []

    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])  # prior HAD [12]
    client.get_all_m3u_group_settings.return_value = {1304: {"custom_properties": {}}}
    if bad == "boom":
        client.get_m3u_accounts.side_effect = RuntimeError("account list unavailable")
    else:
        client.get_m3u_accounts.return_value = bad

    async def _update(aid, data):
        calls.append(aid)
        return {"message": "ok"}
    client.update_m3u_group_settings.side_effect = _update

    with patch("routers.m3u.get_client", return_value=client), \
         patch("routers.m3u.journal") as mock_journal, \
         patch("services.profile_reconcile._resolve_live_rule_ids", _no_live_rules):
        resp = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {}}  # clear
            ]},
        )

    assert resp.status_code == 503
    assert calls == []  # ZERO writes — primary NOT cleared, no sibling touched
    mock_journal.log_entry.assert_not_called()  # nothing changed -> no journal


@pytest.mark.asyncio
async def test_concurrent_opposing_saves_converge_no_divergent_interim():
    """Finding 3: two concurrent opposing enforced-global saves for the SAME
    group (account 1 -> [1], account 2 -> [2]) serialize under the effective-
    group lock (primary PATCH + cascade atomic), so after both, EVERY account
    row carries the SAME selection — no contradictory interim rows."""
    import asyncio
    import services.profile_reconcile as pr
    from routers.m3u import _apply_enforced_global_save

    pr._group_locks.clear()

    class _CascadeClient:
        def __init__(self):
            self.stored = {1: {100: [1]}, 2: {100: [2]}}
            self.delay = 0.01

        async def get_m3u_accounts(self):
            return [
                {"id": aid, "channel_groups": [
                    {"channel_group": 100, "enabled": True, "auto_channel_sync": True,
                     "custom_properties": {"channel_profile_ids": self.stored[aid][100]}}]}
                for aid in (1, 2)
            ]

        async def update_m3u_group_settings(self, aid, data):
            await asyncio.sleep(self.delay)  # widen the interleave window
            for row in data.get("group_settings", []):
                gid = row.get("channel_group")
                sel = (row.get("custom_properties") or {}).get("channel_profile_ids")
                self.stored.setdefault(aid, {})[gid] = sel
            return {"ok": True}

    client = _CascadeClient()
    settings = {100: {"auto_channel_sync": True, "custom_properties": {"channel_profile_ids": [1]}}}

    def _save(primary, sel):
        data = {"group_settings": [
            {"channel_group": 100, "enabled": True, "auto_channel_sync": True,
             "custom_properties": {"channel_profile_ids": sel}}]}
        edited = data["group_settings"]
        before = {100: {"custom_properties": {}}}  # prior none -> genuine change
        return _apply_enforced_global_save(client, primary, data, edited, before, settings)

    await asyncio.gather(_save(1, [1]), _save(2, [2]))

    pr._group_locks.clear()
    # Converged: both account rows carry the SAME selection (last-writer-wins),
    # never one [1] and the other [2].
    assert client.stored[1][100] == client.stored[2][100]


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
async def test_post_refresh_queued_does_not_announce_success():
    """B2&B3: when the post-refresh sweep COALESCES (queued), the helper returns
    a soft 'in progress' note (NOT None), so the caller does NOT announce an
    unqualified success."""
    from routers.m3u import _reconcile_profiles_after_refresh

    fake_sweep = AsyncMock(return_value={"status": "queued", "coalesced": True})
    with patch("services.profile_reconcile.reconcile_all_selected_groups", fake_sweep):
        msg = await _reconcile_profiles_after_refresh(AsyncMock(), "HD Homerun")

    assert msg is not None            # NOT a clean success (None)
    assert "already running" in msg   # soft in-progress note, no false success


@pytest.mark.asyncio
async def test_post_refresh_hook_swallows_sweep_failure():
    """A sweep failure inside the post-refresh hook is best-effort."""
    from routers.m3u import _reconcile_profiles_after_refresh

    boom = AsyncMock(side_effect=RuntimeError("sweep boom"))
    with patch("services.profile_reconcile.reconcile_all_selected_groups", boom):
        # Must not raise.
        await _reconcile_profiles_after_refresh(AsyncMock(), "HD Homerun")
