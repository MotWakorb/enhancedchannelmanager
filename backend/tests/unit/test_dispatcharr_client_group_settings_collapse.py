"""Blocker 3 (GH #720 Part B): get_all_m3u_group_settings collapses multiple
account rows for the same GLOBAL channel-group id DETERMINISTICALLY —
auto_channel_sync ON first, then LOWEST m3u_account_id — and flags a
cross-account channel_profile_ids CONFLICT so the reconcile can warn.
"""
import pytest
from unittest.mock import AsyncMock

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password="secret",
    )
    return DispatcharrClient(settings)


def _account(aid, *, auto_sync, selection):
    return {
        "id": aid,
        "name": f"Account {aid}",
        "channel_groups": [
            {
                "channel_group": 500,
                "auto_channel_sync": auto_sync,
                "custom_properties": (
                    {"channel_profile_ids": selection} if selection is not None else {}
                ),
            }
        ],
    }


def _fan_in_account(aid, gid, selection, *, target=665):
    return {
        "id": aid,
        "name": f"Account {aid}",
        "channel_groups": [{
            "channel_group": gid,
            "auto_channel_sync": True,
            "custom_properties": {
                "channel_profile_ids": selection,
                "group_override": target,
            },
        }],
    }


def _target_account(aid, gid, selection):
    return {
        "id": aid,
        "name": f"Account {aid}",
        "channel_groups": [{
            "channel_group": gid,
            "auto_channel_sync": True,
            "custom_properties": {"channel_profile_ids": selection},
        }],
    }


@pytest.mark.asyncio
async def test_prefetched_accounts_skip_the_internal_account_fetch():
    accounts = [_account(3, auto_sync=True, selection=[7])]
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(side_effect=AssertionError("must reuse accounts"))

    settings = await client.get_all_m3u_group_settings(accounts)

    assert settings[500]["m3u_account_id"] == 3
    client.get_m3u_accounts.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
async def test_collapse_is_deterministic_auto_sync_then_lowest_account(order):
    """Two accounts share group 500. The auto_channel_sync-ON row wins
    regardless of account ORDER; among equals the lowest account id wins."""
    a = _account(9, auto_sync=False, selection=[1])   # OFF, low id
    b = _account(3, auto_sync=True, selection=[2])     # ON, higher-precedence
    accounts = [a, b] if order == ("a", "b") else [b, a]
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=accounts)

    settings = await client.get_all_m3u_group_settings()

    winner = settings[500]
    # auto_channel_sync ON wins over OFF regardless of order -> account 3.
    assert winner["m3u_account_id"] == 3
    assert winner["custom_properties"]["channel_profile_ids"] == [2]


@pytest.mark.asyncio
async def test_lowest_account_id_breaks_auto_sync_tie():
    """Both rows auto_channel_sync ON -> the LOWEST account id wins."""
    a = _account(9, auto_sync=True, selection=[1])
    b = _account(3, auto_sync=True, selection=[2])
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["m3u_account_id"] == 3
    assert settings[500]["custom_properties"]["channel_profile_ids"] == [2]


@pytest.mark.asyncio
async def test_conflicting_selections_flagged():
    """Different non-empty selections across accounts -> conflict flag True."""
    a = _account(3, auto_sync=True, selection=[1])
    b = _account(9, auto_sync=True, selection=[2])
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["_ecm_channel_profile_conflict"] is True


@pytest.mark.asyncio
async def test_no_selection_winner_does_not_shadow_a_real_selection():
    """Should-Fix 3: within the same auto_channel_sync tier, a row WITH a
    selection outranks a lower-id row WITHOUT one — so the operator's real
    selection is not silently shadowed by a no-selection winner."""
    a = _account(3, auto_sync=True, selection=None)   # ON, no selection, low id
    b = _account(9, auto_sync=True, selection=[1, 2])  # ON, has selection
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    # The winner carries the real selection (account 9), not the empty account 3.
    assert settings[500]["m3u_account_id"] == 9
    assert settings[500]["custom_properties"]["channel_profile_ids"] == [1, 2]
    assert settings[500]["_ecm_channel_profile_conflict"] is False


@pytest.mark.asyncio
async def test_cross_tier_selection_shadow_is_flagged_as_conflict():
    """When the auto_channel_sync-ON winner carries NO selection but an OFF row
    DOES, precedence keeps the ON winner but the shadowed selection is flagged
    as a conflict so it is surfaced, not silently ignored (Should-Fix 3)."""
    a = _account(3, auto_sync=True, selection=None)    # ON wins per precedence
    b = _account(9, auto_sync=False, selection=[1, 2])  # OFF, has selection
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["m3u_account_id"] == 3       # ON tier wins
    assert settings[500]["_ecm_channel_profile_conflict"] is True


@pytest.mark.asyncio
async def test_matching_selections_not_flagged():
    """Identical selections across accounts are NOT a conflict."""
    a = _account(3, auto_sync=True, selection=[1, 2])
    b = _account(9, auto_sync=True, selection=[2, 1])  # same set, different order
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["_ecm_channel_profile_conflict"] is False


@pytest.mark.asyncio
async def test_legacy_string_selection_wins_has_selection_tiebreak():
    """Finding 2: a legacy STRING selection (["12"]) is coerced, so the row with
    it wins the has-selection tiebreak over a same-tier no-selection row (it did
    NOT before, when _row_selection dropped strings)."""
    a = _account(3, auto_sync=True, selection=None)      # ON, no selection, low id
    b = _account(9, auto_sync=True, selection=["12"])     # ON, legacy string sel
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["m3u_account_id"] == 9  # the row WITH the selection wins
    assert settings[500]["_ecm_channel_profile_conflict"] is False


@pytest.mark.asyncio
async def test_legacy_string_vs_int_selection_flags_conflict():
    """Finding 2: ["12"] vs [13] is a real conflict once the string is coerced."""
    a = _account(3, auto_sync=True, selection=["12"])
    b = _account(9, auto_sync=True, selection=[13])
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["_ecm_channel_profile_conflict"] is True


@pytest.mark.asyncio
async def test_legacy_string_equals_int_selection_not_flagged():
    """["12"] vs [12] are the SAME selection once coerced — no conflict."""
    a = _account(3, auto_sync=True, selection=["12"])
    b = _account(9, auto_sync=True, selection=[12])
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[a, b])

    settings = await client.get_all_m3u_group_settings()

    assert settings[500]["_ecm_channel_profile_conflict"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_distinct_source_groups_with_divergent_selections_flag_effective_bucket(reverse):
    accounts = [
        _fan_in_account(1, 823, [6, 7]),
        _fan_in_account(2, 825, [7, 6]),
        _fan_in_account(3, 2866, [14]),
    ]
    if reverse:
        accounts.reverse()
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=accounts)

    settings = await client.get_all_m3u_group_settings()

    for gid in (823, 825, 2866):
        assert settings[gid]["_ecm_channel_profile_conflict"] is True
    shape = settings[823]["_ecm_profile_conflict_shape"]
    assert shape["effective_group_id"] == 665
    assert {tuple(choice["profile_ids"]) for choice in shape["choices"]} == {(6, 7), (14,)}
    assert {
        source["source_group_id"]
        for choice in shape["choices"]
        for source in choice["sources"]
    } == {823, 825, 2866}


@pytest.mark.asyncio
async def test_distinct_source_groups_that_agree_do_not_flag_conflict():
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[
        _fan_in_account(1, 823, [6, 7]),
        _fan_in_account(2, 825, [7, 6]),
    ])

    settings = await client.get_all_m3u_group_settings()

    assert settings[823]["_ecm_channel_profile_conflict"] is False
    assert settings[825]["_ecm_channel_profile_conflict"] is False


@pytest.mark.asyncio
async def test_effective_targets_own_selection_outranks_redirected_sources():
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[
        _fan_in_account(1, 823, [6, 7]),
        _target_account(2, 665, [14]),
    ])

    settings = await client.get_all_m3u_group_settings()

    assert settings[665]["_ecm_channel_profile_conflict"] is False
    assert settings[823]["_ecm_channel_profile_conflict"] is False
    assert settings[665]["custom_properties"]["channel_profile_ids"] == [14]


@pytest.mark.asyncio
async def test_conflict_shape_keeps_every_participant_including_unset_sources():
    client = _make_client()
    client.get_m3u_accounts = AsyncMock(return_value=[
        _fan_in_account(1, 823, [6, 7]),
        _fan_in_account(2, 825, None),
        _fan_in_account(3, 2866, [14]),
    ])

    settings = await client.get_all_m3u_group_settings()

    shape = settings[823]["_ecm_profile_conflict_shape"]
    assert {tuple(choice["profile_ids"]) for choice in shape["choices"]} == {
        (), (6, 7), (14,),
    }
    assert {
        source["source_group_id"]
        for choice in shape["choices"]
        for source in choice["sources"]
    } == {823, 825, 2866}
