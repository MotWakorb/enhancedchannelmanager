"""Orphaned placeholders and an empty synthetic account do not survive a restore.

Bead ``enhancedchannelmanager-dgnms``, defects 2 and 3. Measured on drill runs
2026-08-05-run4 AND run5 (pre-existing, NOT caused by PR #781).

DEFECT 2 — RESIDUE. After the standard/redacted round trip (restore -> credential
re-entry + M3U refresh -> re-run restore) the destination held 14 URL-less
placeholder stream objects bound to NO channel::

    URL-less streams: 15   (14 residue + 1 created deliberately for another test)
      of which BOUND to a channel: []
      of which ORPHANED (unbound): 15
    channels holding a URL-less slot: NONE

Every channel played — this is hygiene, not an outage — but the instance accrued
dead rows on every redacted cycle. Cause: the deletion pass only deleted
placeholders in THIS run's ``RollbackLedger``. A placeholder from an EARLIER
restore, superseded when a later restore rebound its channel onto a real stream,
was never referenced again and never cleaned up.

DEFECT 3 — THE EMPTY ACCOUNT. Run 5 rebound and deleted every placeholder
(``14 placeholder(s) deleted``, ``0 channel(s) still hold a slot that cannot
play``) and the M3U account list still showed::

    ['custom', 'ECM Custom Streams (DBAS restore)', 'Infinity']

``_drop_synthetic_account`` iterated the ledger for an ``M3U_ACCOUNT`` entry and
no-oped when there was none, so an account created by an EARLIER restore and
merely REUSED by this one was never dropped, even once nothing was left under it.

WHAT THESE TESTS PIN, AND WHY THE NEGATIVE CASES MATTER MOST
------------------------------------------------------------
The fix relaxes ONE condition — who created the thing — and keeps every
condition that is about state. The three "NOT swept" tests below are the ones
that would catch a widening into "delete any URL-less stream", which would take
an operator's own custom streams with it.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream). Helpers come from the state-loss suite.
"""
from __future__ import annotations

import pytest

from dbas.custom_stream_fallback import CUSTOM_STREAM_ACCOUNT_NAME
from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
)
from tests.dbas.test_restore_state_loss import _client

# The synthetic account's destination id in these fixtures. It is deliberately
# NOT in any ledger below unless a test puts it there — "an earlier run created
# it" is the whole scenario.
SYNTHETIC_ACCOUNT_ID = 3
# A real provider account. Nothing on it is ever a sweep candidate.
PROVIDER_ACCOUNT_ID = 1
# An account the OPERATOR owns. The sweep must never touch anything under it.
OPERATOR_ACCOUNT_ID = 9


def _accounts(*extra: dict) -> list[dict]:
    """The destination's M3U account list, always carrying the synthetic one."""
    return [
        {"id": PROVIDER_ACCOUNT_ID, "name": "Provider XC"},
        {"id": SYNTHETIC_ACCOUNT_ID, "name": CUSTOM_STREAM_ACCOUNT_NAME},
        *extra,
    ]


async def _rebind(client, *, ledger=None, remap=None, archive_channels=None):
    """Run the pass with an EMPTY ledger by default — an earlier run's residue.

    An empty ``STREAM`` ledger is the exact shape of the measured defect: this
    run synthesized nothing, so the ledger-owned deletion has nothing to do and
    only the sweep can reach the residue.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    return await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=RestoreReport(is_dry_run=False),
        ledger=ledger if ledger is not None else RollbackLedger(restore_id="t"),
        remap=remap if remap is not None else IdRemapTable(),
        archive_channels=archive_channels or [],
    )


# ---------------------------------------------------------------------------
# 1. DEFECT 2 — the residue IS swept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_earlier_runs_orphaned_placeholder_is_swept():
    """URL-less + on the synthetic account + bound to nothing == residue.

    THE measured defect: this run's ledger does not own the stream, so before
    the sweep nothing ever deleted it.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Stranded By Run 3", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
            {"id": 900, "name": "Real Provider Stream", "url": "http://p/1",
             "m3u_account": PROVIDER_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Plays Fine", "streams": [900]}]
    }

    result = await _rebind(client)

    client.delete_stream.assert_awaited_once_with(700)
    assert result.orphans_swept == 1
    # It is NOT counted as one of this run's own deletions — different populations.
    assert result.placeholders_deleted == 0


@pytest.mark.asyncio
async def test_a_placeholder_freed_by_this_runs_rebind_is_not_double_deleted():
    """A stream the ledger-owned pass already removed is not swept again.

    Both passes see the same unreferenced, URL-less, synthetic-account stream.
    Only one delete may be issued, and it belongs to the ledger-owned pass.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 500, "name": "US: FOX News Channel FHD", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
            {"id": 900, "name": "US: FOX News Channel FHD", "url": "http://p/1",
             "m3u_account": PROVIDER_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "FOX News", "streams": [500]}]
    }

    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "US: FOX News Channel FHD")
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    result = await _rebind(
        client, ledger=ledger, remap=remap,
        archive_channels=[
            {"id": 101, "name": "FOX News",
             "streams": [{"id": 7, "name": "US: FOX News Channel FHD"}]}
        ],
    )

    client.delete_stream.assert_awaited_once_with(500)
    assert result.placeholders_deleted == 1
    assert result.orphans_swept == 0


@pytest.mark.asyncio
async def test_a_failed_sweep_delete_is_logged_and_never_raises():
    """Best-effort: a delete that errors must not fail the restore."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Stranded", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
            {"id": 701, "name": "Also Stranded", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {"results": []}
    client.delete_stream.side_effect = [RuntimeError("upstream 500"), None]

    result = await _rebind(client)

    # The first failed, the second still went — the loop continues.
    assert result.orphans_swept == 1
    assert client.delete_stream.await_count == 2


# ---------------------------------------------------------------------------
# 2. DEFECT 2 — what is deliberately NOT swept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operators_own_url_less_stream_on_another_account_is_not_swept():
    """The account test is what keeps this narrow — do not widen it.

    An operator may legitimately keep their own custom URL-less streams. This
    pass has no business classifying those, and "URL-less and unbound" alone
    would delete every one of them.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts(
        {"id": OPERATOR_ACCOUNT_ID, "name": "My Custom Streams"}
    )
    client.get_streams.return_value = {
        "results": [
            {"id": 800, "name": "Operator's Placeholder", "url": None,
             "m3u_account": OPERATOR_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {"results": []}

    result = await _rebind(client)

    client.delete_stream.assert_not_awaited()
    assert result.orphans_swept == 0


@pytest.mark.asyncio
async def test_a_placeholder_a_channel_still_holds_is_not_swept():
    """That binding is load-bearing — cutting it is the outage this pass prevents."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Still Bound", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Depends On It", "streams": [700]}]
    }

    result = await _rebind(client)

    client.delete_stream.assert_not_awaited()
    assert result.orphans_swept == 0


@pytest.mark.asyncio
async def test_a_url_bearing_stream_on_the_synthetic_account_is_not_swept():
    """A stream that can actually play is never deleted, wherever it lives."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Playable Custom", "url": "http://p/custom",
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {"results": []}

    result = await _rebind(client)

    client.delete_stream.assert_not_awaited()
    assert result.orphans_swept == 0


@pytest.mark.asyncio
async def test_nothing_is_swept_when_the_synthetic_account_cannot_be_identified():
    """No synthetic account, no sweep — never a guess."""
    client = _client()
    client.get_m3u_accounts.return_value = [
        {"id": PROVIDER_ACCOUNT_ID, "name": "Provider XC"},
    ]
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Unattributable", "url": None, "m3u_account": 42},
        ]
    }
    client.get_channels.return_value = {"results": []}

    result = await _rebind(client)

    client.delete_stream.assert_not_awaited()
    assert result.orphans_swept == 0


@pytest.mark.asyncio
async def test_an_unreadable_account_list_degrades_to_the_ledger_alone():
    """A failed account fetch must not raise, and must not widen the sweep."""
    client = _client()
    client.get_m3u_accounts.side_effect = RuntimeError("upstream 503")
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Stranded", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {"results": []}

    result = await _rebind(client)

    client.delete_stream.assert_not_awaited()
    assert result.orphans_swept == 0


# ---------------------------------------------------------------------------
# 3. DEFECT 3 — the synthetic account is dropped when EMPTY, whoever made it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_account_this_run_did_not_create_is_dropped_once_empty():
    """THE measured defect: the ledger holds no M3U_ACCOUNT entry, yet it goes.

    The account was created by an EARLIER restore and merely REUSED by this one.
    Who created an empty account has no bearing on whether it should exist.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Stranded By Run 3", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
            {"id": 900, "name": "Real", "url": "http://p/1",
             "m3u_account": PROVIDER_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Plays Fine", "streams": [900]}]
    }

    result = await _rebind(client)   # ledger is EMPTY

    assert result.orphans_swept == 1
    client.delete_m3u_account.assert_awaited_once_with(SYNTHETIC_ACCOUNT_ID)
    assert result.account_deleted is True


@pytest.mark.asyncio
async def test_an_account_with_a_stream_still_under_it_is_never_dropped():
    """THE safety property, unchanged: deleting it would cascade the stream away.

    The stream below is URL-less and on the synthetic account, but a channel
    still holds it — so it survives the sweep, and the account that owns it must
    survive with it.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Still Bound", "url": None,
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Depends On It", "streams": [700]}]
    }

    result = await _rebind(client)

    client.delete_m3u_account.assert_not_awaited()
    assert result.account_deleted is False


@pytest.mark.asyncio
async def test_an_account_holding_a_playable_custom_stream_is_never_dropped():
    """Emptiness is about STREAMS, not about bindings.

    An unbound but URL-BEARING stream keeps the account alive: the sweep will not
    delete it, so dropping the account would cascade it away.
    """
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {
        "results": [
            {"id": 700, "name": "Playable Custom", "url": "http://p/custom",
             "m3u_account": SYNTHETIC_ACCOUNT_ID},
        ]
    }
    client.get_channels.return_value = {"results": []}

    result = await _rebind(client)

    client.delete_m3u_account.assert_not_awaited()
    assert result.account_deleted is False


@pytest.mark.asyncio
async def test_a_failed_account_delete_is_reported_as_not_deleted():
    """Best-effort: an upstream error never raises out of the pass."""
    client = _client()
    client.get_m3u_accounts.return_value = _accounts()
    client.get_streams.return_value = {"results": []}
    client.get_channels.return_value = {"results": []}
    client.delete_m3u_account.side_effect = RuntimeError("upstream 500")

    result = await _rebind(client)

    assert result.account_deleted is False
