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

WHAT RUN 4 ADDED (bead ``enhancedchannelmanager-oebpv``)
-------------------------------------------------------
Both counters were BLIND on a repeat restore: the verdict only ran for a channel
holding a placeholder THIS run had synthesized, so a channel stranded by an
EARLIER restore scored 0/0 with an empty ``notes[]`` while returning HTTP 500 on
playback. Section 2b pins the widened population — and the two controls that
stop it over-triggering on a channel that still plays or is fully healthy.

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
        allow_fuzzy=True,
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
        allow_fuzzy=True,
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
        allow_fuzzy=True,
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
# 2b. THE oebpv CASE — the verdict is blind to WHO created the bad slot
#
# Drill run 4 (2026-08-05) ran the standard redacted recovery: restore ->
# credential re-entry + M3U refresh -> RE-RUN the restore. 11 of 12 channels
# played; ``KERA Dallas PBS`` answered HTTP 500 — and the run's report said
# ``channels_needing_stream_reattach: 0``, ``channels_with_no_playable_stream:
# 0``, ``stream_reattach_details: []``, ``notes: []``. Reproduced on the
# encrypted path too: a first restore named the channel, an immediate SECOND
# restore over that exact state reported 0 for both counters.
#
# Cause: the verdict was nested inside "did THIS run's placeholder survive?",
# and a channel stranded by an EARLIER run holds none of this run's placeholders.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_channel_stranded_by_an_earlier_run_is_still_counted_unplayable():
    """A repeat restore must SEE a channel the previous restore left stranded.

    Nothing on this channel belongs to THIS run's ledger — its URL-less slots
    were synthesized by an earlier restore — and it genuinely cannot play. The
    verdict is keyed on what the channel HOLDS, not on who created it.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            # A PRIOR run's placeholders — URL-less, and absent from this run's
            # ledger, so nothing here is ever rebound or deleted by this pass.
            {"id": 601, "name": "TX | Dallas | PBS KERA", "url": None, "m3u_account": 4},
            {"id": 602, "name": "TX | DALLAS | PBS KERA", "url": None, "m3u_account": 4},
            # This run DID synthesize one placeholder, for a different channel.
            {"id": 500, "name": "Other Placeholder", "url": None, "m3u_account": 3},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 12, "name": "KERA Dallas PBS", "streams": [601, 602]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    ledger.record_created(EntityType.STREAM, 500, "Other Placeholder")
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 12)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {
                "id": 101,
                "name": "KERA Dallas PBS",
                "streams": [
                    {"id": 7, "name": "TX | Dallas | PBS KERA"},
                    {"id": 8, "name": "TX | DALLAS | PBS KERA"},
                ],
            }
        ],
    )

    assert report.channels_needing_stream_reattach == 1
    assert report.channels_with_no_playable_stream == 1
    detail = report.stream_reattach_details[0]
    assert detail.name == "KERA Dallas PBS"
    assert detail.channel_id == 12
    assert detail.has_playable_stream is False
    # NAMED from the destination's own stream list — the prior run's placeholders
    # are not in this run's ledger, so the old lookup would have said "<unknown>".
    assert detail.placeholder_streams == [
        "TX | DALLAS | PBS KERA",
        "TX | Dallas | PBS KERA",
    ]
    # And the operator is told, in the report notes, not only in the logs.
    assert any("NO playable stream" in note for note in report.notes)
    # A prior run's placeholder is NEVER deleted — it is not in this ledger.
    assert 601 not in {c.args[0] for c in client.delete_stream.await_args_list}
    # The downgrade fires: this restore did not leave the instance playable.
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.COMPLETED_WITH_FAILURES


@pytest.mark.asyncio
async def test_a_stranded_channel_is_seen_even_when_this_run_made_no_placeholder():
    """The pass must not short-circuit when its own ledger holds no placeholder.

    The repeat restore that produced the run-4 evidence matched every archived
    stream first time, so it synthesized nothing — and the pass returned before
    it could look at the channel that was already broken.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 601, "name": "TX | Dallas | PBS KERA", "url": None, "m3u_account": 4},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 12, "name": "KERA Dallas PBS", "streams": [601]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")  # EMPTY — this run synthesized nothing
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 12)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "KERA Dallas PBS",
             "streams": [{"id": 7, "name": "TX | Dallas | PBS KERA"}]}
        ],
    )

    assert report.channels_with_no_playable_stream == 1
    assert report.stream_reattach_details[0].name == "KERA Dallas PBS"
    # It looked, but it changed nothing — the slots are not this run's to touch.
    client.update_channel.assert_not_awaited()
    client.delete_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stranded_slot_beside_a_real_stream_still_reports_playable():
    """NO OVER-TRIGGER: one dead slot + one real stream is still a PLAYABLE channel.

    The widened verdict must not sweep the ``…-daziw`` population into the
    unplayable list — a channel that kept a URL-bearing stream plays, and the
    restore that produced it is still a success.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 601, "name": "Stale Placeholder", "url": None, "m3u_account": 4},
            {"id": 98, "name": "TX | Austin | PBS KLRU",
             "url": "http://p/live/klru", "m3u_account": 1},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 12, "name": "KERA Dallas PBS", "streams": [601, 98]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 12)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "KERA Dallas PBS",
             "streams": [{"id": 7, "name": "Stale Placeholder"},
                         {"id": 8, "name": "TX | Austin | PBS KLRU"}]}
        ],
    )

    assert report.channels_needing_stream_reattach == 1
    assert report.stream_reattach_details[0].has_playable_stream is True
    assert report.channels_with_no_playable_stream == 0
    assert compute_outcome(
        report=report, failure_occurred=False, rollback=None
    ) == RestoreOutcome.SUCCESS


@pytest.mark.asyncio
async def test_a_fully_healthy_channel_is_not_reported_at_all():
    """Every slot a real URL-bearing stream -> nothing to report about it.

    The widened verdict reports only a channel that genuinely holds a slot that
    streams nothing; it must not start naming healthy channels.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 101, "name": "TX | Dallas | PBS KERA",
             "url": "http://p/live/kera", "m3u_account": 1},
            {"id": 98, "name": "TX | Austin | PBS KLRU",
             "url": "http://p/live/klru", "m3u_account": 1},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 12, "name": "KERA Dallas PBS", "streams": [101, 98]}]
    }

    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="t")
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 101, 12)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "KERA Dallas PBS",
             "streams": [{"id": 7, "name": "TX | Dallas | PBS KERA"},
                         {"id": 8, "name": "TX | Austin | PBS KLRU"}]}
        ],
    )

    assert report.channels_needing_stream_reattach == 0
    assert report.channels_with_no_playable_stream == 0
    assert report.stream_reattach_details == []
    assert report.notes == []
    client.update_channel.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2c. THE kcfru CASE — the verdict may not depend on WHICH RUN made the stream
#
# Cross-instance sync runs unattended on a schedule, so the same destination
# state is judged over and over. Measured on the two-instance xdmru validation
# (2026-08-20, ECM 0.18.1-0127, Dispatcharr 0.28.2), sync-target "XDMRU B":
#
#   run 9  (01:17:53) created 18 entities -> channels_with_no_playable_stream: 6
#   run 11 (01:19:56) same 6 channels, same 6 streams -> 0
#   run 13 (01:21:04) same -> 0
#   run 21 (01:54:02) same -> 0
#   run 22 (01:54:17) same -> 0
#
# NOTHING about B changed between run 9 and run 11: its six channels were bound
# to the same six stream ids throughout, all under the synthetic
# "ECM Custom Streams (DBAS restore)" account, and every one of them carries the
# SOURCE's own url verbatim -- ``custom_stream_fallback._build_stream_payload``
# forwards every archived key except ``id`` / ``pk`` / ``m3u_account``, so a
# synthesized stream built from a FULL-fidelity archive is URL-bearing and
# streams exactly what A streams. Verified by fetching, not inferred:
#
#   A /proxy/ts/stream/1e946091-... -> HTTP 200, 188 bytes
#   B /proxy/ts/stream/bb66da76-... -> HTTP 200, 188 bytes
#
# So B plays, run 11+ told the truth, and run 9's "6" was the FALSE reading.
#
# CAUSE: one set was doing two jobs. ``candidates`` is the match-TARGET set, and
# it excludes this run's ledgered ids for a real reason -- a slot must never be
# rebound onto a placeholder. The playability verdict then read ``candidate_ids``
# off that same set, so "this run created it" silently meant "it cannot play".
# Detection is now taken from ``playable_ids``: every URL-bearing destination
# stream, whoever made it. Write authority is untouched and still ledger-scoped.
#
# THE INVARIANT, which is why both tests below run the pass TWICE: the verdict is
# a function of the DESTINATION'S STATE alone. Two cycles over an unchanged
# destination must return the same answer, whichever of them created the stream.
# A single-apply test cannot see this defect -- it is what let it through.
# ---------------------------------------------------------------------------


def _xdmru_destination(*, stream_url: str | None):
    """B exactly as the xdmru validation left it, for ONE sync cycle.

    One channel holding ONE stream under the synthetic custom-stream account,
    and nothing else on the destination: the sync path suppresses per-cycle
    provider auto-sync (ADR-013 S9), so B's own "XDMRU Provider" account
    ingested nothing and the synthesized stream is the only stream there. That
    is the real shape, measured -- B held exactly 6 streams, all on account 4.

    Args:
        stream_url: The url the synthesized stream carries. A full-fidelity
            archive hands the fallback the source stream's own url, so it is
            URL-BEARING and plays; a redacted archive strips it, so it is
            URL-less and does not.

    Returns:
        ``(client, archive_channels)``.
    """
    client = _client()
    client.get_streams.return_value = {
        "results": [
            {"id": 6, "name": "XDMRU News One", "url": stream_url, "m3u_account": 4},
        ]
    }
    client.get_channels.return_value = {
        "results": [{"id": 6, "name": "XDMRU News One", "streams": [6]}]
    }
    archive_channels = [
        {"id": 1, "name": "XDMRU News One",
         "streams": [{"id": 1, "name": "XDMRU News One", "url": stream_url,
                      "m3u_account": 2}]}
    ]
    return client, archive_channels


async def _one_cycle(*, stream_url: str | None, is_creating_cycle: bool):
    """Run the pass over :func:`_xdmru_destination` once; return the report.

    Args:
        stream_url: Forwarded to :func:`_xdmru_destination`.
        is_creating_cycle: ``True`` models the cycle that SYNTHESIZED the stream
            -- its id is in this run's ledger and in the STREAM remap. ``False``
            models every later cycle: the channel already exists, the matcher
            resolves its archived stream onto the stream that is already there,
            nothing is synthesized, and the ledger is EMPTY.

    Returns:
        ``(report, client)`` -- the client so a caller can assert on what the
        cycle WROTE as well as on what it reported.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client, archive_channels = _xdmru_destination(stream_url=stream_url)
    report = RestoreReport(is_dry_run=False)
    ledger = RollbackLedger(restore_id="cycle")
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL, 1, 6)
    if is_creating_cycle:
        ledger.record_created(EntityType.STREAM, 6, "XDMRU News One")
        ledger.record_created(
            EntityType.M3U_ACCOUNT, 4, "ECM Custom Streams (DBAS restore)"
        )
        remap.add(EntityType.STREAM, 1, 6)

    await rebind_placeholder_streams(
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=archive_channels,
    )
    return report, client


@pytest.mark.asyncio
async def test_a_url_bearing_synthesized_stream_reads_the_same_on_every_cycle():
    """The xdmru red proof: run 9 said 6 unplayable, runs 11-22 said 0.

    Same destination, same stream ids, same urls -- the only difference is which
    run's ledger holds them. B answered HTTP 200 on playback throughout, so the
    honest answer is PLAYABLE on BOTH cycles, and the outcome is SUCCESS on both.

    Before the fix this test fails on the FIRST cycle, which is the one that
    over-reported. The steady-state cycle was already right here; asserting only
    on it would have proved nothing.
    """
    creating, creating_client = await _one_cycle(
        stream_url="http://provider-xdmru/stream/news-one.ts", is_creating_cycle=True
    )
    steady, _ = await _one_cycle(
        stream_url="http://provider-xdmru/stream/news-one.ts", is_creating_cycle=False
    )

    assert creating.channels_with_no_playable_stream == 0
    assert steady.channels_with_no_playable_stream == 0
    # THE INVARIANT, stated as the equality it is.
    assert (
        creating.channels_with_no_playable_stream
        == steady.channels_with_no_playable_stream
    )
    assert (
        creating.channels_needing_stream_reattach
        == steady.channels_needing_stream_reattach
        == 0
    )
    for report in (creating, steady):
        assert compute_outcome(
            report=report, failure_occurred=False, rollback=None
        ) == RestoreOutcome.SUCCESS

    # WRITE AUTHORITY IS UNTOUCHED, and this is the half that must not move.
    # The tempting fix is to widen ``candidates`` -- let the pass see its own
    # placeholders -- and it is DESTRUCTIVE, not merely wrong: the stream would
    # match ITSELF, the slot would count as rebound, the channel would be PATCHed
    # for nothing, and it would then fall out of ``still_referenced`` and be
    # DELETED out from under the channel still bound to it. Nothing in the suite
    # caught that mutant before these two assertions.
    creating_client.update_channel.assert_not_awaited()
    creating_client.delete_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_url_less_placeholder_downgrades_the_outcome_on_every_cycle():
    """The other half of the invariant, and the one the operator is paged on.

    A redacted archive strips the stream url, so the synthesized stream really
    does stream nothing. That channel cannot play on the cycle that created it
    OR on any cycle after it, and BOTH must report
    ``COMPLETED_WITH_FAILURES`` -- an unattended schedule must not go green over
    a replica that has stopped playing. Steady state is part of the property,
    not an extension of it.
    """
    creating, _ = await _one_cycle(stream_url=None, is_creating_cycle=True)
    steady, _ = await _one_cycle(stream_url=None, is_creating_cycle=False)

    for report in (creating, steady):
        assert report.channels_with_no_playable_stream == 1
        assert report.stream_reattach_details[0].name == "XDMRU News One"
        assert report.stream_reattach_details[0].has_playable_stream is False
        assert any("NO playable stream" in note for note in report.notes)
        assert compute_outcome(
            report=report, failure_occurred=False, rollback=None
        ) == RestoreOutcome.COMPLETED_WITH_FAILURES


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


@pytest.mark.asyncio
async def test_the_report_json_names_every_channel_the_note_points_at():
    """The note's "attach a real stream" instruction is backed by the JSON.

    Drill run 2026-08-08-run17 read `details.restore_report`, saw
    `channels_with_no_playable_stream` plus a free-text note telling the operator
    to fix "each named channel", and could not find the names — they looked like
    they lived only in the container log and the restore-complete modal. The
    names ARE in the report (`stream_reattach_details`, written by the same
    recorder as the counters), so the gap was the note not saying where.

    This pins BOTH halves against the serialized payload the task actually
    stores — `report.model_dump(mode="json")`, exactly what
    `tasks/dbas_restore.py` puts under `details.restore_report` — so a future
    change cannot drop the array while leaving the instruction behind.
    """
    from dbas.placeholder_rebind import rebind_placeholder_streams

    client = _client()
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
        allow_fuzzy=True,
        client=client,
        report=report,
        ledger=ledger,
        remap=remap,
        archive_channels=[
            {"id": 101, "name": "Obscure",
             "streams": [{"id": 7, "name": "Obscure Channel"}]}
        ],
    )

    payload = report.model_dump(mode="json")

    assert payload["channels_with_no_playable_stream"] == 1
    named = [row["name"] for row in payload["stream_reattach_details"]]
    assert named == ["Obscure"]

    # The note fires, and it points at the field that holds the names rather
    # than promising "named channel" with no way to find them.
    note = next(n for n in payload["notes"] if "cannot play" in n)
    assert "stream_reattach_details" in note
