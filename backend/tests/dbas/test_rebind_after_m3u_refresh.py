"""An M3U refresh rebinds leftover restore placeholders — no second restore.

Bead ``enhancedchannelmanager-2o0cz`` residual. Measured identically on drill
runs 4, 5 and 7 against a STANDARD (redacted) artifact — the default backup, and
the one most operators hold::

    step                                       streams (with url)  bindings      playback
    1. straight after the restore              14 (0)              all placeholder  0/n, HTTP 500
    2. + re-enter the credential and refresh   110 (96)            STILL all placeholder  0/n, HTTP 500
    3. + re-run the WHOLE restore              96 (96)             all REAL         4/4, 200, 262144 B

Step 2 is the recovery an operator reaches for FIRST, and it changed nothing they
could see: the 96 real streams materialized and sat BESIDE the placeholders. The
cause was arithmetic — ``rebind_placeholder_streams`` had exactly ONE caller, a
restore-completion step that runs immediately after the restore's own deferred
refresh, and on a redacted artifact the M3U account has no credential at that
instant. Nothing re-ran the pass once the streams were real.

WHAT THESE TESTS PIN

The positive case is the headline: a channel bound to a synthetic placeholder is
rebound once real streams exist, WITHOUT re-running a restore, and the archived
slot ORDER survives. But the negative cases carry the safety envelope, and they
are the ones that would catch a widening of the predicate into "rebind any
URL-less stream", which would take an operator's own custom streams with it.

The ``…-ixdaw`` guarantees are re-asserted THROUGH THE NEW ENTRY POINT rather
than assumed: the shared refactor is only sound if the case-differing pair still
resolves to two DISTINCT ids and a genuine identical-name collision still costs
one slot instead of the whole channel on this path too.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream). Helpers come from the state-loss suite.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dbas.custom_stream_fallback import CUSTOM_STREAM_ACCOUNT_NAME
from dbas.placeholder_rebind import rebind_placeholders_after_refresh
from tests.dbas.test_restore_state_loss import _client

# The synthetic account a previous DBAS restore left behind. No ledger, no
# remap, no archive — this entry point runs long after the restore ended and has
# access to none of them.
SYNTHETIC_ACCOUNT_ID = 3
# A real provider account — the one the operator just re-credentialed.
PROVIDER_ACCOUNT_ID = 1
# An account the OPERATOR owns. Nothing under it may ever be touched.
OPERATOR_ACCOUNT_ID = 9


def _accounts(*extra: dict) -> list[dict]:
    """The destination's M3U account list, carrying the synthetic one."""
    return [
        {"id": PROVIDER_ACCOUNT_ID, "name": "Provider XC"},
        {"id": SYNTHETIC_ACCOUNT_ID, "name": CUSTOM_STREAM_ACCOUNT_NAME},
        *extra,
    ]


def _placeholder(stream_id: int, name: str, account: int = SYNTHETIC_ACCOUNT_ID) -> dict:
    """A URL-less placeholder, shaped exactly as ``custom_stream_fallback`` makes it.

    The ``name`` is the ARCHIVED stream's name verbatim — that is the whole
    enabling fact this entry point rests on, so the fixtures must not quietly
    invent a decorated placeholder name that the real code never produces.
    """
    return {"id": stream_id, "name": name, "url": None, "m3u_account": account}


def _real(stream_id: int, name: str, account: int = PROVIDER_ACCOUNT_ID) -> dict:
    """A real, URL-bearing provider stream the refresh just materialized."""
    return {
        "id": stream_id,
        "name": name,
        "url": "http://provider.example/%d" % stream_id,
        "m3u_account": account,
    }


def _wire(client, *, streams: list[dict], channels: list[dict]) -> None:
    """Point the mock at one destination state.

    ``get_streams`` is called both account-scoped (the cheap gate) and unscoped
    (the candidate fetch); the pass filters by account and url itself, so one
    return value serves both and the account filter is exercised for real.
    """
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {"results": streams}
    client.get_channels.return_value = {"results": channels}


# ---------------------------------------------------------------------------
# 1. THE DEFECT — step 2 is now sufficient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_channel_on_a_placeholder_is_rebound_once_real_streams_exist():
    """THE measured defect: no restore is re-run, and the channel is rebound.

    This is drill step 2 exactly — the operator re-entered the credential and
    refreshed, and the real provider streams materialized beside the placeholder.
    Before this entry point existed, nothing rebound and the channel kept
    answering HTTP 500.
    """
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(500, "US: FOX News Channel FHD"),
            _real(900, "US: FOX News Channel FHD"),
        ],
        channels=[{"id": 201, "name": "FOX News", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(
        client=client, trigger="test",
    )

    client.update_channel.assert_awaited_once_with(201, {"streams": [900]})
    assert result.rebound == 1
    assert result.channels_updated == 1


@pytest.mark.asyncio
async def test_the_archived_slot_order_survives_the_rebind():
    """ORDER is load-bearing and runs 5–7 verified it. Rebinding must not reorder.

    The placeholders are deliberately given DESCENDING destination ids and the
    real streams ASCENDING ones, so any implementation that sorts, rebuilds or
    re-derives the list instead of rewriting each slot IN PLACE produces a
    different list and fails here.
    """
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(503, "Third"),
            _placeholder(502, "Second"),
            _placeholder(501, "First"),
            _real(901, "First"),
            _real(902, "Second"),
            _real(903, "Third"),
        ],
        channels=[{"id": 201, "name": "Ordered", "streams": [501, 502, 503]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_awaited_once_with(201, {"streams": [901, 902, 903]})
    assert result.rebound == 3


@pytest.mark.asyncio
async def test_a_freed_placeholder_is_swept_and_the_empty_account_dropped():
    """The rebind's own residue is cleaned by the SAME pair the restore uses.

    Once every channel is on real streams the placeholder is referenced by
    nothing, so the shared sweep removes it and the shared drop removes the
    now-empty synthetic account. That is what stops a redacted cycle accruing
    dead rows (bead ``…-dgnms``), reused here rather than re-derived.
    """
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(500, "US: FOX News Channel FHD"),
            _real(900, "US: FOX News Channel FHD"),
        ],
        channels=[{"id": 201, "name": "FOX News", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.delete_stream.assert_awaited_once_with(500)
    assert result.orphans_swept == 1
    client.delete_m3u_account.assert_awaited_once_with(SYNTHETIC_ACCOUNT_ID)
    assert result.account_deleted is True


# ---------------------------------------------------------------------------
# 2. THE SAFETY ENVELOPE — what must never be touched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operators_url_less_stream_on_another_account_is_not_rebound():
    """The account test is the envelope — do not widen it.

    An operator may legitimately keep their own URL-less custom streams on their
    own account. "URL-less" alone would rebind every one of them onto whatever
    provider stream happens to share a name, silently rewriting the operator's
    lineup. The channel below holds one and must come out untouched.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts(
        {"id": OPERATOR_ACCOUNT_ID, "name": "My Custom Streams"}
    )
    client.get_streams.return_value = {
        "results": [
            _placeholder(500, "Restore Leftover"),
            _placeholder(800, "Operator's Own", account=OPERATOR_ACCOUNT_ID),
            _real(900, "Operator's Own"),
            _real(901, "Restore Leftover"),
        ]
    }
    client.get_channels.return_value = {
        "results": [
            {"id": 201, "name": "Operator Channel", "streams": [800]},
            {"id": 202, "name": "Restored Channel", "streams": [500]},
        ]
    }

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    # ONLY the synthetic-account placeholder moved.
    client.update_channel.assert_awaited_once_with(202, {"streams": [901]})
    assert result.rebound == 1
    # ...and the operator's own stream is not deleted either.
    client.delete_stream.assert_awaited_once_with(500)


@pytest.mark.asyncio
async def test_a_channel_already_on_real_streams_is_untouched():
    """No placeholder on the channel, no PATCH. The pass only ever adds safety."""
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(500, "Stranded Elsewhere"),
            _real(900, "US: FOX News Channel FHD"),
            _real(901, "Stranded Elsewhere"),
        ],
        channels=[{"id": 201, "name": "Healthy", "streams": [900]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_not_awaited()
    assert result.rebound == 0
    assert result.channels_updated == 0


# ---------------------------------------------------------------------------
# 3. THE CHEAP NO-OP — M3U refresh is a hot, scheduled path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_synthetic_account_costs_one_call_and_does_nothing():
    """Gate 1: the steady state on any instance that has never restored.

    One ``get_m3u_accounts`` and out — no stream fetch, no channel fetch. This
    is the assertion that keeps the hook off the hot path's budget.
    """
    client = _client()
    client.get_m3u_accounts.return_value = [
        {"id": PROVIDER_ACCOUNT_ID, "name": "Provider XC"},
    ]

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.get_streams.assert_not_awaited()
    client.get_channels.assert_not_awaited()
    client.update_channel.assert_not_awaited()
    assert result.rebound == 0


@pytest.mark.asyncio
async def test_a_synthetic_account_with_no_placeholder_stops_before_the_channels():
    """Gate 2: the account survives (it holds a playable custom stream), but
    there is nothing URL-less under it, so no channel walk happens."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [_real(700, "Playable Custom", account=SYNTHETIC_ACCOUNT_ID)]
    }

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.get_channels.assert_not_awaited()
    client.delete_stream.assert_not_awaited()
    assert result.rebound == 0


@pytest.mark.asyncio
async def test_placeholders_are_kept_when_no_real_stream_has_materialized_yet():
    """Step 1 of the drill — the refresh brought nothing back.

    Cutting the bindings here would turn "cannot play" into "has no streams at
    all", which is strictly worse and unrecoverable without the archive.
    """
    client = _client()
    _wire(
        client,
        streams=[_placeholder(500, "US: FOX News Channel FHD")],
        channels=[{"id": 201, "name": "FOX News", "streams": [500]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_not_awaited()
    client.delete_stream.assert_not_awaited()
    client.delete_m3u_account.assert_not_awaited()
    assert result.rebound == 0


# ---------------------------------------------------------------------------
# 4. THE ``…-ixdaw`` GUARANTEES, RE-ASSERTED THROUGH THE NEW ENTRY POINT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_case_differing_pair_still_resolves_to_two_distinct_ids():
    """The run-3 outage shape, through the new path.

    ``TX | Dallas | PBS KERA`` and ``TX | DALLAS | PBS KERA`` differ ONLY in
    case, and the matcher's normalizer folds case — so without the RAW-NAME
    PREFERENCE both slots would claim the lower id, the PATCH would carry a
    duplicate, and Dispatcharr's ``unique_channel_stream`` constraint would
    reject the WHOLE channel. Two distinct ids, in archived order, is the pass.
    """
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(501, "TX | Dallas | PBS KERA"),
            _placeholder(502, "TX | DALLAS | PBS KERA"),
            _placeholder(503, "TX | Austin | PBS KLRU"),
            _real(101, "TX | Dallas | PBS KERA"),
            _real(102, "TX | DALLAS | PBS KERA"),
            _real(98, "TX | Austin | PBS KLRU"),
        ],
        channels=[{"id": 12, "name": "PBS", "streams": [501, 502, 503]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    client.update_channel.assert_awaited_once_with(12, {"streams": [101, 102, 98]})
    assert result.rebound == 3


@pytest.mark.asyncio
async def test_a_genuine_identical_name_collision_costs_one_slot_not_the_channel():
    """The ``claimed_ids`` BACKSTOP, through the new path.

    Two placeholders whose names are TRULY identical: no pure per-stream matcher
    can tell them apart, and only ONE real stream exists to take. The second
    slot must be demoted to a MISS and keep its placeholder, so the PATCH
    carries no duplicate — costing one slot instead of every slot, which is what
    the all-or-nothing PATCH would otherwise do.
    """
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(501, "US: DUPLICATE FHD"),
            _placeholder(502, "US: DUPLICATE FHD"),
            _placeholder(503, "US: UNIQUE FHD"),
            _real(101, "US: DUPLICATE FHD"),
            _real(102, "US: UNIQUE FHD"),
        ],
        channels=[{"id": 12, "name": "Collides", "streams": [501, 502, 503]}],
    )

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    # Slot 2 keeps its placeholder; slots 1 and 3 take their real streams; the
    # order is untouched.
    client.update_channel.assert_awaited_once_with(12, {"streams": [101, 502, 102]})
    assert result.rebound == 2
    # The channel PLAYS (it holds two real streams) but still holds a slot that
    # streams nothing, so it is reported in the wider population and NOT as
    # unplayable — the ``…-daziw`` distinction, preserved on this path.
    assert result.still_placeholder == ["Collides"]
    assert result.unplayable == []
    # The RETAINED placeholder (502) is load-bearing and must survive the sweep;
    # the two the rebind freed are residue and go. The account stays because a
    # stream remains under it — the emptiness test, unchanged.
    assert {call.args[0] for call in client.delete_stream.await_args_list} == {501, 503}
    client.delete_m3u_account.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. BEST-EFFORT — an upstream failure is logged and never raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_channel_patch_is_logged_and_never_raises():
    """The channel keeps its placeholders and the refresh carries on."""
    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(500, "US: FOX News Channel FHD"),
            _real(900, "US: FOX News Channel FHD"),
        ],
        channels=[{"id": 201, "name": "FOX News", "streams": [500]}],
    )
    client.update_channel.side_effect = RuntimeError("upstream 500")

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    assert result.rebound == 0
    assert result.channels_updated == 0
    # The placeholder is still bound, so it must NOT be swept out from under the
    # channel — that would turn a failed rebind into a lost binding.
    client.delete_stream.assert_not_awaited()
    assert result.unplayable == ["FOX News"]


@pytest.mark.asyncio
async def test_a_failing_stream_fetch_is_contained(caplog):
    """Any error inside the pass is swallowed — a refresh never fails over hygiene."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.side_effect = RuntimeError("upstream 503")

    result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    assert result.rebound == 0
    client.update_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_rebind_already_in_flight_is_skipped_not_duplicated():
    """The double-run guard: a restore's own rebind holds the lock.

    The archive-driven pass runs as a restore-completion step and is the
    authoritative one. If the scheduled M3U refresh lands while it is mid-flight,
    this entry point must stand down rather than fight it for the same channels.
    """
    from dbas import placeholder_rebind

    client = _client()
    _wire(
        client,
        streams=[
            _placeholder(500, "US: FOX News Channel FHD"),
            _real(900, "US: FOX News Channel FHD"),
        ],
        channels=[{"id": 201, "name": "FOX News", "streams": [500]}],
    )

    async with placeholder_rebind._REBIND_LOCK:
        result = await rebind_placeholders_after_refresh(client=client, trigger="test")

    assert result.rebound == 0
    client.get_streams.assert_not_awaited()
    client.update_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. THE WIRING — the pass is only a fix if a real refresh reaches it
# ---------------------------------------------------------------------------
#
# The seam these cross is the one the defect actually lived on: the pass itself
# was never broken, it simply had ONE caller. Asserting the pass in isolation
# would have proved nothing about whether an operator's refresh reaches it.


@pytest.mark.asyncio
async def test_the_refresh_completion_path_triggers_the_rebind():
    """``POST /api/m3u/refresh/{id}`` completing is the operator's step 2.

    This is the UI "Refresh" button and the MCP ``refresh_m3u`` tool — the exact
    route the drill measured three times as changing nothing visible.
    """
    from routers import m3u as m3u_router

    client = MagicMock()
    client.get_m3u_account = AsyncMock(
        return_value={"id": 1, "updated_at": "2026-08-05T12:00:00Z"}
    )

    with patch.object(m3u_router, "get_client", return_value=client), \
         patch.object(m3u_router, "get_cache", return_value=MagicMock()), \
         patch.object(m3u_router, "send_alert", new=AsyncMock()), \
         patch.object(m3u_router, "send_immediate_digest", new=AsyncMock()), \
         patch.object(m3u_router, "journal", MagicMock()), \
         patch.object(m3u_router, "_advance_refresh_watermark", MagicMock()), \
         patch.object(m3u_router, "_capture_m3u_changes_after_refresh", new=AsyncMock()), \
         patch.object(m3u_router, "_reconcile_profiles_after_refresh",
                      new=AsyncMock(return_value=None)), \
         patch.object(m3u_router.asyncio, "sleep", new=AsyncMock()), \
         patch(
             "dbas.placeholder_rebind.rebind_placeholders_after_refresh",
             new=AsyncMock(),
         ) as rebind:
        await m3u_router._poll_m3u_refresh_completion(1, "Provider XC", None)

    rebind.assert_awaited_once()
    assert rebind.await_args.kwargs["client"] is client


@pytest.mark.asyncio
async def test_the_scheduled_refresh_task_triggers_the_rebind_once_per_run():
    """The scheduled half — an instance heals on its own cadence.

    Deliberately ONCE per run, not once per account: the pass is instance-wide,
    so a per-account call would repeat identical work on every refresh sweep.
    """
    from tasks.m3u_refresh import M3URefreshTask

    accounts = [
        {"id": 1, "name": "Prov A", "is_active": True},
        {"id": 2, "name": "Prov B", "is_active": True},
    ]
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=accounts)
    client.get_channel_groups = AsyncMock(return_value=[])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.get_m3u_account = AsyncMock(side_effect=[
        {"id": 1, "updated_at": "2026-08-05T00:00:00Z", "channel_groups": []},
        {"id": 1, "updated_at": "2026-08-05T01:00:00Z", "channel_groups": []},
        {"id": 2, "updated_at": "2026-08-05T00:00:00Z", "channel_groups": []},
        {"id": 2, "updated_at": "2026-08-05T01:00:00Z", "channel_groups": []},
    ])

    task = M3URefreshTask()
    task.account_ids = []
    task.skip_inactive = True

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", new=AsyncMock()), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock()), \
         patch("tasks.m3u_refresh.get_settings", return_value=MagicMock(
             last_m3u_refresh_completed_at="")), \
         patch("tasks.m3u_refresh.save_settings"), \
         patch(
             "dbas.placeholder_rebind.rebind_placeholders_after_refresh",
             new=AsyncMock(),
         ) as rebind:
        result = await task.execute()

    assert result.success is True
    rebind.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_rebind_error_never_fails_the_refresh_task():
    """Best-effort at the CALL SITE too, not only inside the pass."""
    from tasks.m3u_refresh import M3URefreshTask

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 1, "name": "Prov", "is_active": True}]
    )
    client.get_channel_groups = AsyncMock(return_value=[])
    client.refresh_m3u_account = AsyncMock(return_value=None)
    client.get_m3u_account = AsyncMock(side_effect=[
        {"id": 1, "updated_at": "2026-08-05T00:00:00Z", "channel_groups": []},
        {"id": 1, "updated_at": "2026-08-05T01:00:00Z", "channel_groups": []},
    ])

    task = M3URefreshTask()
    task.account_ids = []
    task.skip_inactive = True

    with patch("tasks.m3u_refresh.get_client", return_value=client), \
         patch("tasks.m3u_refresh.capture_m3u_changes", new=AsyncMock()), \
         patch("tasks.m3u_refresh.POLL_INTERVAL_SECONDS", 0), \
         patch("tasks.m3u_refresh.asyncio.sleep", new=AsyncMock()), \
         patch("tasks.m3u_refresh.get_settings", return_value=MagicMock(
             last_m3u_refresh_completed_at="")), \
         patch("tasks.m3u_refresh.save_settings"), \
         patch(
             "dbas.placeholder_rebind.rebind_placeholders_after_refresh",
             new=AsyncMock(side_effect=RuntimeError("upstream 500")),
         ):
        result = await task.execute()

    assert result.success is True


@pytest.mark.asyncio
async def test_the_restores_own_rebind_is_not_reachable_from_a_refresh():
    """The STRUCTURAL half of the double-run guarantee, asserted in code.

    ``apply_deferred_auto_sync`` — the restore's deferred phase — triggers its
    refresh by calling ``DispatcharrClient.refresh_m3u_account`` DIRECTLY. It
    never goes through ECM's own ``POST /api/m3u/refresh/{id}`` route and never
    schedules the ``m3u_refresh`` task, so neither hook can fire during a
    restore and the orchestrator's own call stays the only rebind a restore
    performs. If that ever changes, this assertion is the tripwire.
    """
    import inspect

    from dbas.importers import m3u_accounts

    source = inspect.getsource(m3u_accounts)
    assert "client.refresh_m3u_account(" in source
    assert "_poll_m3u_refresh_completion" not in source
    assert "M3URefreshTask" not in source
