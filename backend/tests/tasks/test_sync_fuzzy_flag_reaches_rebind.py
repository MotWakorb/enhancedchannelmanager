"""A sync target's ``fuzzy_stream_matching`` must reach EVERY matcher pass.

Bead ``enhancedchannelmanager-efvyg`` (P1, silent-wrong-answer class). Spike
``xp6mp`` ruling 1b floors the SYNC stream matcher at Tier-3 exact-normalized,
and the per-``SyncTarget`` ``fuzzy_stream_matching`` flag (default OFF) is what
enforces it. ``_sync_channels_step`` threaded that flag into ``import_channels``
— but the post-create placeholder rebind that runs AFTERWARDS called
``rebind_placeholder_streams`` without it, so the rebind inherited the
signature default (``allow_fuzzy=True``) and re-ran the FULL 4-tier ladder no
matter what the target said.

Live reproduction (2026-08-19, two-instance disposable stack, flag OFF):
destination channel ``XDMRU News One`` was fuzzy-rebound onto the destination
stream named ``XDMRU News Two``, and the cycle reported SUCCESS. That is why the
assertions below are on the RESULTING BINDING — the stream ids dest-B is
actually left holding — and never on ``report.outcome``, ``skip_details`` or any
top-level ``RestoreReport`` counter: the broken code reports success and clean
counters while the replica plays the wrong content.

The INVARIANT under test is not "the rebind takes a kwarg". It is: every
stream-matching decision in a sync cycle honours that target's
``fuzzy_stream_matching``, at every stage, including passes that run after the
importers. The rebind is one example of it.

WHY THE DECOY ARRIVES LATE
--------------------------
Both tests seed dest-B's near-miss stream *during* the cycle, from the write
hook that fires when the channels importer synthesizes its placeholder — the
production sequencing the rebind exists for (the provider streams materialize
only after the import, which is why a second matcher pass is needed at all).
Seeding it up front instead makes the fuzzy-ON control pass for the wrong
reason: the IMPORTER matches it and the rebind is never asked. That was caught
by mutation-testing this file's own control (inverting the flag at the
orchestrator killed only one of the two tests), not by reasoning about it.

Conventions: ``docs/pytest_conventions.md``; no live upstream — dest-B is the
stateful two-instance harness (``tests/fixtures/sync_harness.py``).
"""
from __future__ import annotations

import pytest

from tests.fixtures.sync_harness import (
    StatefulDispatcharrFake,
    SyncHarness,
    make_sync_target,
)

# The live reproduction's two names. They share every token but the last, so the
# matcher's Tier-4 fuzzy rung scores them at 0.857 (well over the 0.60 floor)
# while Tiers 1-3 all MISS: different URLs, different normalized names. Any pass
# that reaches Tier 4 binds them together; any pass floored at Tier 3 does not.
_SOURCE_STREAM_NAME = "XDMRU News One"
_DECOY_STREAM_NAME = "XDMRU News Two"


def _source_with_one_channel() -> StatefulDispatcharrFake:
    """Source-A holding ONE channel whose only stream is ``XDMRU News One``."""
    source = StatefulDispatcharrFake.seeded_source()
    stream = source.streams.create(
        {
            "name": _SOURCE_STREAM_NAME,
            "url": "http://provider-a.test/news-one.m3u8",
            "m3u_account": 101,
        }
    )
    source.channels.create(
        {"name": _SOURCE_STREAM_NAME, "channel_number": 41, "streams": [stream["id"]]}
    )
    return source


def _dest_materializing_the_decoy_late() -> tuple[StatefulDispatcharrFake, dict]:
    """An empty dest-B that grows ``XDMRU News Two`` mid-cycle.

    The decoy appears the moment the channels importer creates its placeholder —
    i.e. AFTER the importer has finished matching and BEFORE the rebind runs, so
    the rebind is the only pass that can ever see it. Returns the (empty) dest
    and a one-key dict the tests read the decoy's id out of once it exists.
    """
    dest = StatefulDispatcharrFake.empty_dest()
    materialized: dict = {}

    def _materialize(method: str, _payload: object) -> None:
        if method != "create_stream" or materialized:
            return
        materialized["id"] = dest.streams.create(
            {
                "name": _DECOY_STREAM_NAME,
                "url": "http://provider-b.test/news-two.m3u8",
                "m3u_account": 5101,
            }
        )["id"]

    dest.inject_fault(_materialize)
    return dest, materialized


def _dest_channel_streams(dest: StatefulDispatcharrFake) -> list[int]:
    """The stream ids dest-B's replica of the source channel ACTUALLY holds."""
    rows = [c for c in dest.channels.list() if c.get("name") == _SOURCE_STREAM_NAME]
    assert rows, "dest-B never received the source channel — the fixture is wrong"
    return list(rows[0].get("streams") or [])


@pytest.mark.asyncio
async def test_fuzzy_off_leaves_the_replica_off_the_fuzzy_named_stream(tmp_path):
    """flag OFF -> the rebind must NOT bind the channel to ``XDMRU News Two``.

    THE RED PROOF for bead ``…-efvyg``. Before the fix this failed with the
    decoy's id present in the channel's stream list — the exact live symptom —
    while ``report.outcome`` was SUCCESS.
    """
    source = _source_with_one_channel()
    dest, decoy = _dest_materializing_the_decoy_late()

    harness = SyncHarness(
        source=source,
        dest=dest,
        target=make_sync_target(fuzzy_stream_matching=False),
    )
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert decoy, "the decoy never materialized — the fixture proved nothing"
    bound = _dest_channel_streams(dest)
    assert decoy["id"] not in bound, (
        "fuzzy_stream_matching is OFF, yet the replica was bound to %r "
        "(stream id %s) — a Tier-4 fuzzy match the target forbids"
        % (_DECOY_STREAM_NAME, decoy["id"])
    )
    # And the slot is not simply empty: it keeps the synthesized placeholder,
    # which is the ratified fallback when the matcher floors out (ruling 1b).
    assert bound, "the channel should retain its placeholder slot, not lose it"
    placeholder_names = {
        s["name"] for s in dest.streams.list() if s.get("id") in bound
    }
    assert placeholder_names == {_SOURCE_STREAM_NAME}


@pytest.mark.asyncio
async def test_fuzzy_on_still_lets_the_rebind_reach_the_fuzzy_rung(tmp_path):
    """flag ON -> the SAME cycle DOES rebind onto the decoy.

    The control. Without it the test above is satisfied by a rebind that never
    matches anything at all, which would be a different (and equally silent)
    defect. Together the pair pins the flag as the thing that decides, in the
    pass that runs AFTER the importers.
    """
    source = _source_with_one_channel()
    dest, decoy = _dest_materializing_the_decoy_late()

    harness = SyncHarness(
        source=source,
        dest=dest,
        target=make_sync_target(fuzzy_stream_matching=True),
    )
    await harness.run(confirm_apply=True, ledger_dir=tmp_path)

    assert decoy, "the decoy never materialized — the fixture proved nothing"
    assert decoy["id"] in _dest_channel_streams(dest), (
        "fuzzy_stream_matching is ON, so the rebind's Tier-4 rung must still be "
        "reachable — the fix must gate the rung, not remove it"
    )
