"""A restore that leaves a channel with NO playable stream is not a success.

Bead ``enhancedchannelmanager-daziw``. The drill (2026-08-05-run3) finished a
restore reporting ``outcome: success, failed: 0`` for an instance holding a
channel that answered HTTP 500 with 0 bytes on ``/proxy/ts/stream/``. The
restore's own contract already forbids that
(:class:`dbas.restore_contracts.RestoreOutcome`: "NEVER report SUCCESS on mixed
state"), so the outcome must be downgraded.

WHAT THE DOWNGRADE IS KEYED ON, AND WHY IT IS NOT THE OBVIOUS COUNTER
--------------------------------------------------------------------
``channels_needing_stream_reattach`` counts channels holding **at least one**
placeholder slot. Two populations collapse into it:

* **Genuinely unplayable** — every slot is a URL-less placeholder. Nothing on
  the channel can be streamed. THIS is the failure.
* **Playable** — the channel kept its real, URL-bearing streams and holds ONE
  leftover placeholder. That is the DESIGNED output of the ``…-ixdaw`` fix
  shipped in v0.18.1-0026 ("costs one slot instead of the entire channel"), and
  such a channel plays fine.

So the downgrade is keyed on the new ``channels_with_no_playable_stream``
aggregate, never on ``channels_needing_stream_reattach``. The second test below
is the one that pins that distinction: it reuses the EXACT ``…-ixdaw`` drill
fixture and asserts the outcome stays ``SUCCESS``.

Conventions: ``docs/pytest_conventions.md``; the Dispatcharr client is an
``AsyncMock`` (no live upstream).
"""
from __future__ import annotations

import pytest

from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import compute_outcome

# The ixdaw drill fixture is imported, never copied: this file's whole job is to
# prove THAT scenario still resolves to SUCCESS, so it must track the fixture the
# ixdaw tests pin rather than a snapshot of it that could silently drift.
from tests.dbas.test_restore_state_loss import _client, _kera_drill_fixture


# ---------------------------------------------------------------------------
# 1. Every slot a placeholder -> the channel cannot play
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_channel_whose_every_slot_is_a_placeholder_is_counted_unplayable():
    """No URL-bearing stream survives the rebind -> the channel CANNOT play.

    Drill run-3 evidence: this exact shape fetched HTTP 500 / 0 bytes while the
    restore reported ``success``.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    # Only the placeholder exists — the provider never materialized a match.
    client.get_streams.return_value = {
        "results": [{"id": 500, "name": "Obscure Channel", "url": None, "m3u_account": 3}]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Obscure", "streams": [500]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "Obscure Channel")
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "Obscure",
             "streams": [{"id": 7, "name": "Obscure Channel"}]}
        ],
    )

    assert report.channels_with_no_playable_stream == 1
    assert report.stream_reattach_details[0].has_playable_stream is False
    # And the aggregate downgrades a clean apply.
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_a_failed_patch_leaves_the_channel_unplayable_and_says_so():
    """When the PATCH errors the channel keeps ONLY placeholders — count it.

    The rebind's own error handler restores every slot to its placeholder, so
    the playability verdict must be taken from the state the channel is ACTUALLY
    left in, not from the list the pass hoped to write.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 900, "name": "Obscure Channel", "url": "http://p/obs", "m3u_account": 1},
            {"id": 500, "name": "Obscure Channel", "url": None, "m3u_account": 3},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 201, "name": "Obscure", "streams": [500]}]
    }
    client.update_channel.side_effect = RuntimeError("upstream 500")

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "Obscure Channel")
    remap = IdRemapTable()
    remap.add(EntityType.STREAM, 7, 500)
    remap.add(EntityType.CHANNEL, 101, 201)

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "Obscure",
             "streams": [{"id": 7, "name": "Obscure Channel"}]}
        ],
    )

    assert report.channels_needing_stream_reattach == 1
    assert report.channels_with_no_playable_stream == 1


# ---------------------------------------------------------------------------
# 2. THE ixdaw CASE — a leftover placeholder beside real streams still PLAYS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ixdaw_leftover_placeholder_case_stays_a_success():
    """``[real, placeholder, real]`` is PLAYABLE — it must not be downgraded.

    This is the whole reason the downgrade is keyed on
    ``channels_with_no_playable_stream`` and not on
    ``channels_needing_stream_reattach``. The ``…-ixdaw`` fix shipped in
    v0.18.1-0026 deliberately leaves ONE contested slot on its placeholder so
    the channel keeps the two slots that resolved — the channel plays, and the
    restore that produced it is a success.

    If this test ever fails, the change under it is false-failing a restore that
    left every channel playing.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client, report, ledger, remap, archive_channels = _kera_drill_fixture()

    await rebind_placeholder_streams(
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=archive_channels,
    )

    # The channel ends [101 (real), 501 (placeholder), 98 (real)].
    client.update_channel.assert_awaited_once_with(12, {"streams": [101, 501, 98]})
    # It holds a placeholder…
    assert report.channels_needing_stream_reattach == 1
    # …but it is NOT unplayable, and the detail row says which of the two it is.
    assert report.channels_with_no_playable_stream == 0
    assert report.stream_reattach_details[0].has_playable_stream is True
    # So the apply is still a SUCCESS.
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 3. Dry runs are predictions, not failures
# ---------------------------------------------------------------------------


def test_a_dry_run_is_never_downgraded_by_unplayable_channels():
    """A preview that predicts a shortfall is a prediction, not a failure."""
    report = RestoreReport(is_dry_run=True)
    report.record_stream_reattach_needed(
        name="Obscure", channel_id=201, placeholder_streams=["Obscure Channel"],
        has_playable_stream=False,
    )
    assert report.channels_with_no_playable_stream == 1
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.SUCCESS


# ---------------------------------------------------------------------------
# 4. Controls — nothing else about the outcome moves
# ---------------------------------------------------------------------------


def test_a_clean_restore_with_no_placeholders_is_still_success():
    report = RestoreReport(is_dry_run=False)
    assert report.channels_with_no_playable_stream == 0
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.SUCCESS


def test_a_rolled_back_run_with_unplayable_channels_still_reports_the_rollback():
    """The downgrade never masks a rollback state — those are strictly worse."""
    from dbas.restore_orchestrator import RollbackResult

    report = RestoreReport(is_dry_run=False)
    report.record_stream_reattach_needed(
        name="Obscure", placeholder_streams=["Obscure Channel"],
        has_playable_stream=False,
    )
    rolled_back = compute_outcome(
        report=report,
        failure_occurred=True,
        rollback=RollbackResult(complete=True, compensated=[], residue=[]),
    )
    assert rolled_back == RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK

    incomplete = compute_outcome(
        report=report,
        failure_occurred=True,
        rollback=RollbackResult(complete=False, compensated=[], residue=[]),
    )
    assert incomplete == RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE


def test_the_recorder_keeps_the_two_counters_independent():
    """One counter counts placeholders held; the other counts unplayability."""
    report = RestoreReport(is_dry_run=False)
    report.record_stream_reattach_needed(
        name="Plays anyway", placeholder_streams=["leftover"], has_playable_stream=True,
    )
    report.record_stream_reattach_needed(
        name="Cannot play", placeholder_streams=["only one"], has_playable_stream=False,
    )
    assert report.channels_needing_stream_reattach == 2
    assert report.channels_with_no_playable_stream == 1
